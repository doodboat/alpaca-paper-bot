import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from paperbot.crypto_sim import (Data, Runner, SYMBOLS, FEE, SLIP, eligible, initial,
                                 decide, report, rebalance, portfolio, validate_quotes, GuardError, UTC)

NOW = datetime(2026, 9, 20, 0, 15, tzinfo=UTC)

def quotes():
    return {s: dict(bid=100., ask=100., time=NOW.isoformat()) for s in SYMBOLS}

def closes(up=True):
    return {s: list(range(100,300)) if up else list(range(300,100,-1)) for s in SYMBOLS}

class CryptoTests(unittest.TestCase):
    def test_frozen_trend_rules(self):
        self.assertTrue(eligible(closes()[SYMBOLS[0]]))
        self.assertFalse(eligible(closes(False)[SYMBOLS[0]]))
        self.assertFalse(eligible([100.]*200))
        with self.assertRaises(GuardError): eligible([100.]*199)
        with self.assertRaises(GuardError): eligible([float('nan')]*200)

    def test_fee_is_withheld_from_received_asset(self):
        p=portfolio();rebalance(p, {SYMBOLS[0]:1.}, quotes(), NOW)
        gross=45000/(100*(1+SLIP))
        self.assertAlmostEqual(p['positions'][SYMBOLS[0]], gross*(1-FEE))
        self.assertAlmostEqual(p['cash'], 0.)
        self.assertAlmostEqual(p['fees'], 45000*FEE)
        qty=p['positions'][SYMBOLS[0]]
        rebalance(p, {}, quotes(), NOW)
        self.assertAlmostEqual(p['cash'], qty*100*(1-SLIP)*(1-FEE))
        self.assertEqual(p['positions'][SYMBOLS[0]],0.)

    def test_freshness_future_and_crossed_guards(self):
        q={s:dict(bp=100,ap=100,t=NOW.isoformat()) for s in SYMBOLS}
        validate_quotes(q,NOW)
        for change in (dict(t=(NOW-timedelta(seconds=31)).isoformat()),dict(bp=101),dict(ap=102),dict(ap=float('nan'))):
            bad=copy.deepcopy(q);bad[SYMBOLS[0]].update(change)
            with self.assertRaises(GuardError):validate_quotes(bad,NOW)

    def test_no_backfill_or_same_day_duplicate(self):
        s=initial(NOW-timedelta(days=1))
        decide(s,closes(),quotes(),NOW.replace(hour=12));self.assertIsNone(s['last_day'])
        decide(s,closes(),quotes(),NOW);before=copy.deepcopy(s)
        decide(s,closes(),quotes(),NOW);self.assertEqual(s,before)
        self.assertEqual(len(s['portfolios']['trend']['fills']),2)

    def test_no_trade_on_initialization_day(self):
        s=initial(NOW);decide(s,closes(),quotes(),NOW)
        self.assertEqual(s['decisions'],[])

    def test_daily_exit_without_rebalancing_survivor(self):
        s=initial(NOW-timedelta(days=1));decide(s,closes(),quotes(),NOW)
        # Monday is a rebalance; use Tuesday to test exit-only behavior.
        p=s['portfolios']['trend'];kept=p['positions'][SYMBOLS[1]]
        c=closes();c[SYMBOLS[0]]=closes(False)[SYMBOLS[0]]
        decide(s,c,quotes(),NOW+timedelta(days=2))
        self.assertEqual(p['positions'][SYMBOLS[0]],0.)
        self.assertAlmostEqual(p['positions'][SYMBOLS[1]],kept)
        self.assertEqual(s['missed_days'][0]['count'],1)
        self.assertEqual(len(s['portfolios']['buy_hold']['fills']),2)

    def test_cash_no_leverage_and_cost_report(self):
        s=initial(NOW-timedelta(days=1));decide(s,closes(),quotes(),NOW)
        r=report(s,quotes(),NOW)
        self.assertGreaterEqual(s['portfolios']['trend']['cash'],-1e-6)
        self.assertLess(r['portfolios']['trend']['equity'],45000)
        self.assertAlmostEqual(r['portfolios']['trend']['operating_allowance'],3.54,places=2)
        self.assertEqual(r['portfolios']['buy_hold']['operating_allowance'],0)

    def test_get_only_rejects_order_endpoint(self):
        with patch.dict('os.environ',{'ALPACA_API_KEY':'fixture','ALPACA_SECRET_KEY':'fixture'}):d=Data()
        with self.assertRaises(GuardError):d.get('/v2/orders',{})
        self.assertFalse(hasattr(d,'submit'))

    def test_restart_does_not_repeat_fills(self):
        class Fake:
            def history(self,*args):return closes()
            def quotes(self):return {s:dict(bp=100,ap=100,t=NOW.isoformat()) for s in SYMBOLS}
        with tempfile.TemporaryDirectory() as directory, patch('paperbot.crypto_sim.datetime') as dt:
            dt.now.return_value=NOW
            # Actual date parsing is needed for reports.
            dt.fromisoformat.side_effect=datetime.fromisoformat
            r=Runner(Fake(),directory);r.store.save(initial(NOW-timedelta(days=1)));r.store.close()
            r=Runner(Fake(),directory);a=r.tick();r.store.close()
            r=Runner(Fake(),directory);b=r.tick();r.store.close()
            self.assertEqual(a['portfolios'],b['portfolios'])

    def test_missing_daily_history_blocks(self):
        with patch.dict('os.environ',{'ALPACA_API_KEY':'fixture','ALPACA_SECRET_KEY':'fixture'}):d=Data()
        d.get=lambda *a: {'bars':{},'next_page_token':None}
        with self.assertRaises(GuardError):d.history(NOW)
        d.get=lambda *a: {'bars':{},'next_page_token':'repeat'}
        with self.assertRaises(GuardError):d.history(NOW)

    def test_crypto_with_paper_orders_rejected_before_credentials(self):
        from paperbot.__main__ import main
        with patch.dict('os.environ',{'PAPERBOT_CRYPTO':'1'}),patch('paperbot.__main__.credentials') as creds:
            with self.assertRaises(GuardError):main(['run','--enable-paper-orders'])
            creds.assert_not_called()

if __name__=='__main__': unittest.main()
