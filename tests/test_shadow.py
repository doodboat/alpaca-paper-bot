import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from paperbot.shadow import Shadow,valid_quote
from paperbot.state import Store
from paperbot.strategy import UTC,Bar,GuardError

NOW=datetime(2026,9,10,15,30,8,tzinfo=UTC)
class ReadOnlyAPI:
    enable_orders=False
    def submit(self,*a):raise AssertionError('Broker mutation attempted')
    cancel=submit
    def clock(self):return {'timestamp':NOW.isoformat(),'is_open':True}
    def calendar(self,*args):return [dict(date=d,open='09:30',close='16:00') for d in ('2026-09-10','2026-09-11','2026-09-14')]
    def quotes(self,symbols):return {s:dict(ap=100,bp=99.9,t=NOW.isoformat()) for s in symbols}
    def bars(self,*a):return {}

class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=Store(self.root/'source');self.api=ReadOnlyAPI()
        self.shadow=Shadow(self.api,self.source,self.root/'shadow')
        self.shadow.s['valuation_valid']=True
    def tearDown(self):
        self.shadow.close();self.source.close();self.temp.cleanup()
    def event(self,symbol,now=NOW):
        self.source.event(now,'entry_signal',symbol=symbol,signal_time=(now-timedelta(seconds=8)).isoformat(),
         quote_ask=100.,quote_bid=99.9,opening_low=95.,entry_date='2026-09-10',exit_date='2026-09-14')
    def test_entries_budget_and_restart_no_duplicates(self):
        for s in ('AAPL','MSFT','AMZN','NVDA'):self.event(s)
        self.shadow.ingest(NOW,True)
        self.assertEqual(len(self.shadow.s['positions']),3)
        self.assertGreaterEqual(self.shadow.s['cash'],0)
        self.assertEqual(self.shadow.s['decisions']['position_limit'],1)
        saved=self.shadow.s['cash'];self.shadow.close()
        self.shadow=Shadow(self.api,self.source,self.root/'shadow')
        self.shadow.ingest(NOW,True)
        self.assertEqual(self.shadow.s['cash'],saved)
        self.assertEqual(len(self.shadow.s['trades']),3)
    def test_existing_events_not_backfilled(self):
        self.event('AAPL')
        other=Shadow(self.api,self.source,self.root/'new')
        try:
            other.s['valuation_valid']=True;other.ingest(NOW,True)
            self.assertFalse(other.s['positions'])
        finally:other.close()
    def test_expired_event_not_replayed(self):
        self.event('AAPL',NOW-timedelta(minutes=3));self.shadow.ingest(NOW,True)
        self.assertFalse(self.shadow.s['positions']);self.assertFalse(self.shadow.s['complete'])
    def test_stale_quote_does_not_invent_equity(self):
        self.event('AAPL');self.shadow.ingest(NOW,True)
        equity,missing=self.shadow.mark({'AAPL':dict(ap=100,bp=99,t=(NOW-timedelta(seconds=11)).isoformat())},NOW)
        self.assertIsNone(equity);self.assertEqual(missing,['AAPL'])
    def test_third_session_exit_and_fees(self):
        self.event('AAPL');self.shadow.ingest(NOW,True)
        n=self.shadow.s['positions']['AAPL']['qty']
        later=datetime(2026,9,14,19,45,8,tzinfo=UTC)
        cal=self.api.calendar();session=cal[-1]
        self.shadow.s['daily']['2026-09-14']={'exits':0}
        self.shadow.feed.today=lambda *args:({'AAPL':[]},{})
        self.api.clock=lambda:dict(timestamp=later.isoformat(),is_open=True)
        self.api.quotes=lambda symbols:{'AAPL':dict(ap=101,bp=100,t=later.isoformat())}
        self.shadow.exits(later,cal,session,{'AAPL':dict(ap=101,bp=100,t=later.isoformat())})
        self.assertFalse(self.shadow.s['positions'])
        self.assertAlmostEqual(self.shadow.s['cash'],45000+n*(100*.9995-100*1.0005)-2)
        self.assertEqual(self.shadow.s['trades'][-1]['reason'],'scheduled')
    def test_prior_range_failure_survives_restart(self):
        self.event('AAPL');self.shadow.ingest(NOW,True)
        later=datetime(2026,9,11,14,tzinfo=UTC)
        self.shadow.s['daily']['2026-09-11']={'exits':0}
        b=Bar(NOW,96,97,93,94,100,95)
        self.shadow.feed.today=lambda *args:({'AAPL':[b]}, {})
        self.api.clock=lambda:dict(timestamp=later.isoformat(),is_open=True)
        self.api.quotes=lambda symbols:{'AAPL':dict(ap=100,bp=99,t=later.isoformat())}
        self.shadow.exits(later,self.api.calendar(),self.api.calendar()[1],{'AAPL':dict(ap=100,bp=99,t=later.isoformat())})
        self.assertFalse(self.shadow.s['positions']);self.assertFalse(self.shadow.s['complete'])
        self.assertEqual(self.shadow.s['trades'][-1]['reason'],'range_failed')
    def test_rejects_order_enabled_api(self):
        self.api.enable_orders=True
        with self.assertRaises(GuardError):Shadow(self.api,self.source,self.root/'bad')
    def test_tick_writes_daily_report_without_orders(self):
        with patch('paperbot.shadow.datetime') as dt:
            dt.now.return_value=NOW
            result=self.shadow.tick()
        self.assertFalse(result['broker_orders_enabled'])
        self.assertTrue((self.root/'shadow'/'daily-2026-09-10.json').exists())
    def test_cli_rejects_shadow_with_paper_orders_before_credentials(self):
        from paperbot.__main__ import main
        with patch.dict('os.environ',{'PAPERBOT_SHADOW':'1'}), patch('paperbot.__main__.credentials') as creds:
            with self.assertRaises(GuardError):main(['run','--enable-paper-orders'])
            creds.assert_not_called()
    def test_nan_and_crossed_quotes_rejected(self):
        for a,b in ((float('nan'),99),(99,100),(100,0)):
            self.assertFalse(valid_quote(dict(ap=a,bp=b,t=NOW.isoformat()),NOW))

if __name__=='__main__':unittest.main()
