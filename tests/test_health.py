import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from paperbot.health import SupervisorHealth
from paperbot.strategy import GuardError


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = NOW

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **kwargs):
        return SupervisorHealth(self.root, clock=lambda: self.now, **kwargs)

    def read(self):
        return json.loads((self.root / 'supervisor-health.json').read_text())

    def test_child_exit_survives_heartbeat_and_supervisor_shutdown(self):
        health = self.create(crypto=True)
        health.child_started('crypto', 101)
        self.now += timedelta(seconds=10)
        health.child_stopped('crypto', 2)
        stopped = self.read()['children']['crypto']
        self.now += timedelta(seconds=10)
        health.heartbeat()
        self.assertEqual(self.read()['children']['crypto'], stopped)
        self.assertEqual(self.read()['supervisor_status'], 'running')
        health.stopped()
        self.assertEqual(self.read()['children']['crypto'], stopped)
        self.assertEqual(stopped['exit_code'], 2)
        self.assertEqual(self.read()['supervisor_status'], 'stopped')

    def test_new_generation_does_not_trust_or_preserve_old_pid(self):
        old = self.create(crypto=True, cross_asset=True)
        old.child_started('crypto', 123)
        old.child_stopped('crypto', 7)
        previous_generation = self.read()['generation']
        self.now += timedelta(minutes=1)
        new = self.create(crypto=True)
        state = self.read()
        self.assertNotEqual(state['generation'], previous_generation)
        self.assertEqual(state['started_at'], self.now.isoformat())
        self.assertEqual(state['children']['crypto']['status'], 'stopped')
        self.assertEqual(state['children']['crypto']['reason'], 'not_started')
        self.assertNotIn('pid', state['children']['crypto'])
        self.assertNotIn('exit_code', state['children']['crypto'])
        self.assertEqual(state['children']['cross_asset']['status'], 'disabled')
        new.child_started('crypto', 124)
        self.assertEqual(self.read()['children']['crypto']['status'], 'running')
        self.assertEqual(self.read()['children']['crypto']['generation'], state['generation'])

    def test_shadow_recovers_from_blocked_without_exposing_error_text(self):
        health = self.create(shadow=True)
        health.shadow_started()
        health.shadow_checked(blocked=True)
        self.assertEqual(self.read()['shadow']['status'], 'blocked')
        self.assertEqual(self.read()['shadow']['last_check_at'], NOW.isoformat())
        self.now += timedelta(seconds=10)
        health.shadow_checked()
        self.assertEqual(self.read()['shadow']['status'], 'ok')
        self.assertNotIn('message', self.read()['shadow'])
        health.stopped()
        self.assertEqual(self.read()['shadow']['status'], 'stopped')
        self.assertEqual(self.read()['shadow']['last_check_status'], 'ok')

    def test_health_never_opens_or_changes_portfolio_ledgers(self):
        expected = {}
        for directory in (self.root, self.root/'crypto', self.root/'cross_asset', self.root/'shadow'):
            directory.mkdir(exist_ok=True)
            for name in ('state.sqlite3', 'status.json', 'daily-2026-09-30.json'):
                path = directory/name
                path.write_bytes(b'untouched portfolio data')
                expected[path] = path.read_bytes()
        health = self.create(crypto=True, shadow=True)
        health.child_started('crypto', 1)
        health.shadow_started()
        health.shadow_checked(blocked=True)
        health.heartbeat()
        health.stopped()
        self.assertEqual({path: path.read_bytes() for path in expected}, expected)
        self.assertFalse(self.read()['broker_orders_enabled'])
        self.assertEqual(set(self.root.iterdir()),
                         {self.root/'crypto', self.root/'cross_asset', self.root/'shadow',
                          self.root/'state.sqlite3', self.root/'status.json',
                          self.root/'daily-2026-09-30.json', self.root/'supervisor-health.json'})

    def test_failed_atomic_replace_keeps_previous_complete_report(self):
        health = self.create()
        previous = (self.root/'supervisor-health.json').read_bytes()
        self.now += timedelta(seconds=10)
        with patch('paperbot.health.os.replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                health.heartbeat()
        self.assertEqual((self.root/'supervisor-health.json').read_bytes(), previous)
        self.assertEqual(list(self.root.glob('.supervisor-health-*.tmp')), [])

    def run_main(self, *, crypto=False, shadow=False, process=None, shadow_error=None):
        from paperbot.__main__ import main
        output = io.StringIO()
        flags = {'PAPERBOT_CRYPTO': str(int(crypto)), 'PAPERBOT_CROSS_ASSET': '0',
                 'PAPERBOT_SHADOW': str(int(shadow))}
        with patch.dict('os.environ', flags), \
             patch('paperbot.__main__.credentials', return_value=('fixture', 'fixture')), \
             patch('paperbot.__main__.Alpaca') as api, \
             patch('paperbot.__main__.Engine') as engine, \
             patch('paperbot.__main__.Store') as store, \
             patch('paperbot.__main__.subprocess.Popen', return_value=process) as spawn, \
             patch('paperbot.__main__.signal.signal'), \
             patch('paperbot.shadow.Shadow') as shadow_type, \
             contextlib.redirect_stdout(output):
            engine.return_value.tick.return_value = {'mode': 'observe'}
            shadow_type.return_value.tick.side_effect = shadow_error
            shadow_type.return_value.tick.return_value = {'mode': 'shadow_simulation'}
            result = main(['run', '--once', '--state-dir', str(self.root)])
            self.assertFalse(api.call_args.kwargs['enable_orders'])
            store.return_value.save.assert_not_called()
            store.return_value.event.assert_not_called()
            return result, output.getvalue(), spawn.call_args_list

    def test_main_records_child_exit_before_forgetting_process(self):
        process = Mock(pid=101, returncode=19)
        process.poll.return_value = 19
        result, output, calls = self.run_main(crypto=True, process=process)
        self.assertEqual(result, 0)
        child = self.read()['children']['crypto']
        self.assertEqual(child['status'], 'stopped')
        self.assertEqual(child['exit_code'], 19)
        self.assertEqual(child['reason'], 'process_exited')
        self.assertEqual(self.read()['supervisor_status'], 'stopped')
        process.terminate.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0][1:],
                         ['-u', '-m', 'paperbot.crypto_sim', 'run',
                          '--state-dir', str(self.root/'crypto')])

    def test_main_persists_sanitized_shadow_block_and_closes_cleanly(self):
        result, output, calls = self.run_main(shadow=True,
                                             shadow_error=GuardError('sensitive fixture value'))
        self.assertEqual(result, 0)
        self.assertEqual(calls, [])
        record = self.read()['shadow']
        self.assertEqual(record['status'], 'stopped')
        self.assertEqual(record['last_check_status'], 'blocked')
        self.assertIn('last_check_at', record)
        self.assertNotIn('sensitive fixture value', output)
        self.assertNotIn('sensitive fixture value', json.dumps(self.read()))


if __name__ == '__main__':
    unittest.main()
