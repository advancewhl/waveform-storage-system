"""Durable, bounded v1 Agent jobs and idempotency, owned by one process."""
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
import json

from waveform_storage.analysis.service import _owner_lock, write_json


TERMINAL = frozenset({'succeeded', 'failed', 'cancelled'})


def now():
    return datetime.now(timezone.utc).isoformat()


class AgentJobRegistry:
    """One atomic JSON record binds each key to its normalized request and job."""

    def __init__(self, output_root):
        self.root = output_root / 'agent-v1-jobs'
        self.root.mkdir(exist_ok=True)
        self._owner = (self.root / 'owner.lock').open('a+')
        try:
            _owner_lock(self._owner, True)
        except OSError:
            self._owner.close()
            raise RuntimeError('Agent job registry already owned') from None
        self.lock = RLock()
        self.closed = False
        self.states = {}
        self.keys = {}
        try:
            for path in sorted(self.root.glob('*.json')):
                state = json.loads(path.read_text(encoding='utf-8'))
                key = state['request']['idempotency_key']
                if key in self.keys:
                    raise ValueError('Duplicate persisted idempotency key')
                if state['status'] not in TERMINAL:
                    state.update(status='failed', updated_at=now(), error={
                        'code': 'UNAVAILABLE', 'reason': 'Agent restarted before job completion',
                        'diagnostics': {'error_code': 'UNAVAILABLE',
                                        'reason': 'Agent restarted before job completion',
                                        'detail': 'Retry returns this failed job; use a new key for an intentional rerun'}})
                    write_json(path, state)
                self.states[state['job_id']] = state
                self.keys[key] = state['job_id']
        except BaseException:
            _owner_lock(self._owner, False)
            self._owner.close()
            raise

    def create(self, request, config, accepting):
        from .agent_service import AgentError
        with self.lock:
            existing = self.keys.get(request['idempotency_key'])
            if existing:
                state = self.states[existing]
                if state['request'] != request:
                    raise AgentError('INVALID_ARGUMENT', 'Idempotency key is bound to a different request')
                return deepcopy(state), False
            if self.closed or not accepting or sum(
                    s['status'] not in TERMINAL for s in self.states.values()) >= 8:
                raise AgentError('UNAVAILABLE', 'Agent analysis queue unavailable')
            job_id = str(uuid4())
            state = {'job_id': job_id, 'analysis_job_id': None, 'status': 'accepted',
                     'request': request, 'analysis_parameters': config,
                     'created_at': now(), 'updated_at': now()}
            # Persist before acknowledging acceptance or dispatching heavy work.
            write_json(self.root / (job_id + '.json'), state)
            self.states[job_id] = state
            self.keys[request['idempotency_key']] = job_id
            return deepcopy(state), True

    def update(self, job_id, **fields):
        with self.lock:
            state = {**self.states[job_id], **fields, 'updated_at': now()}
            write_json(self.root / (job_id + '.json'), state)
            self.states[job_id] = state

    def get(self, job_id):
        from .agent_service import AgentError
        with self.lock:
            if job_id not in self.states:
                raise AgentError('NOT_FOUND', 'Agent job not found')
            return deepcopy(self.states[job_id])

    def close(self):
        with self.lock:
            if not self._owner.closed:
                _owner_lock(self._owner, False)
                self._owner.close()
