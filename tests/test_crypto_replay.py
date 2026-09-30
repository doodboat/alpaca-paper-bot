import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research import crypto_replay as replay

UTC = timezone.utc
START = datetime(2023, 1, 1, tzinfo=UTC)
AS_OF = START+timedelta(days=205)
FIRST_DAY = (START+timedelta(days=200)).date().isoformat()
LAST_DAY = (START+timedelta(days=201)).date().isoformat()


def fixture():
    rows = []
    for i in range(202):
        price = 100.+i
        rows.append(dict(timestamp=(START+timedelta(days=i)).isoformat(),
                         open=price, high=price+1, low=price-1, close=price, volume=1))
    return dict(bars={s: copy.deepcopy(rows) for s in replay.SYMBOLS}, request={'timeframe': '1Day'})


class CryptoReplayTests(unittest.TestCase):
    def test_duplicates_even_identical_are_rejected(self):
        raw = fixture()
        raw['bars'][replay.SYMBOLS[0]].insert(1, copy.deepcopy(raw['bars'][replay.SYMBOLS[0]][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate daily bar'):
            replay.validate_data(raw, AS_OF)

    def test_missing_date_in_both_symbols_is_not_hidden(self):
        raw = fixture()
        for rows in raw['bars'].values():
            del rows[100]
        with self.assertRaisesRegex(ValueError, 'Missing calendar date'):
            replay.validate_data(raw, AS_OF)

    def test_mismatched_symbol_date_coverage_is_rejected(self):
        raw = fixture()
        raw['bars'][replay.SYMBOLS[0]].pop()
        with self.assertRaisesRegex(ValueError, 'different date coverage'):
            replay.validate_data(raw, AS_OF)

    def test_missing_requested_end_is_not_silently_shortened(self):
        raw = fixture()
        for rows in raw['bars'].values():
            rows.pop()
        with self.assertRaisesRegex(ValueError, 'not fully covered'):
            replay.run(raw, FIRST_DAY, LAST_DAY, as_of=AS_OF)

    def test_warmup_must_precede_start(self):
        early = (START+timedelta(days=199)).date().isoformat()
        with self.assertRaisesRegex(ValueError, '200 consecutive prior'):
            replay.run(fixture(), early, LAST_DAY, as_of=AS_OF)

    def test_bad_ohlc_and_nonfinite_values_rejected(self):
        for change in (dict(low=200), dict(close=float('nan')), dict(open=0), dict(high=float('inf')),
                       dict(volume=-1), dict(open=True), dict(symbol='OTHER')):
            with self.subTest(change=change):
                raw = fixture()
                raw['bars'][replay.SYMBOLS[0]][0].update(change)
                with self.assertRaises(ValueError):
                    replay.validate_data(raw, AS_OF)

    def test_timezone_alignment_order_and_completion(self):
        for stamp in ('2023-01-01T00:00:00', '2023-01-01T00:15:00+00:00'):
            raw = fixture()
            raw['bars'][replay.SYMBOLS[0]][0]['timestamp'] = stamp
            with self.assertRaises(ValueError):
                replay.validate_data(raw, AS_OF)
        raw = fixture()
        raw['bars'][replay.SYMBOLS[0]][:2] = list(reversed(raw['bars'][replay.SYMBOLS[0]][:2]))
        with self.assertRaisesRegex(ValueError, 'strictly increasing'):
            replay.validate_data(raw, AS_OF)
        with self.assertRaisesRegex(ValueError, 'Incomplete or future'):
            replay.validate_data(fixture(), START+timedelta(days=201, hours=12))

    def test_current_day_close_cannot_change_that_days_decision(self):
        raw = fixture()
        changed = copy.deepcopy(raw)
        for rows in changed['bars'].values():
            rows[200]['close'] = 1.
            rows[200]['low'] = 1.
        first = replay.run(raw, FIRST_DAY, FIRST_DAY, as_of=AS_OF)
        second = replay.run(changed, FIRST_DAY, FIRST_DAY, as_of=AS_OF)
        self.assertEqual(first['daily'][0]['eligibility'], second['daily'][0]['eligibility'])
        for kind in first['results']:
            self.assertEqual(first['results'][kind]['fills'], second['results'][kind]['fills'])
            self.assertEqual(first['daily'][0]['portfolios'][kind]['positions'],
                             second['daily'][0]['portfolios'][kind]['positions'])
        self.assertNotEqual(first['results']['trend']['final_equity'], second['results']['trend']['final_equity'])

    def test_end_of_day_timing_preserves_original_cost_accounting(self):
        raw = fixture()
        result = replay.run(raw, FIRST_DAY, LAST_DAY, as_of=AS_OF)
        day = result['daily'][0]
        self.assertTrue(day['proxy_open_time'].endswith('T00:00:00+00:00'))
        self.assertTrue(day['decision_time'].endswith('T00:15:00+00:00'))
        self.assertTrue(day['accounting_mark_time'].endswith('T23:59:59.999999+00:00'))
        self.assertLess(day['signal_latest_close_date'], day['date'])
        self.assertGreater(day['bar_completed_at'][:10], day['date'])
        self.assertEqual(result['results']['trend']['operating_allowance'], round(106.25*2/31, 2))
        self.assertEqual(result['results']['buy_hold']['fees'], 112.5)
        self.assertIsNone(replay.run(raw, FIRST_DAY, FIRST_DAY, as_of=AS_OF)['results']['trend']['daily_Sharpe_zero_cash'])

    def test_metadata_fingerprints_bytes_and_labels_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cached.json'
            path.write_text(json.dumps(fixture()))
            result = replay.build_report(path, [(FIRST_DAY, LAST_DAY)], as_of=AS_OF)
            meta = result['metadata']
            self.assertEqual(meta['input_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(meta['code_sha256']['strategy_module'], replay.sha256(replay.crypto_sim.__file__))
            self.assertFalse(meta['out_of_sample'])
            self.assertFalse(meta['parameter_search'])
            self.assertEqual(meta['config']['fee_rate'], replay.FEE)

    def test_output_never_overwrites_an_existing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'result.json'
            path.write_text('preserve old evidence')
            with patch.object(replay, 'build_report') as build:
                with self.assertRaises(SystemExit):
                    replay.main(['unused.json', '--output', str(path)])
                build.assert_not_called()
            self.assertEqual(path.read_text(), 'preserve old evidence')


if __name__ == '__main__':
    unittest.main()
