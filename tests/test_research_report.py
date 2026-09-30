import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from paperbot.research_report import build_report, main

NOW = datetime(2026, 9, 30, 9, tzinfo=timezone.utc)


def crypto(time=None):
    return {'time': (time or NOW).isoformat(), 'mode': 'crypto_simulation',
            'broker_orders_enabled': False, 'start_day': '2026-09-21',
            'last_decision_day': '2026-09-30', 'missed_days': [{'before': '2026-09-27', 'count': 1}],
            'portfolios': {'trend': {'equity': 45400, 'cash': 0, 'positions': {'BTC/USD': .2},
                                     'fees': 110, 'slippage': 44, 'operating_allowance': 35,
                                     'max_observed_drawdown': .05, 'fills': 4},
                           'buy_hold': {'equity': 45435, 'cash': 0, 'positions': {'BTC/USD': .2},
                                        'fills': 2}}}


class ResearchReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, path, value):
        path = self.root/path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def report(self):
        return build_report(self.root, NOW)

    def test_missing_reports_do_not_create_directory_or_zero_equity(self):
        root = self.root/'not-created'
        result = build_report(root, NOW)
        self.assertFalse(root.exists())
        self.assertEqual(result['crypto']['health']['state'], 'absent')
        self.assertEqual(result['crypto']['valuation_state'], 'absent')
        self.assertEqual(result['crypto']['portfolios'], {})
        self.assertIsNone(result['equity_shadow']['portfolio']['equity'])

    def test_fresh_crypto_has_independent_portfolios_not_combined_capital(self):
        self.write('crypto/status.json', crypto())
        result = self.report()['crypto']
        self.assertEqual(result['valuation_state'], 'current_at_report_timestamp')
        self.assertEqual(result['health']['age_seconds'], 0)
        for p in result['portfolios'].values():
            self.assertEqual(p['starting_virtual_capital'], 45000)
        self.assertEqual(result['portfolios']['trend']['profit_loss'], 400)
        self.assertAlmostEqual(result['comparison']['excess_return_percentage_points'], -35/450)
        self.assertEqual(result['missed_days'], [{'before': '2026-09-27', 'count': 1}])

    def test_stale_crypto_is_never_current(self):
        self.write('crypto/status.json', crypto(NOW-timedelta(minutes=20)))
        result = self.report()['crypto']
        self.assertEqual(result['health']['state'], 'stale')
        self.assertEqual(result['valuation_state'], 'last_recorded_not_current')
        self.assertEqual(result['portfolios']['trend']['equity'], 45400)

    def test_blocked_status_falls_back_explicitly_to_older_daily(self):
        self.write('crypto/status.json', {'time': NOW.isoformat(), 'status': 'blocked', 'message': 'secret response'})
        self.write('crypto/daily-2026-09-29.json', crypto(NOW-timedelta(hours=10)))
        result = self.report()['crypto']
        self.assertEqual(result['health']['state'], 'fresh')
        self.assertEqual(result['cycle_status'], 'blocked')
        self.assertEqual(result['valuation_state'], 'last_recorded_not_current')
        self.assertEqual(result['fallback_report_date'], '2026-09-29')
        self.assertNotIn('secret response', json.dumps(result))

    def test_shadow_null_equity_preserves_last_mark_and_never_imputes_zero(self):
        self.write('shadow/status.json', {'time': NOW.isoformat(), 'equity': None,
                   'last_complete_equity': 44000, 'last_valued_at': '2026-09-29T19:59:50+00:00',
                   'valuation_missing': ['AAPL'], 'positions': {'AAPL': 44},
                   'benchmark_equity': 46000, 'benchmark_started': '2026-09-10T13:30:00+00:00'})
        result = self.report()['equity_shadow']
        self.assertFalse(result['current_equity_available'])
        self.assertEqual(result['valuation_state'], 'last_recorded_not_current')
        self.assertEqual(result['portfolio']['equity'], 44000)
        self.assertFalse(result['benchmark']['comparable'])
        self.assertNotIn('excess_return_percentage_points', result['benchmark'])

    def test_cross_asset_daily_values_not_labeled_live(self):
        self.write('cross_asset/status.json', {'time': NOW.isoformat(), 'start_session': '2026-09-16',
            'last_completed_session': '2026-09-29', 'next_precommitted_session': '2026-09-30',
            'latest_report': {'date': '2026-09-29', 'portfolios': {
                'momentum': {'equity': 44000}, 'equal_weight': {'equity': 44500}, 'BIL': {'equity': 45000}}}})
        result = self.report()['cross_asset']
        self.assertEqual(result['valuation_state'], 'completed_session_only')
        self.assertEqual(result['valuation_session'], '2026-09-29')
        self.assertIsNone(result['valuation_time'])
        self.assertTrue(result['comparisons'][0]['same_period_confirmed'])
        self.assertNotIn('missed_sessions', result)

    def test_unknown_start_does_not_manufacture_excess_return(self):
        value = crypto(); value.pop('start_day')
        self.write('crypto/status.json', value)
        self.assertFalse(self.report()['crypto']['comparison']['same_period_confirmed'])

    def test_corrupt_one_strategy_does_not_hide_others_or_echo_paths(self):
        self.write('crypto/status.json', crypto())
        (self.root/'shadow').mkdir()
        (self.root/'shadow/status.json').write_text('{secret:invalid')
        result = self.report()
        self.assertEqual(result['equity_shadow']['health']['state'], 'invalid_json')
        self.assertEqual(result['crypto']['portfolios']['trend']['equity'], 45400)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))

    def test_daily_reports_expose_timestamps_and_gap_without_sharpe(self):
        self.write('crypto/daily-2026-09-27.json', crypto(NOW-timedelta(days=3)))
        self.write('crypto/daily-2026-09-29.json', crypto(NOW-timedelta(days=1, hours=1)))
        result = self.report()['crypto']['daily_quality']
        self.assertEqual(result['daily_report_count'], 2)
        self.assertEqual(result['calendar_gaps'], [{'after': '2026-09-27', 'before': '2026-09-29', 'calendar_days': 1}])
        self.assertIsNone(result['annualized_sharpe'])
        self.assertEqual(len(result['observations']), 2)
        self.assertIn('variable timestamps', result['valuation_note'])

    def test_daily_future_snapshot_is_excluded_for_asof(self):
        self.write('crypto/daily-2026-09-30.json', crypto(NOW+timedelta(hours=1)))
        result = self.report()['crypto']
        self.assertEqual(result['portfolios'], {})
        self.assertEqual(result['daily_quality']['file_errors'][0]['error'], 'future_snapshot')

    def test_future_and_naive_timestamps_are_not_fresh(self):
        self.write('crypto/status.json', crypto(NOW+timedelta(minutes=1)))
        self.assertEqual(self.report()['crypto']['health']['state'], 'future_timestamp')
        value = crypto(); value['time'] = '2026-09-30T09:00:00'
        self.write('crypto/status.json', value)
        self.assertEqual(self.report()['crypto']['health']['state'], 'unknown_timestamp')

    def test_supervisor_stale_stopped_heartbeat_does_not_prove_current_stop(self):
        self.write('supervisor-health.json', {'time': (NOW-timedelta(days=1)).isoformat(),
            'supervisor_status': 'stopped', 'children': {'crypto': {'status': 'stopped', 'exit_code': 2}}})
        result = self.report()['supervisor']
        self.assertEqual(result['last_reported_status'], 'stopped')
        self.assertEqual(result['current_worker_state'], 'unknown')
        self.write('supervisor-health.json', {'time': NOW.isoformat(), 'supervisor_status': 'stopped'})
        self.assertEqual(self.report()['supervisor']['current_worker_state'], 'stopped')

    def test_no_file_or_ledger_writes_and_no_database_reads(self):
        self.write('crypto/status.json', crypto())
        (self.root/'crypto/state.sqlite3').write_bytes(b'private ledger sentinel')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('sqlite3.connect', side_effect=AssertionError('database access forbidden')):
            self.report()
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_cli_emits_json_only_and_requires_timezone(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['--state-dir', str(self.root), '--as-of', NOW.isoformat()]), 0)
        self.assertTrue(json.loads(out.getvalue())['read_only'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['--as-of', '2026-09-30'])

    def test_fresh_shadow_status_with_old_mark_is_not_current(self):
        self.write('shadow/status.json', {'time': NOW.isoformat(), 'equity': 44000,
                   'last_complete_equity': 44000, 'last_valued_at': (NOW-timedelta(days=1)).isoformat()})
        result = self.report()['equity_shadow']
        self.assertEqual(result['valuation_state'], 'last_recorded_not_current')
        self.assertFalse(result['current_equity_available'])

    def test_malformed_containers_do_not_hide_other_strategies(self):
        self.write('crypto/status.json', {'time': NOW.isoformat(), 'portfolios': []})
        self.write('supervisor-health.json', {'time': NOW.isoformat(), 'children': []})
        self.write('cross_asset/status.json', {'time': NOW.isoformat(), 'latest_report': []})
        result = self.report()
        self.assertEqual(result['crypto']['valuation_state'], 'absent')
        self.assertEqual(result['cross_asset']['valuation_state'], 'absent')
        self.assertEqual(result['supervisor']['children'], {})

    def test_fresh_status_without_valid_portfolio_marks_has_no_current_value(self):
        value = crypto()
        for p in value['portfolios'].values():
            p['equity'] = None
        self.write('crypto/status.json', value)
        result = self.report()['crypto']
        self.assertEqual(result['valuation_state'], 'absent')
        self.assertFalse(result['comparison']['same_period_confirmed'])

    def test_repeated_daily_marks_are_not_independent_valuations(self):
        value = {'time': NOW.isoformat(), 'equity': None, 'last_complete_equity': 45000,
                 'last_valued_at': '2026-09-25T19:59:00+00:00'}
        self.write('shadow/daily-2026-09-26.json', {'latest': value})
        self.write('shadow/daily-2026-09-27.json', {'latest': value})
        quality = self.report()['equity_shadow']['daily_quality']
        self.assertEqual(quality['daily_report_count'], 2)
        self.assertEqual(quality['distinct_valuation_times'], 1)
        self.assertIsNone(quality['annualized_sharpe'])

    def test_blocked_nested_last_valid_uses_newest_evidence_and_start(self):
        old = crypto(NOW-timedelta(days=1))
        self.write('crypto/daily-2026-09-29.json', old)
        newer = crypto(NOW-timedelta(minutes=5))
        self.write('crypto/status.json', {'time': NOW.isoformat(), 'status': 'blocked', 'portfolios': None,
             'last_valid_valuation': {'time': newer['time'], 'is_current': False, 'portfolios': newer['portfolios']},
             'decision_health': {'start_day': '2026-09-21', 'status': 'completed', 'missed_day_count': 1},
             'observation_journal_status': 'write_failed',
             'quote_diagnostic': {'code': 'stale', 'symbol': 'BTC/USD', 'quote_age_seconds': 40, 'headers': 'SECRET'}})
        result = self.report()['crypto']
        self.assertEqual(result['valuation_state'], 'last_recorded_not_current')
        self.assertEqual(result['fallback_source'], 'status.last_valid_valuation')
        self.assertEqual(result['valuation_age_seconds'], 300)
        self.assertTrue(result['comparison']['same_period_confirmed'])
        self.assertTrue(result['observation_journal_alarm'])
        self.assertEqual(result['quote_diagnostic']['quote_age_seconds'], 40)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_newer_daily_beats_older_nested_last_valid(self):
        newer = crypto(NOW-timedelta(minutes=5))
        self.write('crypto/daily-2026-09-30.json', newer)
        old = crypto(NOW-timedelta(days=1))
        self.write('crypto/status.json', {'time': NOW.isoformat(), 'status': 'blocked',
             'last_valid_valuation': old, 'portfolios': None})
        result = self.report()['crypto']
        self.assertEqual(result['fallback_source'], 'daily_report')
        self.assertEqual(result['valuation_time'], newer['time'])

    def test_malformed_daily_timestamp_cannot_abort_combined_report(self):
        value = crypto(); value['time'] = {'unexpected': 'SECRET'}
        self.write('crypto/daily-2026-09-29.json', value)
        self.write('shadow/daily-2026-09-29.json', {'latest': {
            'time': NOW.isoformat(), 'last_complete_equity': 45000, 'last_valued_at': ['SECRET']}})
        result = self.report()
        self.assertIsNone(result['crypto']['daily_quality']['observations'][0]['valuation_time'])
        self.assertEqual(result['crypto']['daily_quality']['distinct_valuation_times'], 0)
        self.assertIsNone(result['equity_shadow']['valuation_time'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_invalid_equity_is_unavailable_not_nan(self):
        value = crypto(); value['portfolios']['trend']['equity'] = float('nan')
        self.write('crypto/status.json', value)
        result = self.report()
        self.assertIsNone(result['crypto']['portfolios']['trend']['equity'])
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
