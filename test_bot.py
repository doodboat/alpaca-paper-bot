import copy
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from urllib.error import HTTPError
from paperbot.strategy import (Bar,GuardError,SYMBOLS,UTC,INTERVAL,completed_bars,entry_signal,exit_day,quantity,session_time)
from paperbot.engine import Engine,ledger
from paperbot.api import Alpaca,APIError,NoRedirect,PAPER
from paperbot.state import process_lock
from paperbot.demo import make_engine,clock_patch,FakeFeed


def raw(t,c=102,v=100):
    return {'t':t.isoformat(),'o':100,'h':103,'l':99,'c':c,'v':v,'vw':101}


class StrategyTests(unittest.TestCase):
    def setUp(self):self.open=datetime(2026,9,9,13,30,tzinfo=UTC);self.close=self.open+timedelta(hours=6,minutes=30)
    def test_incomplete_bar_is_not_used(self):
        rows=[raw(self.open,100,200),raw(self.open+INTERVAL),raw(self.open+2*INTERVAL,103)]
        now=self.open+2*INTERVAL+timedelta(seconds=10)
        bars=completed_bars(rows,self.open,self.close,now)
        self.assertEqual(len(bars),2)
        # Set original opening range below the later breakout.
        bars[0]=Bar(self.open,100,101,99,100,200,100)
        self.assertIsNotNone(entry_signal(bars,[100]*20,now,self.close))
        rows[-1]['c']=99999
        self.assertEqual(len(completed_bars(rows,self.open,self.close,now)),2)
    def test_missing_bar_blocks(self):
        with self.assertRaises(GuardError):completed_bars([raw(self.open+INTERVAL)],self.open,self.close,self.open+2*INTERVAL+timedelta(seconds=10))
    def test_no_catchup_no_noon_entry_and_volume_boundary(self):
        bars=[Bar(self.open,100,101,99,100,149,100),Bar(self.open+INTERVAL,100,103,100,102,100,101)]
        now=self.open+2*INTERVAL+timedelta(seconds=10)
        self.assertIsNone(entry_signal(bars,[100]*20,now,self.close))
        bars[0]=Bar(self.open,100,101,99,100,150,100)
        self.assertIsNotNone(entry_signal(bars,[100]*20,now,self.close))
        self.assertIsNone(entry_signal(bars,[100]*20,now+timedelta(minutes=2),self.close))
        bars[-1]=Bar(self.open+timedelta(hours=2,minutes=15),100,103,100,102,100,101)
        self.assertIsNone(entry_signal(bars,[100]*20,self.open+timedelta(hours=2,minutes=30,seconds=10),self.close))
    def test_calendar_weekend_and_early_close(self):
        cal=[{'date':d} for d in ('2026-09-11','2026-09-14','2026-09-15')]
        self.assertEqual(exit_day(cal,'2026-09-11'),'2026-09-15')
        self.assertEqual(session_time('2026-11-27','13:00').hour,18)
        self.assertEqual(session_time('2026-09-09','09:30').hour,13)
    def test_whole_share_budget(self):
        self.assertEqual(quantity(15000,102.11),146)
        self.assertLessEqual(quantity(45000,102.11)*102.11,45000)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.api,self.store,self.engine=make_engine(self.temp.name)
        self.patch=clock_patch(self.api);self.patch.start()
    def tearDown(self):self.patch.stop();self.store.close();self.temp.cleanup()
    def test_three_positions_budget_ranking_and_no_duplicates(self):
        self.engine.tick();self.engine.tick()
        self.assertEqual([r['symbol'] for r in self.api.submissions],['AAPL','AMD','AMZN'])
        cash,qty,reserved,_=ledger(self.engine.s)
        self.assertGreaterEqual(cash,0);self.assertEqual(len(qty),3)
        self.assertLessEqual(sum(int(r['qty'])*float(r['limit_price']) for r in self.api.submissions),45000)
    def test_restart_does_not_repeat_order(self):
        self.engine.tick()
        restart=Engine(self.api,self.store,True);restart.feed=self.engine.feed;restart.tick()
        self.assertEqual(len(self.api.submissions),3)
    def test_timeout_after_acceptance_recovers_without_resubmit(self):
        self.api.timeout='after'
        with self.assertRaises(APIError):self.engine.tick()
        self.api.timeout=None
        restart=Engine(self.api,self.store,True);restart.feed=self.engine.feed;restart.tick()
        cids=[r['client_order_id'] for r in self.api.submissions]
        self.assertEqual(len(cids),len(set(cids)))
        self.assertEqual(len(cids),3)
    def test_timeout_before_acceptance_blocks_without_retry(self):
        self.api.timeout='before'
        with self.assertRaises(APIError):self.engine.tick()
        self.api.timeout=None
        restart=Engine(self.api,self.store,True);restart.feed=self.engine.feed
        result=restart.tick();restart.tick()
        self.assertEqual(len(self.api.submissions),1)
        self.assertIn('Unresolved',result['message'])
    def test_partial_fills_are_accounted_as_filled_quantity(self):
        self.api.partial=5;self.engine.tick();self.engine.tick()
        cash,qty,reserved,pending=ledger(self.engine.s)
        self.assertEqual(cash,45000-15*102);self.assertTrue(all(q==5 for q in qty.values()))
        self.assertEqual(reserved,0);self.assertFalse(pending)
    def test_zero_fill_not_reentered_same_day(self):
        self.api.partial=0;self.engine.tick();before=len(self.api.submissions);self.engine.tick()
        self.assertEqual(len(self.api.submissions),before)
        self.assertEqual(ledger(self.engine.s)[0],45000)
    def test_manual_cash_or_positions_block_orders(self):
        self.api.cash+=5
        with self.assertRaisesRegex(GuardError,'Cash reconciliation'):self.engine.tick()
        self.assertFalse(self.api.submissions)
        self.api.cash-=5;self.api.holdings['TSLA']=1
        with self.assertRaisesRegex(GuardError,'Position reconciliation'):self.engine.tick()
    def test_manual_order_detected(self):
        self.api.records['manual']={'client_order_id':'manual','status':'filled'}
        with self.assertRaisesRegex(GuardError,'Unowned'):self.engine.tick()
    def test_changed_account_blocked(self):
        original=self.api.account
        self.api.account=lambda:dict(original(),id='different')
        with self.assertRaisesRegex(GuardError,'different paper account'):self.engine.tick()
    def test_no_orders_when_closed_or_quotes_stale(self):
        self.api.open=False;self.engine.tick();self.assertFalse(self.api.submissions)
        self.api.open=True;self.api.quote_age=120;self.engine.tick();self.assertFalse(self.api.submissions)
    def test_stale_bars_block_entries(self):
        self.engine.feed.stale=True;result=self.engine.tick()
        self.assertFalse(self.api.submissions);self.assertIn('stale',result['message'])
    def test_scheduled_exit_survives_stale_entry_data(self):
        self.engine.tick();self.api.now=datetime(2026,9,11,19,45,10,tzinfo=UTC)
        self.engine.feed.stale=True;self.engine.tick()
        self.assertEqual(len(self.api.submissions),6);self.assertFalse(any(self.api.holdings.values()))
    def test_range_failure_exits(self):
        self.engine.tick();self.api.now+=INTERVAL;self.engine.feed.range_fail=True
        self.engine.tick();self.assertFalse(any(self.api.holdings.values()))
    def test_pause_does_not_disable_exit(self):
        self.engine.tick();(self.store.directory/'PAUSE').touch()
        self.api.now+=INTERVAL;self.engine.feed.range_fail=True;self.engine.tick()
        self.assertFalse(any(self.api.holdings.values()))
    def test_observe_mode_never_submits(self):
        self.engine.enable_orders=False;self.engine.tick();self.engine.tick()
        self.assertFalse(self.api.submissions);self.assertFalse(self.engine.s['orders'])
    def test_missing_state_needs_explicit_init(self):
        self.engine.s=None
        with self.assertRaisesRegex(GuardError,'not initialized'):self.engine.tick()
    def test_process_lock_excludes_second_instance(self):
        with process_lock(self.temp.name):
            with self.assertRaises(GuardError):
                with process_lock(self.temp.name):pass
    def test_pending_entry_canceled_before_exit(self):
        self.api.partial=5;self.engine.tick()
        for o in self.api.records.values():o['status']='partially_filled'
        self.api.now+=INTERVAL;self.engine.feed.range_fail=True
        self.engine.tick()
        self.assertEqual(len(self.api.submissions),3)
        self.assertTrue(all(o['status']=='canceled' for o in self.api.records.values()))
        self.engine.tick()
        self.assertEqual(len(self.api.submissions),6)
        self.assertFalse(any(self.api.holdings.values()))
    def test_sip_denied_does_not_prevent_scheduled_exit(self):
        self.engine.tick();self.api.now=datetime(2026,9,11,19,45,10,tzinfo=UTC)
        self.engine.feed.stale=True
        with patch.object(self.api,'quotes',side_effect=APIError(403)):
            self.engine.tick()
        self.assertFalse(any(self.api.holdings.values()))
    def test_buy_fill_above_limit_is_rejected(self):
        self.engine.tick();s=copy.deepcopy(self.engine.s)
        next(iter(s['orders'].values()))['broker']['filled_avg_price']='9999'
        with self.assertRaisesRegex(GuardError,'exceeds'):ledger(s)


