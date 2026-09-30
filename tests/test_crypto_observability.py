import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from paperbot.crypto_sim import (
    GuardError, QuoteGuardError, Runner, SYMBOLS, UTC, atomic_text,
    decision_health, initial, safe_error, validate_quotes,
)


NOW = datetime(2026, 9, 21, 0, 15, tzinfo=UTC)


class FakeData:
    def __init__(self, now=NOW):
        self.now = now
        self.closes = {s: list(range(100, 300)) for s in SYMBOLS}
        self.raw = {s: dict(bp=100., ap=100., t=now.isoformat()) for s in SYMBOLS}

    def history(self, now):
        return copy.deepcopy(self.closes)

    def quotes(self):
        return copy.deepcopy(self.raw)


class CryptoObservabilityTests(unittest.TestCase):
    def tick_at(self, runner, now):
        with patch('paperbot.crypto_sim.datetime') as clock:
            clock.now.return_value = now
            clock.fromisoformat.side_effect = datetime.fromisoformat
            return runner.tick()

    def seed(self, directory, now=NOW):
        runner = Runner(FakeData(now), directory)
        runner.store.save(initial(now-timedelta(days=1)))
        runner.store.close()

    def records(self, directory):
        paths = sorted((Path(directory)/'observations').glob('*.jsonl'))
        return [json.loads(line) for path in paths for line in path.read_text().splitlines()]

    def test_quote_diagnostics_preserve_freshness_and_redact_bad_input(self):
        raw = FakeData().raw
        for age in (-2, 30):
            raw[SYMBOLS[0]]['t'] = (NOW-timedelta(seconds=age)).isoformat()
            validate_quotes(raw, NOW)
        for age, code in ((31, 'stale_quote'), (-3, 'future_quote')):
            raw[SYMBOLS[0]]['t'] = (NOW-timedelta(seconds=age)).isoformat()
            with self.assertRaises(QuoteGuardError) as caught:
                validate_quotes(raw, NOW)
            self.assertEqual(caught.exception.diagnostic['symbol'], 'BTC/USD')
            self.assertEqual(caught.exception.diagnostic['quote_age_seconds'], age)
            self.assertEqual(caught.exception.diagnostic['code'], code)
        raw[SYMBOLS[0]]['t'] = 'SECRET_NOT_A_TIMESTAMP'
        with self.assertRaises(QuoteGuardError) as caught:
            validate_quotes(raw, NOW)
        self.assertNotIn('SECRET', json.dumps(caught.exception.diagnostic))
        self.assertNotIn('SECRET', safe_error(caught.exception))
        self.assertNotIn('SECRET', safe_error(GuardError('SECRET provider response')))
        self.assertNotIn('SECRET', safe_error(RuntimeError('SECRET header')))

    def test_missing_decision_dates_appear_at_window_end_without_mutation(self):
        state = initial(NOW-timedelta(days=1))
        before = copy.deepcopy(state)
        self.assertEqual(decision_health(state, NOW)['status'], 'pending_in_window')
        self.assertEqual(decision_health(state, NOW.replace(minute=29))['missing_utc_dates'], [])
        health = decision_health(state, NOW.replace(minute=30))
        self.assertEqual(health['status'], 'missed')
        self.assertEqual(health['missing_utc_dates'], ['2026-09-21'])
        self.assertEqual(decision_health(state, NOW+timedelta(days=2))['missing_utc_dates'],
                         ['2026-09-21', '2026-09-22'])
        self.assertEqual(state, before)

    def test_blocked_poll_retains_explicit_historical_mark_and_no_ledger_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.seed(directory)
            data = FakeData()
            runner = Runner(data, directory)
            good = self.tick_at(runner, NOW)
            self.assertEqual(good['quote_diagnostics']['BTC/USD']['bid'], 100.)
            self.assertEqual(good['quote_diagnostics']['BTC/USD']['ask'], 100.)
            self.assertEqual(good['quote_request_started_at'], NOW.isoformat())
            self.assertEqual(good['quote_received_at'], NOW.isoformat())
            before = runner.store.load()
            late = NOW+timedelta(days=1, minutes=16)
            with self.assertRaises(QuoteGuardError) as caught:
                self.tick_at(runner, late)
            blocked = runner.blocked(caught.exception, late)
            self.assertIsNone(blocked['equity'])
            self.assertIsNone(blocked['portfolios'])
            self.assertFalse(blocked['last_valid_valuation']['is_current'])
            self.assertEqual(blocked['last_valid_valuation']['time'], good['time'])
            self.assertEqual(blocked['decision_health']['missing_utc_dates'], ['2026-09-22'])
            self.assertEqual(runner.store.load(), before)
            self.assertEqual(runner.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
            records = self.records(directory)
            self.assertEqual([r['status'] for r in records], ['ok', 'blocked'])
            self.assertEqual(json.loads((Path(directory)/'daily-2026-09-21.json').read_text())['time'], good['time'])
            runner.store.close()
            restarted = Runner(data, directory)
            blocked = restarted.blocked(RuntimeError('SECRET'), late)
            self.assertEqual(blocked['last_valid_valuation']['time'], good['time'])
            self.assertNotIn('SECRET', json.dumps(blocked))
            restarted.store.close()

    def test_same_day_revised_history_has_matching_immutable_evidence(self):
        midnight = NOW.replace(minute=0)
        with tempfile.TemporaryDirectory() as directory:
            self.seed(directory)
            early_data = FakeData(midnight)
            early = Runner(early_data, directory)
            first = self.tick_at(early, midnight)
            legacy = Path(directory)/'inputs-2026-09-21.json'
            legacy_bytes = legacy.read_bytes()
            early.store.close()
            changed = FakeData()
            changed.closes['BTC/USD'][-1] += 1
            later = Runner(changed, directory)
            second = self.tick_at(later, NOW)
            saved = later.store.load()
            evidence = Path(directory)/second['input_evidence']
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            self.assertEqual(saved['decisions'][-1]['input_sha256'], digest)
            self.assertEqual(second['input_sha256'], digest)
            self.assertNotEqual(first['input_sha256'], digest)
            self.assertTrue((Path(directory)/first['input_evidence']).is_file())
            self.assertEqual(legacy.read_bytes(), legacy_bytes)
            later.store.close()
            restart = Runner(changed, directory)
            self.tick_at(restart, NOW)
            self.assertEqual(restart.store.load(), saved)
            restart.store.close()

    def test_corrupt_content_addressed_evidence_blocks_before_ledger_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            self.seed(directory)
            data = FakeData()
            runner = Runner(data, directory)
            before = runner.store.load()
            body = json.dumps(data.closes, sort_keys=True)
            digest = hashlib.sha256(body.encode()).hexdigest()
            path = Path(directory)/'inputs'/('sha256-'+digest+'.json')
            atomic_text(path, '{}')
            with self.assertRaisesRegex(GuardError, 'evidence hash mismatch'):
                self.tick_at(runner, NOW)
            self.assertEqual(runner.store.load(), before)
            self.assertEqual(runner.s, before)
            runner.store.close()

    def test_observations_append_atomically_and_cap_without_deleting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Runner(FakeData(), directory)
            evidence = Path(directory)/'inputs-2026-09-01.json'
            evidence.write_text('{}')
            small = dict(time=NOW.isoformat(), status='blocked', message='fixture')
            runner.append_observation(small, NOW)
            first_path = next((Path(directory)/'observations').glob('*.jsonl'))
            prefix = first_path.read_bytes()
            runner.append_observation(small, NOW+timedelta(minutes=5))
            self.assertTrue(first_path.read_bytes().startswith(prefix))
            runner.append_observation(small, NOW+timedelta(days=100))
            self.assertTrue(first_path.exists())
            self.assertTrue(evidence.exists())
            with patch('paperbot.crypto_sim.OBSERVATION_SEGMENT_BYTES', 150):
                for minute in range(5):
                    runner.append_observation(small, NOW+timedelta(days=2, minutes=minute))
            files = list((Path(directory)/'observations').glob('*.jsonl'))
            self.assertGreater(len(files), 2)
            saved = {p: p.read_bytes() for p in files}
            with patch('paperbot.crypto_sim.OBSERVATION_MAX_BYTES', 1):
                blocked = runner.blocked(GuardError('fixture'), NOW)
            self.assertEqual(blocked['observation_journal_status'], 'journal_capacity_exceeded')
            self.assertEqual({p: p.read_bytes() for p in files}, saved)
            self.assertEqual(json.loads((Path(directory)/'status.json').read_text())['observation_journal_status'],
                             'journal_capacity_exceeded')
            self.assertTrue(self.records(directory))
            self.assertEqual(list(Path(directory).rglob('*.tmp')), [])
            runner.store.close()

    def test_full_journal_preserves_simulation_and_current_daily_output(self):
        with tempfile.TemporaryDirectory() as directory:
            self.seed(directory)
            runner = Runner(FakeData(), directory)
            with patch('paperbot.crypto_sim.OBSERVATION_MAX_BYTES', 1):
                result = self.tick_at(runner, NOW)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['observation_journal_status'], 'journal_capacity_exceeded')
            self.assertEqual(runner.store.load()['last_day'], NOW.date().isoformat())
            daily = json.loads((Path(directory)/'daily-2026-09-21.json').read_text())
            self.assertEqual(daily['portfolios'], result['portfolios'])
            self.assertEqual(daily['observation_journal_status'], 'journal_capacity_exceeded')
            runner.store.close()


if __name__ == '__main__':
    unittest.main()
