import copy
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from paperbot.cross_asset import (Data,GuardError,ASSETS,SYMBOLS,weights,initial,plan,settle_day,check_revisions,validate,Runner)
from paperbot.strategy import UTC

D='2026-09-14'
NOW=datetime(2026,9,12,12,tzinfo=UTC)
SESSION={'date':D,'open':'09:30','close':'16:00'}

def fixture():
    bars={s:{'2026-09-11':{'c':100.},D:{'o':100.,'c':101.}} for s in SYMBOLS}
    tri={s:[1.]*253 for s in SYMBOLS}
    tri['SPY'][-22]=1.3;tri['SPY'][-1]=1.2
    tri['GLD'][-22]=1.2;tri['GLD'][-1]=1.1
    return {'calendar':[SESSION], 'days':['2026-09-11'],'bars':bars,'actions':[],'tri':tri}

class CrossAssetTests(unittest.TestCase):
    def test_exact_lookback_and_absolute_filter(self):
        x=fixture();self.assertEqual(weights(x['tri']),{'SPY':.5,'GLD':.5,'BIL':0.})
        x['tri']['SPY'][-1]=.9
        self.assertEqual(weights(x['tri']),{'GLD':.5,'BIL':.5})
        # The latest month must not alter relative ranking, only the absolute filter.
        x['tri']['EFA'][-1]=100
        self.assertEqual(weights(x['tri']),{'GLD':.5,'BIL':.5})
    def test_plan_refuses_lookahead_and_is_immutable(self):
        s=initial(NOW,SESSION);x=fixture();plan(s,x,SESSION,NOW)
        old=copy.deepcopy(s['plans'][D]);x['tri']['SPY'][-22]=0.1
        plan(s,x,SESSION,NOW+timedelta(hours=1));self.assertEqual(s['plans'][D],old)
        with self.assertRaises(GuardError):plan(s,x,SESSION,datetime(2026,9,14,14,tzinfo=UTC))
    def test_affordability_fees_and_shared_start(self):
        s=initial(NOW,SESSION);x=fixture();plan(s,x,SESSION,NOW)
        for sym in SYMBOLS:x['bars'][sym][D]['o']=130
        settle_day(s,x,D)
        for p in s['portfolios'].values():
            self.assertGreaterEqual(p['cash'],0)
            self.assertTrue(all(q>=0 and isinstance(q,int) for q in p['shares'].values()))
        self.assertEqual(s['portfolios']['BIL']['ops'],0)
        self.assertAlmostEqual(s['portfolios']['momentum']['ops'],106.25/30)
        self.assertEqual(len(s['reports']),1)
        settle_day(s,x,D);self.assertEqual(len(s['reports']),1)
    def test_no_dividend_for_ex_date_purchase(self):
        s=initial(NOW,SESSION);x=fixture();plan(s,x,SESSION,NOW)
        x['actions']=[dict(id='d',kind='cash_dividends',symbol='SPY',ex_date=D,rate=2.,payable_date=D)]
        settle_day(s,x,D)
        self.assertEqual(s['portfolios']['momentum']['receivables'],[])
    def test_distribution_receivable_not_spendable_before_payment(self):
        s=initial(NOW,SESSION);x=fixture();plan(s,x,SESSION,NOW);settle_day(s,x,D)
        p=s['portfolios']['momentum'];shares=p['shares']['SPY'];cash=p['cash']
        d='2026-09-15';s['plans'][d]={'created':'2026-09-14T21:00:00+00:00','targets':{}}
        x['calendar'].append(dict(date=d,open='09:30',close='16:00'))
        for symbol in SYMBOLS:x['bars'][symbol][d]={'o':101.,'c':101.}
        x['actions']=[dict(id='d',kind='cash_dividends',symbol='SPY',ex_date=d,rate=2.,payable_date=None)]
        settle_day(s,x,d)
        self.assertEqual(p['cash'],cash);self.assertEqual(p['receivables'][0]['amount'],shares*2)
        self.assertFalse(p['receivables'][0]['paid'])
    def test_revisions_and_missing_plan_block(self):
        s=initial(NOW,SESSION);x=fixture()
        with self.assertRaises(GuardError):settle_day(s,x,D)
        plan(s,x,SESSION,NOW);settle_day(s,x,D)
        x['actions']=[dict(id='late',kind='cash_dividends',symbol='SPY',ex_date=D,rate=2.,payable_date=D)]
        with self.assertRaises(GuardError):check_revisions(s,x)
    def test_transport_no_mutation_route(self):
        with patch.dict('os.environ',{'ALPACA_API_KEY':'fixture','ALPACA_SECRET_KEY':'fixture'}):data=Data()
        with self.assertRaises(GuardError):data.get('/v2/orders')
        self.assertFalse(hasattr(data,'submit'))
    def test_cli_blocks_order_enabled_configuration(self):
        from paperbot.__main__ import main
        with patch.dict('os.environ',{'PAPERBOT_CROSS_ASSET':'1'}),patch('paperbot.__main__.credentials') as creds:
            with self.assertRaises(GuardError):main(['run','--enable-paper-orders'])
            creds.assert_not_called()
    def test_repeated_pagination_token_blocks(self):
        with patch.dict('os.environ',{'ALPACA_API_KEY':'fixture','ALPACA_SECRET_KEY':'fixture'}):data=Data()
        data.get=lambda *args:{'bars':{},'next_page_token':'same'}
        with self.assertRaises(GuardError):data.collect('/v2/stocks/bars',{},'bars')
    def test_runner_restart_retains_plan_without_old_fills(self):
        x=fixture()
        class Fake:
            def get(self,*args):return {'timestamp':NOW.isoformat()}
            def snapshot(self,*args):return x
        with tempfile.TemporaryDirectory() as d,patch('paperbot.cross_asset.datetime') as dt:
            dt.now.return_value=NOW
            r=Runner(Fake(),d);a=r.tick();original=copy.deepcopy(r.s);r.store.close()
            r=Runner(Fake(),d);b=r.tick()
            self.assertEqual(r.s['plans'],original['plans']);self.assertEqual(a['start_session'],b['start_session'])
            self.assertEqual(r.s['reports'],[]);r.store.close()

if __name__=='__main__':unittest.main()