class TransportTests(unittest.TestCase):
    def test_live_endpoint_environment_rejected(self):
        with patch.dict(os.environ,{'APCA_API_BASE_URL':'https://api.alpaca.markets'}):
            with self.assertRaises(GuardError):Alpaca('dummy','dummy')
    def test_readonly_blocks_orders_and_redirects(self):
        client=Alpaca('dummy','dummy')
        with self.assertRaises(GuardError):client.submit({})
        with self.assertRaises(GuardError):client.request('GET','https://example.com')
        with self.assertRaises(GuardError):NoRedirect().redirect_request(None)
    def test_timeout_mutation_is_not_retried(self):
        client=Alpaca('dummy','dummy',True)
        with patch.object(client.opener,'open',side_effect=TimeoutError) as method:
            with self.assertRaises(APIError):client.submit({'symbol':'AAPL'})
            self.assertEqual(method.call_count,1)
    def test_bar_pagination_and_incomplete_detection(self):
        client=Alpaca('dummy','dummy')
        with patch.object(client,'request',side_effect=[{'bars':{'AAPL':[1]},'next_page_token':'x'}, {'bars':{'AAPL':[2]},'next_page_token':None}]):
            self.assertEqual(client.bars(['AAPL'],'start','end'),{'AAPL':[1,2]})
        with patch.object(client,'request',return_value={'bars':{},'next_page_token':'x'}):
            with self.assertRaises(GuardError):client.bars(['AAPL'],'start','end')
    def test_auth_error_does_not_leak_response(self):
        client=Alpaca('dummy-key','dummy-secret')
        with patch.object(client.opener,'open',side_effect=HTTPError('url',403,'dummy-secret',{},None)):
            with self.assertRaises(APIError) as ctx:client.account()
            self.assertNotIn('dummy-secret',str(ctx.exception))


if __name__=='__main__':unittest.main()
