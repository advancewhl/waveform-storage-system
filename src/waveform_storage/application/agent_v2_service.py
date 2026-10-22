"""Agent v2 facade over existing catalog, retrieval, writers and acquisition v2."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import math
from pathlib import Path
from threading import Lock
import tempfile
import time
import h5py
import numpy as np
from uuid import uuid4

from waveform_storage.acquisition.protocol import CaptureRequest, PROTOCOL_VERSION
from waveform_storage.acquisition.runtime import capture_interval_seconds
from waveform_storage.acquisition.server import RegistryPolicy
from waveform_storage.analysis.http_api import ARTIFACTS
from waveform_storage.analysis.loaders import _sampling_rate
from waveform_storage.analysis.models import AnalysisConfig, AnalysisRequest
from waveform_storage.analysis.service import write_json
from waveform_storage.io.hdf5_io import read_single_device_hdf5, write_single_device_hdf5, write_alignment_v2, write_legacy_merged_hdf5
from waveform_storage.io.txt_parser import ParsedWaveform
from waveform_storage.metadata.agent_version_repository import AgentVersionRepository, now
from waveform_storage.processing.alignment import AlignmentParameters
from .agent_v2_contract import APIError, PREFIX, exact, target, uuid, identifier, digest
from .agent_v2_storage import CatalogSnapshots
from .retrieval_service import RetrievalService, StorageQuery


class WaveformAgentV2:
    def __init__(self, v1, registry, access, *, roots=(), max_bytes=64 * 1024 * 1024,
                 timeout=30, max_duration=3600, max_devices=32):
        self.v1 = v1
        self.backend = v1.backend
        self.access = access
        self.registry = registry
        self.policy = RegistryPolicy(registry, max_duration_seconds=max_duration, max_devices=max_devices)
        self.repo = AgentVersionRepository(v1.jobs.output_root / 'agent-v2')
        self.snapshots = CatalogSnapshots(self.backend, roots, self.repo.root,
                                         max_bytes=max_bytes, timeout=timeout)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='agent-v2')
        self.closed = False
        self.mutation_lock = Lock()
        from waveform_storage.processing import alignment
        self.alignment_source_hash = hashlib.sha256(Path(alignment.__file__).read_bytes()).hexdigest()

    def close(self):
        with self.repo.lock:
            self.closed = True
        self.worker.shutdown(wait=True)
        self.repo.close()

    def segment(self, request_id, sequence):
        with self.v1._lock:
            return self.v1._segment(request_id, sequence)

    def _entry(self, identity):
        key = target(identity)
        entry = self.repo.get('targets', key)
        return entry or {'target': identity, 'target_id': key, 'version': 0,
                         'deleted': False, 'annotations': {}, 'versions': []}

    def captures(self, request_id):
        result = []
        for segment in self.v1.list_captures(request_id):
            segment['device_ids'] = [d for d in segment['device_ids'] if not self._entry({
                'request_id': request_id, 'capture_sequence': segment['capture_sequence'],
                'device_id': d})['deleted']]
            if segment['device_ids']:
                result.append(segment)
        return result

    def inspect(self, request_id, sequence):
        self.segment(request_id, sequence)
        row = next((s for s in self.captures(request_id) if s['capture_sequence'] == sequence), None)
        if row is None:
            raise APIError('NOT_FOUND')
        row['objects'] = [self.public_entry(self._entry({'request_id': request_id,
                          'capture_sequence': sequence, 'device_id': d})) for d in row['device_ids']]
        return row

    def run(self, request_id):
        captures = self.captures(uuid(request_id))
        if not captures:
            raise APIError('NOT_FOUND')
        selected_date = captures[0]['storage_date_local'] or captures[0]['captured_at'][:10]
        matches = [r for r in self.v1.list_capture_runs(selected_date) if r['request_id'] == request_id]
        if len(matches) != 1:
            raise APIError('AMBIGUOUS' if matches else 'NOT_FOUND')
        row = matches[0]
        row.update(segment_count=len(captures), available_device_count=len({
            d for s in captures for d in s['device_ids']}))
        return row

    def runs(self, query):
        start = date.fromisoformat(query['start_date'])
        end = date.fromisoformat(query['end_date'])
        if start.isoformat() != query['start_date'] or end.isoformat() != query['end_date']:
            raise APIError('INVALID_ARGUMENT', 'Dates must be YYYY-MM-DD')
        if not 0 <= (end - start).days <= 30:
            raise APIError('INVALID_ARGUMENT', 'Date range must be at most 31 days')
        result = {}
        for offset in range((end - start).days + 1):
            for row in self.v1.list_capture_runs((start + timedelta(days=offset)).isoformat()):
                captures = self.captures(row['request_id'])
                devices = {d for s in captures for d in s['device_ids']}
                if (not devices or query.get('device_id') and query['device_id'] not in devices
                        or query.get('status') and query['status'] != row['status']):
                    continue
                result[row['request_id']] = {**row, 'segment_count': len(captures),
                                              'available_device_count': len(devices)}
        return sorted(result.values(), key=lambda r: (r['first_capture_at'], r['request_id']), reverse=True)

    def health(self):
        health = self.v1.health()
        with self.repo.lock:
            pending = sum(j['status'] in ('accepted', 'running') for j in self.repo.state['jobs'].values())
            readiness = 'closed' if self.closed else 'busy' if pending >= 8 else 'ready'
        if readiness != 'ready' and health['status'] == 'ok':
            health['status'] = 'degraded'
        return {**health, 'api_version': 'v2', 'agent_v2': readiness}

    def devices(self, query):
        if query.get('acquisition_only', 'false') not in ('true', 'false'):
            raise APIError('INVALID_ARGUMENT')
        return [{'device_id': d.id, 'section': d.section,
                 'enabled': d.enabled, 'acquisition_enabled': d.acquisition_enabled}
                for d in self.registry.devices if
                (not query.get('section') or d.section == query['section']) and
                (query.get('acquisition_only') != 'true' or d.enabled and d.acquisition_enabled)]

    def raw(self, identity, *, include_deleted=False, version=None):
        target(identity)
        segment = self.segment(identity['request_id'], identity['capture_sequence'])
        if identity['device_id'] not in segment.device_ids:
            raise APIError('NOT_FOUND', 'Device is absent from this capture')
        entry = self._entry(identity)
        if entry['deleted'] and not include_deleted:
            raise APIError('NOT_FOUND', 'Object is soft deleted')
        if version is not None:
            matches = [v for v in entry['versions'] if v['version'] == version]
            if len(matches) != 1:
                raise APIError('NOT_FOUND', 'Version not found')
            entry = matches[0]
        if entry.get('file'):
            return self.snapshots.snapshot(self.repo.root / entry['file'], version=True,
                                           expected_hash=entry['sha256'])
        with self.v1._lock:
            pairs = self.backend._candidate_segment_file_pairs(
                identity['request_id'], identity['capture_sequence'], (identity['device_id'],))
        if len(pairs) != 1:
            raise APIError('AMBIGUOUS' if pairs else 'NOT_FOUND')
        return self.snapshots.snapshot(pairs[0][0], expected_hash=entry.get('sha256'))

    def _read(self, identity, **kwargs):
        blob = self.raw(identity, **kwargs)
        try:
            # Existing HDF5 reader accepts filesystem names, never an API path.
            with tempfile.NamedTemporaryFile(suffix='.h5') as file:
                while block := blob.stream.read(1024 * 1024):
                    file.write(block)
                file.flush()
                with h5py.File(file.name, 'r') as h5:
                    for name in ('BunchIDData', 'Data'):
                        if not isinstance(h5.get(name, getlink=True), h5py.HardLink):
                            raise APIError('DATA_UNREADABLE', 'External HDF5 links are unsupported')
                        dataset = h5[name]
                        if (not isinstance(dataset, h5py.Dataset) or dataset.size > 8_000_000
                                or dataset.is_virtual or dataset.external):
                            raise APIError('DATA_UNREADABLE', 'Unsupported or oversized HDF5 dataset')
                try:
                    series = read_single_device_hdf5(file.name)
                except (OSError, ValueError, KeyError, TypeError):
                    raise APIError('DATA_UNREADABLE', 'HDF5 cannot be decoded') from None
            if series.device_id != identity['device_id']:
                raise APIError('DATA_UNREADABLE', 'HDF5 identity differs from catalog')
            return series, {'target': identity, 'target_id': target(identity),
                            'version': self._entry(identity)['version'],
                            'sha256': blob.sha256, 'bytes': blob.size}
        finally:
            blob.stream.close()

    @staticmethod
    def public_entry(entry):
        return {**{k: deepcopy(v) for k, v in entry.items() if k not in ('file', 'versions')},
                'effect_scope': 'agent-v2-overlay', 'source_modified': False, 'source_authoritative': True}

    def versions(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise APIError('INVALID_ARGUMENT', 'Expected target_id from preview')
        with self.repo.lock:
            entry = self.repo.get('targets', key)
            if entry is not None:
                return [self.public_entry(v) for v in entry['versions']]
            # A successful preview already binds the opaque ID to an exact
            # catalog identity. Resolve it without creating mutation state.
            identities = [op['result']['proposal']['target']
                          for op in self.repo.state['operations'].values()
                          if op['status'] == 'succeeded'
                          and op['action'] == 'POST ' + PREFIX + '/data/preview'
                          and op['result'].get('target_id') == key]
            if not identities:
                raise APIError('NOT_FOUND')
            identity = identities[0]
            if any(value != identity for value in identities) or target(identity) != key:
                raise APIError('AMBIGUOUS', 'Target identity is not unique')
            # Hash and decode the current original object, not the preview's
            # historical snapshot; retain existing safe read errors.
            before = self._entry(identity)
        _, source = self._read(identity)
        with self.repo.lock:
            if self._entry(identity) != before:
                raise APIError('VERSION_CONFLICT')
        return [{**self.public_entry(before),
                 'sha256': source['sha256'], 'bytes': source['bytes']}]

    def operation(self, operation_id):
        row = self.repo.get('operations', uuid(operation_id))
        if row is None:
            raise APIError('NOT_FOUND')
        if not row['action'].startswith('POST ' + PREFIX + '/data/'):
            raise APIError('NOT_FOUND')
        if '/data/' in row['action']:
            row.update(effect_scope='agent-v2-overlay', source_modified=False, source_authoritative=True)
            if row.get('result', {}).get('supersedes_version') is not None:
                row['supersedes_version'] = row['result']['supersedes_version']
        return row

    def operations(self, scope, query):
        fields = ('operation_id', 'action', 'status', 'operator_id', 'approval_id',
                  'target_id', 'created_at', 'updated_at')
        with self.repo.lock:
            rows = deepcopy(list(self.repo.state['operations'].values()))
        result = []
        for row in rows:
            if not row['action'].startswith('POST ' + PREFIX + '/' + scope + '/'):
                continue
            parameters = row['parameters']
            try:
                target_id = target(parameters['target']) if 'target' in parameters else None
            except (APIError, ValueError, TypeError):
                # Failed input validation must not make the audit unreadable.
                target_id = None
            request_id = parameters.get('request_id', row.get('result', {}).get('request_id'))
            if scope == 'control' and row['action'].endswith('/cancel'):
                request_id = row['action'].split('/')[-2]
            if any(query.get(k) and query[k] != value for k, value in (
                    ('operator_id', row['operator_id']), ('action', row['action']),
                    ('target_id', target_id), ('request_id', request_id))):
                continue
            item = {field: row.get(field) for field in fields}
            item['target_id'] = target_id
            if row.get('error'):
                item['error'] = {'code': row['error']['code']}
            result.append(item)
        return sorted(result, key=lambda r: (r['created_at'], r['operation_id']), reverse=True)

    def post(self, scope, action, body, headers, execute):
        """Commit intent under the catalog lock; perform slow work outside it."""
        key = headers.get('Idempotency-Key') or body.get('idempotency_key')
        identifier(key)
        if body.get('idempotency_key', key) != key:
            raise APIError('INVALID_ARGUMENT', 'Idempotency header/body mismatch')
        binding = digest({'scope': scope, 'key': key})
        fingerprint = digest({'action': action, 'body': body})
        needs_approval = scope != 'read' and action != 'POST ' + PREFIX + '/data/preview'
        prior_id = self.repo.get('keys', binding)
        refs = (self.access.approval(scope, action, body, headers, allow_expired=bool(prior_id)) if needs_approval
                else {'operation_id': str(uuid4()), 'approval_id': None, 'operator_id': 'service:' + scope})
        with self.repo.lock:
            if self.closed:
                raise APIError('RESOURCE_BUSY')
            prior = self.repo.state['keys'].get(binding)
            if prior:
                op = self.repo.state['operations'][prior]
                if op['fingerprint'] != fingerprint:
                    raise APIError('IDEMPOTENCY_CONFLICT')
                if needs_approval and any(op[k] != refs[k] for k in refs):
                    raise APIError('APPROVAL_INVALID', 'Retry must retain original approval and operation references')
                if op['status'] == 'failed':
                    error = op['error']
                    raise APIError(error['code'], error['message'])
                result = deepcopy(op.get('result', op))
            else:
                if refs['operation_id'] in self.repo.state['operations']:
                    raise APIError('CONFLICT', 'Operation ID already exists')
                if refs['approval_id'] and refs['approval_id'] in self.repo.state['approvals']:
                    raise APIError('APPROVAL_INVALID', 'Approval already consumed')
                op = {**refs, 'action': action, 'parameters': deepcopy(body), 'fingerprint': fingerprint,
                      'status': 'running', 'created_at': now(), 'updated_at': now()}
                self.repo.state['operations'][refs['operation_id']] = op
                self.repo.state['keys'][binding] = refs['operation_id']
                if refs['approval_id']:
                    self.repo.state['approvals'][refs['approval_id']] = refs['operation_id']
                self.repo.save()
        if prior:
            if isinstance(result, dict) and result.get('job_id'):
                return self.job(result['job_id'])
            if action.endswith('/control/captures') and op['status'] == 'succeeded':
                return {**result, **self.capture_status(body['request_id'])}
            return result
        try:
            result = {**execute(op), **refs}
            if scope == 'data':
                result.update(effect_scope='agent-v2-overlay', source_modified=False, source_authoritative=True)
            with self.repo.lock:
                op.update(status='succeeded', result=result, updated_at=now())
                self.repo.save()
            return deepcopy(result)
        except Exception as exc:
            error = safe_error(exc)
            with self.repo.lock:
                op.update(status='failed', error=error.payload(refs['operation_id'])['error'], updated_at=now())
                self.repo.save()
            raise error from None

    def capture_status(self, request_id):
        reply = self.backend._candidate_transport().get_status(uuid(request_id))
        return self._capture_reply(reply)

    @staticmethod
    def _capture_reply(reply):
        if not reply.accepted:
            code = {'TASK_NOT_FOUND': 'NOT_FOUND', 'SERVICE_BUSY': 'RESOURCE_BUSY',
                    'REQUEST_ID_CONFLICT': 'CONFLICT', 'CANCEL_NOT_SUPPORTED': 'CAPABILITY_UNAVAILABLE',
                    'TIMING_FREQUENCY_UNAVAILABLE': 'CAPABILITY_UNAVAILABLE',
                    'TIMING_FREQUENCY_FAILED': 'TIMEOUT'}.get(reply.error_code, 'INVALID_ARGUMENT')
            raise APIError(code, 'Acquisition backend rejected request')
        # Backend messages may contain paths or exceptions; expose only codes.
        return {'request_id': reply.request_id, 'accepted': reply.accepted,
                'status': reply.status, 'backend_error_code': reply.error_code}

    def start(self, body):
        exact(body, ('request_id', 'frequency_hz', 'duration_seconds', 'area_ids', 'device_ids',
                     'operator_id', 'approval_id'), ('idempotency_key',))
        if not isinstance(body['area_ids'], list) or not isinstance(body['device_ids'], list):
            raise APIError('INVALID_ARGUMENT')
        request = CaptureRequest(PROTOCOL_VERSION, uuid(body['request_id']), 'start_capture',
                                 body['frequency_hz'], body['duration_seconds'],
                                 tuple(body['area_ids']), tuple(body['device_ids']))
        self.policy.validate(request)
        capture_interval_seconds(request.frequency_hz)
        transport = self.backend._candidate_transport()
        try:
            return self._capture_reply(transport.request(request))
        except TimeoutError:
            # The same request identity is the only recovery query; never resubmit.
            return self.capture_status(request.request_id)

    def cancel(self, request_id, body):
        exact(body, (), ('idempotency_key',))
        reply = self.backend._candidate_transport().cancel(uuid(request_id))
        return {**self._capture_reply(reply), 'cancellation_requested': reply.status in ('accepted', 'running')}

    def preview(self, body, *, records=None):
        exact(body, ('action', 'target', 'expected_version'),
              ('annotations', 'source', 'restore_version', 'idempotency_key'))
        proposal = {k: v for k, v in body.items() if k != 'idempotency_key'}
        if body['action'] not in ('update', 'soft-delete', 'restore', 'overwrite'):
            raise APIError('INVALID_ARGUMENT')
        key = target(body['target'])
        entry = self._entry(body['target'])
        if type(body['expected_version']) is not int or body['expected_version'] != entry['version']:
            raise APIError('VERSION_CONFLICT')
        allowed = {'update': {'annotations'}, 'soft-delete': set(),
                   'restore': {'restore_version'}, 'overwrite': {'source'}}[body['action']]
        if set(proposal) - {'action', 'target', 'expected_version'} != allowed:
            # Restore without a historical version simply clears the tombstone.
            if not (body['action'] == 'restore' and set(proposal) == {'action', 'target', 'expected_version'}):
                raise APIError('INVALID_ARGUMENT', 'Invalid action parameters')
        if 'annotations' in body:
            annotations = body['annotations']
            if (not isinstance(annotations, dict) or set(annotations) - {'label', 'comment', 'tags'}
                    or any(not isinstance(v, str) or len(v) > 2048 for k, v in annotations.items() if k != 'tags')
                    or 'tags' in annotations and (not isinstance(annotations['tags'], list)
                        or len(annotations['tags']) > 32 or any(not isinstance(v, str) or len(v) > 128 for v in annotations['tags']))):
                raise APIError('INVALID_ARGUMENT', 'Only label/comment/tags annotations are mutable')
        if entry['deleted'] and body['action'] != 'restore':
            raise APIError('CONFLICT', 'Restore deleted object before modifying')
        records = {} if records is None else records
        identities = [body['target']] + ([body['source']] if 'source' in body else [])
        with self.repo.lock:
            generations = [self._entry(identity) for identity in identities]
            if generations[0] != entry:
                raise APIError('VERSION_CONFLICT')
        for identity in identities:
            identity_key = target(identity)
            if identity_key not in records:
                records[identity_key] = self._read(identity, include_deleted=identity == body['target'])
        sources = [{'target': identity, 'sha256': records[target(identity)][1]['sha256']}
                   for identity in identities]
        if 'source' in body:
            target(body['source'])
            if body['source']['device_id'] != body['target']['device_id']:
                raise APIError('INVALID_ARGUMENT', 'Overwrite source must be the same device')
            source_run = self.run(body['source']['request_id'])
            target_run = self.run(body['target']['request_id'])
            if source_run['frequency_hz'] != target_run['frequency_hz']:
                raise APIError('ANALYSIS_INVALID', 'Overwrite source sampling rate differs')
            series, _ = records[target(body['source'])]
            if not all(np.issubdtype(np.asarray(a).dtype, np.integer) for a in (series.x, series.y)):
                raise APIError('CAPABILITY_UNAVAILABLE', 'Version writer requires production integer raw schema')
        if 'restore_version' in body:
            if type(body['restore_version']) is not int:
                raise APIError('INVALID_ARGUMENT')
            if not any(v['version'] == body['restore_version'] for v in entry['versions']):
                raise APIError('NOT_FOUND', 'Restore version not found')
        with self.repo.lock:
            if [self._entry(identity) for identity in identities] != generations:
                raise APIError('VERSION_CONFLICT')
            dependencies = sorted(j['job_id'] for j in self.repo.state['jobs'].values()
                                  if any(s['target'] == body['target'] for s in j.get('sources', [])))
        return {'effect_scope': 'agent-v2-overlay', 'source_modified': False, 'source_authoritative': True,
                **({'supersedes_version': entry['version']} if body['action'] == 'overwrite' else {}),
                'preview_id': str(uuid4()), 'proposal': proposal, 'target_id': key,
                'sources': sources, 'dependencies': dependencies,
                'impact': 'One Agent v2 object; existing artifacts and v1 original catalog remain retained',
                'recoverable': True, 'expires_at': time.time() + 900}

    def mutate(self, action, body, op):
        # Serialize data writers without blocking catalog readers or job status.
        with self.mutation_lock:
            return self._mutate(action, body, op)

    def _mutate(self, action, body, op):
        exact(body, ('target', 'expected_version', 'preview_id', 'confirm_target'),
              ('annotations', 'source', 'restore_version', 'idempotency_key'))
        key = target(body['target'])
        if body['confirm_target'] != key:
            raise APIError('APPROVAL_INVALID', 'Explicit target confirmation mismatch')
        proposal = {'action': action, **{k: v for k, v in body.items() if k not in
                    ('preview_id', 'confirm_target', 'idempotency_key')}}
        with self.repo.lock:
            operations = deepcopy(list(self.repo.state['operations'].values()))
        previews = [p['result'] for p in operations
                    if p['status'] == 'succeeded' and p['action'].endswith('/data/preview')
                    and p['result'].get('preview_id') == body['preview_id']]
        if len(previews) != 1 or previews[0]['proposal'] != proposal or previews[0]['expires_at'] <= time.time():
            raise APIError('APPROVAL_INVALID', 'Preview is absent, expired or differs from operation')
        records = {}
        current_preview = self.preview(proposal, records=records)
        if current_preview['sources'] != previews[0]['sources']:
            raise APIError('VERSION_CONFLICT', 'Source changed since preview')
        entry = self._entry(body['target'])
        if not entry['versions']:
            entry['sha256'] = previews[0]['sources'][0]['sha256']
            entry['versions'].append({k: deepcopy(v) for k, v in entry.items() if k != 'versions'})
        row = {k: deepcopy(v) for k, v in entry.items() if k != 'versions'}
        row.pop('supersedes_version', None)
        row.update(version=entry['version'] + 1, operation_id=op['operation_id'],
                   approval_id=op['approval_id'], operator_id=op['operator_id'], created_at=now())
        if action == 'update':
            row['annotations'] = {**row['annotations'], **body['annotations']}
        elif action == 'soft-delete':
            row['deleted'] = True
        elif action == 'restore':
            if 'restore_version' in body:
                old = next(v for v in entry['versions'] if v['version'] == body['restore_version'])
                for field in ('file', 'sha256', 'bytes', 'source'):
                    row.pop(field, None)
                    if field in old:
                        row[field] = old[field]
                row['annotations'] = deepcopy(old['annotations'])
                row['restored_from_version'] = old['version']
            row['deleted'] = False
        elif action == 'overwrite':
            series, source = records[target(body['source'])]
            row['supersedes_version'] = entry['version']
            if source['sha256'] != previews[0]['sources'][1]['sha256']:
                raise APIError('VERSION_CONFLICT')
            # An unpublished orphan must never block a later intentional operation.
            directory = self.repo.root / 'versions' / key / (str(row['version']) + '-' + op['operation_id'])
            directory.mkdir(parents=True, exist_ok=False)
            file = write_single_device_hdf5(directory / 'waveform.h5', body['target']['device_id'],
                ParsedWaveform(series.x, series.y, series.bunch_ids),
                metadata={**series.metadata, 'agent_source': source,
                          'operation_id': op['operation_id']})
            blob = self.snapshots.snapshot(file, version=True)
            try:
                row.update(file=str(file.relative_to(self.repo.root)), sha256=blob.sha256, bytes=blob.size,
                           source=source)
            finally:
                blob.stream.close()
            write_json(directory / 'manifest.json', {**self.public_entry(row), 'proposal': proposal})
        entry = {**row, 'versions': entry['versions']}
        entry['versions'].append(deepcopy(row))
        result = {**self.public_entry(row), **{k: op[k] for k in ('operation_id', 'approval_id', 'operator_id')}}
        with self.repo.lock:
            if self._entry(body['target'])['version'] != body['expected_version']:
                raise APIError('VERSION_CONFLICT')
            self.repo.state['targets'][key] = entry
            op.update(status='succeeded', result=result, updated_at=now())
            self.repo.save()
        return result

    def submit(self, kind, body, operation_id=None):
        with self.repo.lock:
            return self._submit(kind, body, operation_id)

    def _submit(self, kind, body, operation_id=None):
        if kind == 'alignment':
            exact(body, ('request_id', 'capture_sequence', 'device_ids'), ('parameters', 'idempotency_key'))
            AlignmentParameters(**body.get('parameters', {}))
            devices = body['device_ids']
        else:
            exact(body, ('request_id', 'capture_sequence', 'device_id', 'channel'), ('profile', 'idempotency_key'))
            if body['channel'] not in ('X', 'Y'):
                raise APIError('INVALID_ARGUMENT')
            AnalysisConfig.for_profile(body.get('profile', 'high_band'))
            devices = [body['device_id']]
        if not isinstance(devices, list) or not 1 <= len(devices) <= 64 or len(set(devices)) != len(devices):
            raise APIError('INVALID_ARGUMENT', 'Expected 1 to 64 unique devices')
        for device in devices:
            target({'request_id': body['request_id'], 'capture_sequence': body['capture_sequence'], 'device_id': device})
        if self.v1.jobs.availability() != 'ready' or sum(j['status'] in ('accepted', 'running')
                for j in self.repo.state['jobs'].values()) >= 8:
            raise APIError('RESOURCE_BUSY')
        job_id = str(uuid4())
        row = {'job_id': job_id, 'operation_id': operation_id, 'kind': kind, 'status': 'accepted', 'parameters': deepcopy(body),
               'created_at': now(), 'updated_at': now()}
        self.repo.state['jobs'][job_id] = row
        self.repo.save()
        self.worker.submit(self._execute, job_id)
        return deepcopy(row)

    def job(self, job_id):
        row = self.repo.get('jobs', uuid(job_id))
        if row is None:
            raise APIError('NOT_FOUND')
        return row

    def cancel_job(self, job_id, body):
        exact(body, (), ('idempotency_key',))
        row = self.job(job_id)
        if row['status'] in ('accepted', 'running'):
            self._update_job(job_id, cancellation_requested=True)
            if row.get('analysis_job_id'):
                self.v1.jobs.cancel(row['analysis_job_id'])
        return self.job(job_id)

    def _check_cancel(self, job_id):
        if self.job(job_id).get('cancellation_requested'):
            raise JobCancelled()

    def _update_job(self, job_id, **values):
        with self.repo.lock:
            self.repo.state['jobs'][job_id].update(**values, updated_at=now())
            self.repo.save()

    def _execute(self, job_id):
        try:
            self._update_job(job_id, status='running')
            self._check_cancel(job_id)
            row = self.job(job_id)
            body = row['parameters']
            devices = body.get('device_ids', [body.get('device_id')])
            segment = self.segment(body['request_id'], body['capture_sequence'])
            records, sources = [], []
            identities = [{'request_id': body['request_id'], 'capture_sequence': body['capture_sequence'],
                           'device_id': device} for device in devices]
            with self.repo.lock:
                before = [self._entry(identity) for identity in identities]
            try:
                for identity in identities:
                    self._check_cancel(job_id)
                    record, source = self._read(identity)
                    records.append(record)
                    sources.append(source)
                # Original catalogs have no immutable content generation. Rehash once
                # outside the lock, then compare overlay generations under the lock.
                for identity, source in zip(identities, sources):
                    blob = self.raw(identity)
                    try:
                        if blob.sha256 != source['sha256']:
                            raise APIError('VERSION_CONFLICT', 'Source changed during read')
                    finally:
                        blob.stream.close()
                with self.repo.lock:
                    if before != [self._entry(identity) for identity in identities]:
                        raise APIError('VERSION_CONFLICT', 'Overlay changed during read')
            except Exception:
                with self.repo.lock:
                    if before != [self._entry(identity) for identity in identities]:
                        raise APIError('VERSION_CONFLICT', 'Overlay changed during read') from None
                raise
            run = self.run(body['request_id'])
            self._update_job(job_id, sources=sources)
            rate = run['frequency_hz']
            try:
                for record in records:
                    _sampling_rate(record.metadata, rate)
            except (ValueError, TypeError):
                raise APIError('ANALYSIS_INVALID', 'HDF5 sampling rate conflicts with historical frequency') from None
            class Gateway:
                def find_files(self, query):
                    return [(str(i), r.device_id) for i, r in enumerate(records)]

                def read_series(self, pairs, reader):
                    return records, ()
            retrieval = RetrievalService(Gateway())
            query = StorageQuery('agent-catalog', segment.storage_date_local or 'capture', segment.time_sec,
                                 tuple(devices), segment.request_id, segment.capture_sequence,
                                 segment.captured_at.isoformat())
            prepared = retrieval.prepare(query)
            result = retrieval.align_prepared(prepared, AlignmentParameters(**body.get('parameters', {})))
            self._check_cancel(job_id)
            provenance = {'sources': sources, 'sample_rate_hz': rate,
                          'sample_rate_source': 'capture_run_summary',
                          'algorithm_version': 'bunch-id-alignment:' + self.alignment_source_hash[:16],
                          'algorithm_source_sha256': self.alignment_source_hash,
                          'parameters': asdict(result.parameters), 'created_at': now(),
                          'captured_at': segment.captured_at.isoformat()}
            directory = self.repo.root / 'artifacts' / job_id
            directory.mkdir(parents=True)
            if row['kind'] == 'alignment':
                write_alignment_v2(directory / 'alignment-v2.h5', result, metadata=provenance)
                artifacts = ['alignment-v2.h5']
                if result.valid_mask.all():
                    write_legacy_merged_hdf5(directory / 'alignment-legacy.h5', result)
                    artifacts.append('alignment-legacy.h5')
                summary = {'device_ids': list(result.device_ids), 'data_points': int(result.bunch_ids.size),
                           'quality': [asdict(q) for q in result.quality_report],
                           'skipped_devices': [{'device_id': s.device_id, 'reason': s.reason} for s in result.skipped_devices],
                           'provenance': provenance}
            else:
                if rate is None or not math.isfinite(rate) or rate <= 0:
                    raise APIError('ANALYSIS_INVALID', 'Historical sampling rate is missing')
                config = AnalysisConfig.for_profile(body.get('profile', 'high_band'))
                analysis_id = self.v1.jobs.submit(AnalysisRequest('alignment', config=config,
                    alignment=result, sample_rate_hz=rate, sample_rate_source='capture_run_summary'))
                self._update_job(job_id, analysis_job_id=analysis_id, sources=sources)
                while self.v1.jobs.get(analysis_id)['status'] in ('accepted', 'running'):
                    if self.job(job_id).get('cancellation_requested'):
                        self.v1.jobs.cancel(analysis_id)
                    time.sleep(.02)
                self._check_cancel(job_id)
                if self.v1.jobs.get(analysis_id)['status'] != 'succeeded':
                    raise APIError('ANALYSIS_INVALID', 'Analysis did not complete successfully')
                analysis = self.v1.jobs.result(analysis_id)
                channel = next(c for c in analysis.channels if c.channel == body['device_id'] + '_' + body['channel'])
                summary = {k: getattr(channel, k) for k in ('classification', 'display_status', 'reason',
                           'peak_hz', 'resolution_hz', 'sample_rate_hz', 'data_points', 'quality', 'warnings')}
                # The analysis manifest defines a spectrum version, not a
                # stable source-file set. Never label the alignment hash as
                # the spectrum implementation's source identity.
                provenance.pop('algorithm_source_sha256')
                provenance.update(algorithm_version=analysis.manifest['algorithm_version'], parameters=asdict(config))
                summary['provenance'] = provenance
                artifacts = sorted(ARTIFACTS)
            summary['artifact_refs'] = [{'name': a, 'uri': f'{PREFIX}/read/jobs/{job_id}/artifacts/{a}'} for a in artifacts]
            self._check_cancel(job_id)
            self._update_job(job_id, status='succeeded', result=summary, artifacts=artifacts, sources=sources)
        except JobCancelled:
            self._update_job(job_id, status='cancelled')
        except Exception as exc:
            error = safe_error(exc)
            self._update_job(job_id, status='failed', error=error.payload(self.job(job_id).get('operation_id'))['error'])

    def artifact(self, job_id, name):
        row = self.job(job_id)
        if row['status'] != 'succeeded':
            raise APIError('OPERATION_IN_PROGRESS' if row['status'] in ('accepted', 'running') else 'ANALYSIS_INVALID')
        if name not in row['artifacts']:
            raise APIError('NOT_FOUND')
        root = self.repo.root
        path = root / 'artifacts' / job_id / name
        if row['kind'] == 'spectrum':
            root = self.v1.jobs.output_root
            path = root / uuid(row['analysis_job_id']) / name
        # Reuse the same no-follow, bounded snapshot implementation for artifacts.
        from .agent_v2_storage import local_stream, Download
        with local_stream(path, (root,)) as (stream, size):
            if size > self.snapshots.max_bytes:
                raise APIError('INVALID_ARGUMENT', 'Artifact exceeds download limit')
            output = tempfile.TemporaryFile()
            hasher = hashlib.sha256()
            total = 0
            started = time.monotonic()
            try:
                while block := stream.read(1024 * 1024):
                    if time.monotonic() - started > self.snapshots.timeout:
                        raise APIError('TIMEOUT')
                    total += len(block)
                    if total > self.snapshots.max_bytes:
                        raise APIError('INVALID_ARGUMENT', 'Artifact exceeds download limit')
                    hasher.update(block)
                    output.write(block)
                output.seek(0)
                return Download(output, total, hasher.hexdigest(), name)
            except BaseException:
                output.close()
                raise


class JobCancelled(Exception):
    pass


def safe_error(exc):
    from .agent_service import AgentError
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, AgentError):
        return APIError('CAPABILITY_UNAVAILABLE' if exc.code == 'UNAVAILABLE' else exc.code, exc.reason)
    if isinstance(exc, TimeoutError):
        return APIError('TIMEOUT')
    if isinstance(exc, RuntimeError):
        detail = str(exc).lower()
        if 'duplicate' in detail or 'ambiguous' in detail:
            return APIError('AMBIGUOUS')
        for token, code in (('unavailable', 'CAPABILITY_UNAVAILABLE'), ('not_found', 'NOT_FOUND'),
                            ('invalid', 'DATA_UNREADABLE'), ('timeout', 'TIMEOUT')):
            if token in detail:
                return APIError(code)
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return APIError('INVALID_ARGUMENT')
    if isinstance(exc, FileNotFoundError):
        return APIError('NOT_FOUND')
    if isinstance(exc, OSError):
        return APIError('DATA_UNREADABLE')
    return APIError('INTERNAL', 'Waveform operation failed')
