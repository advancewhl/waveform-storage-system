"""Durable Agent catalog overlay: versions, audit and idempotency in one commit.

The acquisition Mongo catalog remains authoritative for original identities.
This separately owned repository never updates or removes original records.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock

from waveform_storage.analysis.service import _owner_lock


def now():
    return datetime.now(timezone.utc).isoformat()


class AgentVersionRepository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RLock()
        self.owner = (self.root / 'owner.lock').open('a+')
        try:
            _owner_lock(self.owner, True)
            path = self.root / 'catalog.json'
            self.state = json.loads(path.read_text()) if path.exists() else {
                'operations': {}, 'keys': {}, 'approvals': {}, 'targets': {}, 'jobs': {}}
            # Existing unversioned Agent overlays are the compatible version-1 layout.
            version = self.state.get('schema_version', 1)
            if type(version) is not int or version != 1:
                raise ValueError('Unsupported Agent catalog schema_version')
            self.state['schema_version'] = 1
            # Recovery is state-only: never resubmit jobs, hardware or file writes.
            for group in ('operations', 'jobs'):
                for row in self.state[group].values():
                    if row['status'] in ('accepted', 'running'):
                        row.update(status='failed', updated_at=now(), error={
                            'code': 'INTERNAL', 'message': 'Interrupted by restart; no automatic replay',
                            'operation_id': row.get('operation_id'), 'retryable': False})
            self.save()
        except BaseException:
            self.close()
            raise

    def save(self):
        temporary = self.root / 'catalog.json.tmp'
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.state, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.root / 'catalog.json')
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def get(self, group, key):
        with self.lock:
            return deepcopy(self.state[group].get(key))

    def close(self):
        if not self.owner.closed:
            _owner_lock(self.owner, False)
            self.owner.close()
