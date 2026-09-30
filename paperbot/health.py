"""Supervisor observations only; never opens a portfolio ledger or broker connection."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class SupervisorHealth:
    """One process generation's observations, persisted separately from valuations.

    A running record is meaningful only with a fresh supervisor heartbeat. PIDs
    are diagnostic metadata, never evidence that a previous process is alive.
    """

    def __init__(self, directory, *, broker_orders_enabled=False, crypto=False,
                 cross_asset=False, shadow=False, clock=None):
        self.path = Path(directory) / 'supervisor-health.json'
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        now = self._now()
        generation = str(uuid4())
        self.state = {
            'schema_version': 1,
            'generation': generation,
            'time': now,
            'started_at': now,
            'supervisor_status': 'running',
            'broker_orders_enabled': bool(broker_orders_enabled),
            'children': {
                name: self._initial(enabled, generation, now)
                for name, enabled in (('crypto', crypto), ('cross_asset', cross_asset))
            },
            'shadow': self._initial(shadow, generation, now),
        }
        # Do not load or merge an old generation, including its PIDs or statuses.
        self._write()

    @staticmethod
    def _initial(enabled, generation, now):
        result = {'status': 'stopped' if enabled else 'disabled',
                  'generation': generation, 'time': now}
        if enabled:
            result['reason'] = 'not_started'
        return result

    def _now(self):
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError('Supervisor clock must have a timezone')
        return now.astimezone(timezone.utc).isoformat()

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.state, indent=2, allow_nan=False) + '\n'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent,
                                             prefix='.supervisor-health-',
                                             suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def heartbeat(self):
        self.state['time'] = self._now()
        self._write()

    def child_started(self, name, pid):
        now = self._now()
        self.state['children'][name] = {
            'status': 'running', 'generation': self.state['generation'],
            'time': now, 'started_at': now, 'pid': int(pid),
        }
        self.state['time'] = now
        self._write()

    def child_stopped(self, name, exit_code):
        record = self.state['children'][name]
        # Retain the first observed exit across subsequent cycles and shutdown.
        if record['status'] != 'running':
            return
        now = self._now()
        record.update(status='stopped', time=now, stopped_at=now,
                      exit_code=int(exit_code) if exit_code is not None else None,
                      reason='process_exited')
        self.state['time'] = now
        self._write()

    def shadow_started(self):
        now = self._now()
        self.state['shadow'] = {
            'status': 'running', 'generation': self.state['generation'],
            'time': now, 'started_at': now,
        }
        self.state['time'] = now
        self._write()

    def shadow_checked(self, *, blocked=False):
        now = self._now()
        status = 'blocked' if blocked else 'ok'
        record = self.state['shadow']
        record.update(status=status, time=now, last_check_at=now,
                      last_check_status=status)
        # No exception text, response body, request headers or credential values.
        record.pop('message', None)
        if blocked:
            record['message'] = 'Shadow check blocked; inspect shadow status and logs.'
        self.state['time'] = now
        self._write()

    def stopped(self):
        now = self._now()
        self.state.update(time=now, supervisor_status='stopped', stopped_at=now)
        for record in list(self.state['children'].values()) + [self.state['shadow']]:
            if record['status'] in ('running', 'ok', 'blocked'):
                record.update(status='stopped', time=now, stopped_at=now,
                              reason='supervisor_shutdown')
        self._write()
