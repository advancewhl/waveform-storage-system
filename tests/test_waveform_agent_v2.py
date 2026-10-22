"""Real loopback HTTP + Candidate ZMQ + HDF5 + analysis v2 acceptance tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from http.client import HTTPConnection
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread, current_thread
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

import h5py
import numpy as np

from test_waveform_agent_api import agent_fixture, ROOT, REQUEST_ID, DEVICE
from waveform_storage.acquisition.task import CaptureCancelled
from waveform_storage.application.agent_http_api import make_server
from waveform_storage.application.agent_v2_contract import AccessPolicy, action_hash, sign_approval, APIError
from waveform_storage.application.agent_v2_service import WaveformAgentV2
from waveform_storage.config.registry import load_registry
from waveform_storage.metadata.agent_version_repository import AgentVersionRepository

PREFIX = '/api/agent/v2'
IDENTITY = {'request_id': REQUEST_ID, 'capture_sequence': 3, 'device_id': DEVICE}


class FixtureRunner:
    def __init__(self):
        self.started = Event()
        self.cancelled = Event()
        self.release = Event()
        self.calls = 0

    def prepare(self):
        self.cancelled.clear()

    def cancel(self):
        self.cancelled.set()

    def __call__(self, request):
        self.calls += 1
        self.started.set()
        while not self.release.wait(.01):
            if self.cancelled.is_set():
                raise CaptureCancelled()
        return ()


@contextmanager
def v2_fixture(root, *, address=('127.0.0.1', 0), access=None, v2_only=None):
    runner = FixtureRunner()
    with agent_fixture(root, capture_runner=runner, capture_canceller=runner.cancel,
                       capture_prepare=runner.prepare) as (v1, repository):
        # Use the integer raw schema that the production writer accepts.
        for path in root.glob('segment-*.h5'):
            with h5py.File(path, 'a') as h5:
                values = np.rint(np.asarray(h5['Data']) * 10000).astype(np.int64)
                del h5['Data']
                h5.create_dataset('Data', data=values)
        policy = access or AccessPolicy({s: str(uuid4()) for s in ('read', 'control', 'data')},
                              control=True, data=True, secret=str(uuid4()),
                              rate_per_minute=0, write_rate_per_minute=0)
        api = WaveformAgentV2(v1, load_registry(ROOT / 'config/devices.probe47.json'), policy, roots=[root])
        server = make_server(v1, address, v2=api, v2_only=v2_only)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, *, scope='read', headers=None):
            connection = HTTPConnection(*server.server_address, timeout=20)
            actual = {'Authorization': 'Bearer ' + policy.tokens[scope], **(headers or {})}
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=actual)
            response = connection.getresponse()
            status, received, returned = response.status, response.read(), dict(response.getheaders())
            connection.close()
            payload = json.loads(received) if returned.get('Content-Type', '').startswith('application/json') else received
            return status, payload, returned
        request.server_address = server.server_address
        try:
            yield request, api, repository, runner
        finally:
            runner.release.set()
            server.shutdown()
            server.server_close()
            thread.join()
            api.close()


def approved(api, scope_name, route, body, *, key=None, refs=None, **overrides):
    refs = refs or {'operation_id': str(uuid4()), 'approval_id': str(uuid4()), 'operator_id': 'fixture-operator'}
    action = 'POST ' + PREFIX + '/' + scope_name + '/' + route
    payload = {**refs, 'action': action, 'action_hash': action_hash(action, body),
               'expires_at': time.time() + 120, 'scope': scope_name, **overrides}
    return {'X-Operation-Id': refs['operation_id'], 'X-Approval-Id': refs['approval_id'],
            'X-Operator-Id': refs['operator_id'], 'X-Approval-Token': sign_approval(payload, api.access.secret),
            'Idempotency-Key': key or str(uuid4())}


def wait_job(request, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        code, row, _ = request('GET', PREFIX + '/read/jobs/' + job_id)
        if code != 200:
            raise AssertionError((code, row))
        if row['status'] in ('succeeded', 'failed'):
            return row
        time.sleep(.02)
    raise AssertionError('fixture job timed out')


def record_evidence(section, value):
    destination = os.environ.get('WAVEFORM_V2_EVIDENCE_PATH')
    if destination:
        path = Path(destination)
        evidence = json.loads(path.read_text()) if path.exists() else {}
        evidence[section] = value
        path.write_text(json.dumps(evidence, indent=2))


class AgentV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = v2_fixture(self.root)
        self.request, self.api, self.repository, self.runner = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def post(self, scope, route, body, **kwargs):
        headers = approved(self.api, scope, route, body, **kwargs)
        return self.request('POST', PREFIX + '/' + scope + '/' + route, body, scope=scope, headers=headers)

    def preview(self, action, **extra):
        version = self.api._entry(IDENTITY)['version']
        proposal = {'action': action, 'target': IDENTITY, 'expected_version': version, **extra}
        code, preview, _ = self.request('POST', PREFIX + '/data/preview', proposal, scope='data',
                                        headers={'Idempotency-Key': str(uuid4())})
        self.assertEqual(code, 200, preview)
        body = {k: v for k, v in proposal.items() if k != 'action'}
        body.update(preview_id=preview['preview_id'], confirm_target=preview['target_id'])
        return body, preview

    def assert_error(self, response, status, code):
        self.assertEqual(response[0], status, response[1])
        self.assertEqual(response[1]['error']['code'], code)
        self.assertEqual(set(response[1]['error']), {'code', 'message', 'operation_id', 'retryable'})
        self.assertNotIn(str(self.root), json.dumps(response[1]))

    def test_v2_only_environment_and_startup_warning(self):
        with self.assertLogs('waveform_storage.application.agent_http_api', level='WARNING') as logs:
            server = make_server(self.api.v1, v2=self.api, address=('127.0.0.1', 0), v2_only=False)
            server.server_close()
        self.assertIn('v1 无认证，必须由网络 ACL/代理限制', ''.join(logs.output))
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'WAVEFORM_AGENT_V2_ONLY': 'true'}):
            with v2_fixture(Path(tmp)) as (request, _, _, _):
                self.assertEqual(request('GET', '/api/agent/v1/waveform/runs?date=2026-10-08')[0], 404)
                self.assertEqual(request('POST', '/api/agent/v1/waveform/spectrum', {})[0], 404)
                self.assertEqual(request('GET', '/health')[0], 200)
                self.assertEqual(request('GET', PREFIX + '/read/health')[0], 200)
        self.assertIn('WAVEFORM_AGENT_V2_ONLY', (ROOT / 'docs/agent-api-v2.md').read_text())

    def test_preview_without_approval_and_single_mutation_approval(self):
        before = deepcopy(self.api.repo.state['approvals'])
        body, preview = self.preview('update', annotations={'label': 'one-confirmation'})
        self.assertIsNone(preview['approval_id'])
        self.assertEqual(before, self.api.repo.state['approvals'])
        self.assertEqual(self.api.repo.state['targets'], {})
        headers = approved(self.api, 'data', 'update', body)
        response = self.request('POST', PREFIX + '/data/update', body, scope='data', headers=headers)
        self.assertEqual(response[0], 200, response)
        self.assertEqual(len(self.api.repo.state['approvals']), len(before) + 1)
        self.assertEqual(self.request('POST', PREFIX + '/data/update', body, scope='data', headers=headers)[1], response[1])
        self.assertEqual(len(self.api.repo.state['approvals']), len(before) + 1)
        record_evidence('single-approval', {'preview': preview, 'mutation': response[1]})

    def test_interrupted_download_closes_without_second_response(self):
        from waveform_storage.application.agent_v2_storage import Download
        class BrokenStream:
            closed = False
            reads = 0
            def read(self, size):
                self.reads += 1
                if self.reads == 1:
                    return b'first-block'
                raise OSError('private storage path must not escape')
            def close(self):
                self.closed = True
        stream = BrokenStream()
        from http.client import IncompleteRead
        blob = Download(stream, 1000, 'a' * 64, 'waveform.h5')
        with patch.object(self.api, 'raw', return_value=blob):
            connection = HTTPConnection(*self.request.server_address, timeout=3)
            connection.request('GET', PREFIX + '/read/requests/' + REQUEST_ID + '/captures/3/raw/' + DEVICE,
                               headers={'Authorization': 'Bearer ' + self.api.access.tokens['read']})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader('Content-Length'), '1000')
            self.assertEqual(response.getheader('X-Content-SHA256'), 'a' * 64)
            with self.assertRaises(IncompleteRead) as failure:
                response.read()
            self.assertEqual(failure.exception.partial, b'first-block')
            self.assertTrue(response.isclosed())
            connection.close()
        self.assertTrue(stream.closed)
        # Pre-header failure still uses the structured envelope.
        with patch.object(self.api, 'raw', side_effect=APIError('DATA_UNREADABLE')):
            self.assert_error(self.request('GET', PREFIX + '/read/requests/' + REQUEST_ID + '/captures/3/raw/' + DEVICE),
                              422, 'DATA_UNREADABLE')

    def test_slow_job_read_allows_status_and_detects_mutation(self):
        body, preview = self.preview('update', annotations={'comment': 'during-read'})
        entered, release = Event(), Event()
        original = self.api._read
        def slow(identity, **kwargs):
            result = original(identity, **kwargs)
            if current_thread().name.startswith('agent-v2'):
                entered.set()
                if not release.wait(5):
                    raise AssertionError('test reader was not released')
            return result
        with patch.object(self.api, '_read', side_effect=slow):
            response = self.request('POST', PREFIX + '/read/alignments',
                    {'request_id': REQUEST_ID, 'capture_sequence': 3, 'device_ids': [DEVICE]},
                    headers={'Idempotency-Key': str(uuid4())})
            self.assertEqual(response[0], 202, response)
            job = response[1]
            try:
                self.assertTrue(entered.wait(3))
                start = time.monotonic()
                self.assertEqual(self.request('GET', PREFIX + '/read/jobs/' + job['job_id'])[0], 200)
                self.assertEqual(self.request('GET', PREFIX + '/data/' + preview['operation_id'], scope='data')[0], 200)
                elapsed = time.monotonic() - start
                self.assertLess(elapsed, .75)
                update = self.post('data', 'update', body)
                self.assertEqual(update[0], 200, update)
            finally:
                release.set()
            result = wait_job(self.request, job['job_id'])
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(result['error']['code'], 'VERSION_CONFLICT')
            record_evidence('concurrent-read', {'query_elapsed_seconds': elapsed, 'job': result})

    def test_overlay_scope_and_original_v1_view(self):
        body, preview = self.preview('soft-delete')
        code, result, _ = self.post('data', 'soft-delete', body)
        self.assertEqual(code, 200, result)
        operation = self.request('GET', PREFIX + '/data/' + result['operation_id'], scope='data')[1]
        version_response = self.request('GET', PREFIX + '/data/' + preview['target_id'] + '/versions', scope='data')[1]
        versions = version_response['versions']
        for row in [preview, result, operation, version_response, *versions]:
            self.assertEqual(row['effect_scope'], 'agent-v2-overlay')
            self.assertIs(row['source_modified'], False)
            self.assertIs(row['source_authoritative'], True)
        self.assertEqual(self.request('GET', PREFIX + '/read/requests/' + REQUEST_ID + '/captures/3/raw/' + DEVICE)[0], 404)
        code, v1, _ = self.request('GET', '/api/agent/v1/waveform/requests/' + REQUEST_ID + '/captures/3')
        self.assertEqual(code, 200)
        self.assertIn(DEVICE, json.dumps(v1))
        self.assertTrue((self.root / 'segment-3.h5').exists())
        record_evidence('overlay', {'preview': preview, 'operation': operation, 'versions': versions, 'v1_status': code})

    def test_operation_lists_filter_page_sanitize_and_scope(self):
        body, preview = self.preview('update', annotations={'comment': 'must-not-appear-in-list'})
        update = self.post('data', 'update', body)[1]
        base = PREFIX + '/data/operations'
        code, first, _ = self.request('GET', base + '?limit=1', scope='data')
        self.assertEqual(code, 200, first)
        self.assertEqual(first['total'], 2)
        self.assertTrue(first['truncated'])
        second = self.request('GET', base + '?' + urlencode({'limit': 1, 'cursor': first['next_cursor']}), scope='data')[1]
        self.assertEqual(len(second['operations']), 1)
        self.assertNotEqual(first['operations'][0]['operation_id'], second['operations'][0]['operation_id'])
        for field, value in [('operator_id', 'fixture-operator'), ('target_id', preview['target_id']),
                             ('action', 'POST ' + PREFIX + '/data/update')]:
            rows = self.request('GET', base + '?' + urlencode({field: value}), scope='data')[1]
            self.assertEqual(rows['total'], 2 if field == 'target_id' else 1)
        serialized = json.dumps(first)
        for private in ('parameters', 'must-not-appear-in-list', 'fingerprint', 'secret', str(self.root), self.api.access.tokens['data']):
            self.assertNotIn(private, serialized)
        self.assert_error(self.request('GET', base), 403, 'FORBIDDEN')
        self.assertEqual(self.request('GET', PREFIX + '/control/operations', scope='control')[1]['total'], 0)
        self.assert_error(self.request('GET', PREFIX + '/control/operations', scope='data'), 403, 'FORBIDDEN')
        record_evidence('operations', first)
        # Invalid bodies and failed cancels remain discoverable in safe audit.
        bad = {'action': 'soft-delete', 'target': {}, 'expected_version': 0}
        self.assert_error(self.request('POST', PREFIX + '/data/preview', bad, scope='data',
                          headers={'Idempotency-Key': str(uuid4())}), 400, 'INVALID_ARGUMENT')
        failed = self.request('GET', base, scope='data')[1]['operations'][0]
        self.assertEqual(failed['error'], {'code': 'INVALID_ARGUMENT'})
        self.assertIsNone(failed['target_id'])
        missing = str(uuid4())
        self.assert_error(self.post('control', 'captures/' + missing + '/cancel', {}), 404, 'NOT_FOUND')
        rows = self.request('GET', PREFIX + '/control/operations?' + urlencode({'request_id': missing}), scope='control')[1]
        self.assertEqual(rows['total'], 1)
        self.assertEqual(rows['operations'][0]['error'], {'code': 'NOT_FOUND'})


    def test_http_rate_limits_scope_window_and_write_quota(self):
        stamp = [10.0]
        access = self.api.access
        access._clock = lambda: stamp[0]
        access._windows.clear()
        access.rate_per_minute = 2
        for _ in range(2):
            self.assertEqual(self.request('GET', PREFIX + '/read/health')[0], 200)
        self.assert_error(self.request('GET', PREFIX + '/read/health'), 429, 'RESOURCE_BUSY')
        self.assertEqual(self.request('GET', PREFIX + '/control/health', scope='control')[0], 200)
        stamp[0] += 60
        self.assertEqual(self.request('GET', PREFIX + '/read/health')[0], 200)
        access.rate_per_minute = 0
        access.write_rate_per_minute = 1
        self.assert_error(self.request('POST', PREFIX + '/control/captures', {}, scope='control',
                          headers={'Idempotency-Key': str(uuid4())}), 403, 'APPROVAL_REQUIRED')
        self.assert_error(self.request('POST', PREFIX + '/control/captures', {}, scope='control',
                          headers={'Idempotency-Key': str(uuid4())}), 429, 'RESOURCE_BUSY')

    def test_source_checksum_change_during_job_read_is_conflict(self):
        entered, release = Event(), Event()
        original = self.api._read
        def slow(identity, **kwargs):
            result = original(identity, **kwargs)
            entered.set()
            release.wait(5)
            return result
        with patch.object(self.api, '_read', side_effect=slow):
            response = self.request('POST', PREFIX + '/read/alignments',
                    {'request_id': REQUEST_ID, 'capture_sequence': 3, 'device_ids': [DEVICE]},
                    headers={'Idempotency-Key': str(uuid4())})
            self.assertEqual(response[0], 202)
            try:
                self.assertTrue(entered.wait(3))
                with h5py.File(self.root / 'segment-3.h5', 'a') as h5:
                    h5['Data'][0, 0] += 1
            finally:
                release.set()
            job = wait_job(self.request, response[1]['job_id'])
            self.assertEqual(job['status'], 'failed', job)
            self.assertEqual(job['error']['code'], 'VERSION_CONFLICT')

    def test_approval_previous_secret_rotation_window(self):
        body, _ = self.preview('update', annotations={'label': 'rotated'})
        headers = approved(self.api, 'data', 'update', body)
        configured = AccessPolicy.from_env({'WAVEFORM_AGENT_READ_TOKEN': 'test-read',
                  'WAVEFORM_AGENT_APPROVAL_SECRET': 'current', 'WAVEFORM_AGENT_APPROVAL_SECRET_PREVIOUS': 'previous'})
        self.assertEqual(configured.previous_secret, 'previous')
        self.assertEqual(configured.rate_per_minute, 60)
        self.assertEqual(configured.write_rate_per_minute, 10)
        old_secret = self.api.access.secret
        self.api.access.secret = str(uuid4())
        self.assert_error(self.request('POST', PREFIX + '/data/update', body, scope='data', headers=headers), 403, 'APPROVAL_INVALID')
        self.api.access.previous_secret = old_secret
        self.assertEqual(self.request('POST', PREFIX + '/data/update', body, scope='data', headers=headers)[0], 200)
        self.api.access.previous_secret = ''
        self.assert_error(self.request('POST', PREFIX + '/data/update', body, scope='data', headers=headers), 403, 'APPROVAL_INVALID')
        body, _ = self.preview('update', annotations={'label': 'current'})
        self.assertEqual(self.post('data', 'update', body)[0], 200)

    def test_catalog_schema_version_legacy_and_future_rejection(self):
        self.assertEqual(self.api.repo.state['schema_version'], 1)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = {'operations': {}, 'keys': {}, 'approvals': {}, 'targets': {}, 'jobs': {}}
            (root / 'catalog.json').write_text(json.dumps(legacy))
            repository = AgentVersionRepository(root)
            self.assertEqual(repository.state['schema_version'], 1)
            repository.close()
            self.assertEqual(json.loads((root / 'catalog.json').read_text())['schema_version'], 1)
            legacy['schema_version'] = 2
            original = json.dumps(legacy)
            (root / 'catalog.json').write_text(original)
            with self.assertRaisesRegex(ValueError, 'schema_version'):
                AgentVersionRepository(root)
            self.assertEqual((root / 'catalog.json').read_text(), original)

    def test_preview_mutation_snapshot_counts_and_overwrite_scope(self):
        original = self.api.snapshots.snapshot
        with patch.object(self.api.snapshots, 'snapshot', wraps=original) as snapshot:
            body, preview = self.preview('overwrite', source={**IDENTITY, 'capture_sequence': 0})
            self.assertEqual(snapshot.call_count, 2)
            result = self.post('data', 'overwrite', body)
            self.assertEqual(result[0], 200, result)
            # Two original reads in preview + two revalidation reads in mutation;
            # one additional hash reads the newly written local version.
            originals = [c for c in snapshot.call_args_list if not c.kwargs.get('version')]
            self.assertEqual(len(originals), 4)
            self.assertEqual(snapshot.call_count, 5)
            self.assertEqual(preview['supersedes_version'], 0)
            self.assertEqual(result[1]['supersedes_version'], 0)
        record_evidence('overwrite-counts', {'source_snapshots': len(originals), 'total_snapshots': 5, 'result': result[1]})
        with patch.object(self.api.snapshots, 'snapshot', wraps=original) as snapshot:
            body, _ = self.preview('overwrite', source=IDENTITY)
            self.assertEqual(snapshot.call_count, 1)
            self.assertEqual(self.post('data', 'overwrite', body)[0], 200)
            self.assertEqual(snapshot.call_count, 3)

    def test_read_runs_captures_devices_and_v1_compatibility(self):
        request = self.request
        code, runs, _ = request('GET', PREFIX + '/read/runs?start_date=2026-10-08&end_date=2026-10-08&device_id=' + DEVICE + '&status=failed')
        self.assertEqual(code, 200, runs)
        self.assertEqual(runs['total'], 1)
        self.assertFalse(runs['truncated'])
        self.assertIsNone(runs['next_cursor'])
        self.assertEqual(request('GET', PREFIX + '/read/runs/' + REQUEST_ID)[0], 200)
        path = PREFIX + '/read/requests/' + REQUEST_ID + '/captures'
        code, rows, _ = request('GET', path + '?limit=1')
        self.assertEqual([s['capture_sequence'] for s in rows['captures']], [0])
        self.assertTrue(rows['truncated'])
        self.assertEqual(rows['total'], 2)
        next_page = request('GET', path + '?limit=1&cursor=' + rows['next_cursor'])[1]
        self.assertEqual(next_page['captures'][0]['capture_sequence'], 3)
        self.assertEqual(request('GET', path + '/3')[1]['objects'][0]['version'], 0)
        self.assertEqual(request('GET', PREFIX + '/read/devices?section=INJ1&acquisition_only=true')[0], 200)
        self.assertEqual(request('GET', '/api/agent/v1/waveform/runs?date=2026-10-08', headers={'Authorization': ''})[0], 200)

    def test_auth_scope_isolation_and_default_disabled(self):
        for scope in ('read', 'control', 'data'):
            path = PREFIX + '/' + scope + ('/health' if scope != 'data' else '/' + str(uuid4()))
            self.assert_error(self.request('GET', path, scope=scope, headers={'Authorization': ''}), 401, 'UNAUTHORIZED')
            other = next(s for s in self.api.access.tokens if s != scope)
            self.assert_error(self.request('GET', path, scope=other), 403, 'FORBIDDEN')
        self.api.access.enabled['control'] = False
        self.assert_error(self.request('GET', PREFIX + '/control/health', scope='control'), 503, 'CAPABILITY_UNAVAILABLE')
        self.assert_error(self.request('GET', PREFIX + '/read/health', headers={'Authorization': 'Bearer invalid'}), 401, 'UNAUTHORIZED')
        self.assert_error(self.request('GET', PREFIX + '/read/health', headers={'Authorization': 'Bearer \u00e9'}), 401, 'UNAUTHORIZED')
        with self.assertRaises(ValueError):
            AccessPolicy({'read': 'same', 'control': 'same'})

    def test_raw_exact_identity_checksum_limits_and_escaping(self):
        path = PREFIX + '/read/requests/' + REQUEST_ID + '/captures/3/raw/' + DEVICE
        code, data, headers = self.request('GET', path)
        self.assertEqual(code, 200, data)
        self.assertEqual(hashlib.sha256(data).hexdigest(), headers['X-Content-SHA256'])
        self.assertEqual(len(data), int(headers['Content-Length']))
        self.assert_error(self.request('GET', path.replace('/3/', '/1/')), 404, 'NOT_FOUND')
        self.assert_error(self.request('GET', path.replace(DEVICE, 'unknown')), 404, 'NOT_FOUND')
        self.assert_error(self.request('GET', path + '/../../etc/passwd'), 400, 'INVALID_ARGUMENT')
        self.assert_error(self.request('GET', path.replace(DEVICE, '%2e%2e')), 400, 'INVALID_ARGUMENT')
        self.api.snapshots.max_bytes = 100
        self.assert_error(self.request('GET', path), 400, 'INVALID_ARGUMENT')
        self.api.snapshots.max_bytes = 64 * 1024 * 1024
        source = self.root / 'segment-3.h5'
        source.rename(self.root / 'saved.h5')
        source.symlink_to(self.root / 'saved.h5')
        self.assertNotEqual(self.request('GET', path)[0], 200)

    def test_approval_signature_scope_expiry_hash_and_replay(self):
        body, _ = self.preview('soft-delete')
        path = PREFIX + '/data/soft-delete'
        self.assert_error(self.request('POST', path, body, scope='data', headers={'Idempotency-Key': str(uuid4())}), 403, 'APPROVAL_REQUIRED')
        self.assert_error(self.request('POST', PREFIX + '/control/captures', {}, scope='control',
                          headers={'Idempotency-Key': str(uuid4())}), 403, 'APPROVAL_REQUIRED')
        for override in ({'scope': 'control'}, {'expires_at': time.time() - 1}, {'action_hash': 'bad'}):
            headers = approved(self.api, 'data', 'soft-delete', body, **override)
            self.assert_error(self.request('POST', path, body, scope='data', headers=headers), 403, 'APPROVAL_INVALID')
        headers = approved(self.api, 'data', 'soft-delete', body)
        headers['X-Approval-Token'] += 'broken'
        self.assert_error(self.request('POST', path, body, scope='data', headers=headers), 403, 'APPROVAL_INVALID')
        headers = approved(self.api, 'data', 'soft-delete', body)
        first = self.request('POST', path, body, scope='data', headers=headers)
        self.assertEqual(first[0], 200, first)
        self.assertEqual(self.request('POST', path, body, scope='data', headers=headers)[1], first[1])
        headers['Idempotency-Key'] = str(uuid4())
        headers['X-Operation-Id'] = str(uuid4())
        self.assert_error(self.request('POST', path, body, scope='data', headers=headers), 403, 'APPROVAL_INVALID')

    def test_data_preview_update_delete_restore_overwrite_and_versions(self):
        body, preview = self.preview('update', annotations={'label': 'reviewed', 'tags': ['fixture']})
        code, update, _ = self.post('data', 'update', body)
        self.assertEqual(code, 200, update)
        self.assertEqual(update['version'], 1)
        self.assertEqual(self.request('GET', PREFIX + '/data/' + update['operation_id'], scope='data')[1]['status'], 'succeeded')
        body, _ = self.preview('soft-delete')
        code, deleted, _ = self.post('data', 'soft-delete', body)
        self.assertEqual(code, 200, deleted)
        self.assertTrue(deleted['deleted'])
        raw = PREFIX + '/read/requests/' + REQUEST_ID + '/captures/3/raw/' + DEVICE
        self.assert_error(self.request('GET', raw), 404, 'NOT_FOUND')
        # v1 retains its original, read-only historical contract.
        self.assertEqual(self.request('GET', '/api/agent/v1/waveform/requests/' + REQUEST_ID + '/captures/3')[0], 200)
        body, _ = self.preview('restore')
        self.assertEqual(self.post('data', 'restore', body)[0], 200)
        original_bytes = (self.root / 'segment-3.h5').read_bytes()
        body, preview = self.preview('overwrite', source={**IDENTITY, 'capture_sequence': 0})
        code, overwritten, _ = self.post('data', 'overwrite', body)
        self.assertEqual(code, 200, overwritten)
        self.assertEqual(overwritten['version'], 4)
        self.assertEqual((self.root / 'segment-3.h5').read_bytes(), original_bytes)
        download = self.request('GET', raw)
        self.assertEqual(download[2]['X-Content-SHA256'], overwritten['sha256'])
        self.assertNotEqual(download[1], original_bytes)
        manifests = list((self.api.repo.root / 'versions').rglob('manifest.json'))
        self.assertEqual(len(manifests), 1)
        body, _ = self.preview('restore', restore_version=0)
        restored = self.post('data', 'restore', body)
        self.assertEqual(restored[0], 200)
        self.assertEqual(restored[1]['restored_from_version'], 0)
        self.assertNotIn('source', restored[1])
        self.assertEqual(self.request('GET', raw)[1], original_bytes)
        code, versions, _ = self.request('GET', PREFIX + '/data/' + preview['target_id'] + '/versions', scope='data')
        self.assertEqual(code, 200, versions)
        self.assertEqual(versions['total'], 6)
        record_evidence('data', {'updated': update, 'deleted': deleted, 'overwritten': overwritten,
                                'versions': versions, 'original_retained': True, 'restored_bytes_equal': True})

    def test_versions_after_preview_before_mutation_are_read_only(self):
        _, preview = self.preview('soft-delete')
        before = deepcopy(self.api.repo.state)
        catalog = (self.api.repo.root / 'catalog.json').read_bytes()
        original_catalog = deepcopy(self.repository.files)
        code, response, _ = self.request('GET', PREFIX + '/data/' + preview['target_id'] + '/versions', scope='data')
        self.assertEqual(code, 200, response)
        self.assertEqual(response['total'], 1)
        self.assertFalse(response['truncated'])
        self.assertIsNone(response['next_cursor'])
        version = response['versions'][0]
        self.assertEqual(version['target'], IDENTITY)
        self.assertEqual(version['target_id'], preview['target_id'])
        self.assertEqual(version['version'], 0)
        self.assertFalse(version['deleted'])
        self.assertEqual(version['annotations'], {})
        self.assertEqual(version['sha256'], hashlib.sha256((self.root / 'segment-3.h5').read_bytes()).hexdigest())
        self.assertNotIn('operation_id', version)
        self.assertNotIn('approval_id', version)
        self.assertEqual(self.api.repo.state, before)
        self.assertEqual((self.api.repo.root / 'catalog.json').read_bytes(), catalog)
        self.assertEqual(self.repository.files, original_catalog)

    def test_unmutated_versions_use_live_source_and_safe_read_errors(self):
        _, preview = self.preview('soft-delete')
        path = PREFIX + '/data/' + preview['target_id'] + '/versions'
        original_hash = preview['sources'][0]['sha256']
        with h5py.File(self.root / 'segment-3.h5', 'a') as h5:
            h5['Data'][0, 0] += 1
        code, response, _ = self.request('GET', path, scope='data')
        self.assertEqual(code, 200, response)
        live_hash = hashlib.sha256((self.root / 'segment-3.h5').read_bytes()).hexdigest()
        self.assertEqual(response['versions'][0]['sha256'], live_hash)
        self.assertNotEqual(live_hash, original_hash)
        self.assertEqual(self.api.repo.state['targets'], {})
        with h5py.File(self.root / 'segment-3.h5', 'a') as h5:
            h5.attrs['Device'] = 'wrong-device'
        self.assert_error(self.request('GET', path, scope='data'), 422, 'DATA_UNREADABLE')
        with h5py.File(self.root / 'segment-3.h5', 'a') as h5:
            h5.attrs['Device'] = DEVICE
        duplicate = next(deepcopy(r) for r in self.repository.files.values() if r['capture_sequence'] == 3)
        self.repository.files['duplicate'] = duplicate
        self.assert_error(self.request('GET', path, scope='data'), 409, 'AMBIGUOUS')
        del self.repository.files['duplicate']
        (self.root / 'segment-3.h5').unlink()
        self.assert_error(self.request('GET', path, scope='data'), 404, 'NOT_FOUND')
        self.assert_error(self.request('GET', PREFIX + '/data/' + '0' * 64 + '/versions', scope='data'), 404, 'NOT_FOUND')
        self.assertEqual(self.api.repo.state['targets'], {})

    def test_openapi_approval_and_provenance_contract(self):
        spec = json.loads((ROOT / 'docs/agent-api-v2.openapi.json').read_text())
        read_posts = []
        for path, methods in spec['paths'].items():
            if 'post' not in methods:
                continue
            operation = methods['post']
            scope = path.split('/')[4]
            headers = {p['name']: p for p in operation['parameters'] if p['in'] == 'header'}
            self.assertEqual(operation['security'], [{scope.title() + 'Token': []}])
            self.assertTrue(headers['Idempotency-Key']['required'])
            approval_headers = {'X-Operation-Id', 'X-Approval-Id', 'X-Operator-Id', 'X-Approval-Token'}
            if scope == 'read' or path.endswith('/data/preview'):
                if scope == 'read':
                    read_posts.append(path)
                self.assertFalse(approval_headers & headers.keys())
            else:
                self.assertTrue(all(headers[h]['required'] for h in approval_headers))
        self.assertEqual(set(read_posts), {PREFIX + '/read/alignments', PREFIX + '/read/spectrum',
                                         PREFIX + '/read/jobs/{job_id}/cancel'})
        provenance = spec['components']['schemas']['Provenance']
        self.assertNotIn('algorithm_source_sha256', provenance['required'])
        self.assertIn('spectrum', provenance['properties']['algorithm_source_sha256']['description'])

    def test_data_preview_target_parameter_and_version_conflicts(self):
        body, _ = self.preview('update', annotations={'comment': 'first'})
        changed = {**body, 'annotations': {'comment': 'changed'}}
        self.assert_error(self.post('data', 'update', changed), 403, 'APPROVAL_INVALID')
        self.assert_error(self.post('data', 'update', {**body, 'confirm_target': 'wrong'}), 403, 'APPROVAL_INVALID')
        self.assertEqual(self.post('data', 'update', body)[0], 200)
        self.assert_error(self.post('data', 'update', body), 409, 'VERSION_CONFLICT')
        self.assert_error(self.post('data', 'preview', {'action': 'update', 'target': IDENTITY,
                          'expected_version': 1, 'annotations': {'frequency_hz': '500'}}), 400, 'INVALID_ARGUMENT')

    def test_data_same_target_concurrency_has_single_commit(self):
        body, _ = self.preview('update', annotations={'label': 'concurrent'})
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.post('data', 'update', body), range(2)))
        self.assertEqual(sorted(r[0] for r in results), [200, 409], results)
        self.assertEqual(self.api._entry(IDENTITY)['version'], 1)

    def test_all_post_idempotency_and_conflict(self):
        body = {'request_id': REQUEST_ID, 'capture_sequence': 3, 'device_ids': [DEVICE]}
        path = PREFIX + '/read/alignments'
        headers = {'Idempotency-Key': str(uuid4())}
        code, first, _ = self.request('POST', path, body, headers=headers)
        self.assertEqual(code, 202, first)
        self.assertEqual(self.request('POST', path, body, headers=headers)[1]['job_id'], first['job_id'])
        self.assert_error(self.request('POST', path, {**body, 'capture_sequence': 0}, headers=headers), 409, 'IDEMPOTENCY_CONFLICT')
        data_body = {'action': 'soft-delete', 'target': IDENTITY, 'expected_version': 0}
        key = str(uuid4())
        code, _, _ = self.post('data', 'preview', data_body, key=key)
        self.assertEqual(code, 200)
        self.assert_error(self.post('data', 'preview', {**data_body, 'expected_version': 1}, key=key), 409, 'IDEMPOTENCY_CONFLICT')

    def test_alignment_spectrum_artifacts_real_http_e2e(self):
        evidence = {'fixture': True, 'production_verified': False}
        health = self.request('GET', PREFIX + '/read/health')
        self.assertEqual(health[0], 200)
        evidence['health'] = health[1]
        alignment = {'request_id': REQUEST_ID, 'capture_sequence': 3, 'device_ids': [DEVICE],
                     'parameters': {'mode': 'strict'}}
        code, accepted, _ = self.request('POST', PREFIX + '/read/alignments', alignment,
                                       headers={'Idempotency-Key': str(uuid4())})
        self.assertEqual(code, 202, accepted)
        row = wait_job(self.request, accepted['job_id'])
        self.assertEqual(row['status'], 'succeeded', row)
        from waveform_storage.processing import alignment as alignment_module
        alignment_hash = hashlib.sha256(Path(alignment_module.__file__).read_bytes()).hexdigest()
        self.assertEqual(row['result']['provenance']['algorithm_version'], 'bunch-id-alignment:' + alignment_hash[:16])
        self.assertEqual(row['result']['provenance']['algorithm_source_sha256'], alignment_hash)
        evidence['alignment'] = row
        for fmt in ('legacy', 'v2'):
            code, binary, headers = self.request('GET', PREFIX + '/read/alignments/' + row['job_id'] + '/export?format=' + fmt)
            self.assertEqual(code, 200, binary)
            self.assertEqual(hashlib.sha256(binary).hexdigest(), headers['X-Content-SHA256'])
        self.assertEqual(self.request('GET', PREFIX + '/read/alignments/' + row['job_id'] + '/result')[1]['data_points'], 20000)
        spectrum = {**IDENTITY, 'channel': 'X'}
        code, accepted, _ = self.request('POST', PREFIX + '/read/spectrum', spectrum,
                                       headers={'Idempotency-Key': str(uuid4())})
        self.assertEqual(code, 202, accepted)
        row = wait_job(self.request, accepted['job_id'])
        self.assertEqual(row['status'], 'succeeded', row)
        self.assertAlmostEqual(row['result']['peak_hz'], 47.6, places=3)
        from waveform_storage.analysis.engine import ALGORITHM_VERSION
        self.assertEqual(row['result']['provenance']['algorithm_version'], ALGORITHM_VERSION)
        self.assertEqual(row['result']['provenance']['algorithm_version'],
                         self.api.v1.jobs.result(row['analysis_job_id']).manifest['algorithm_version'])
        self.assertNotIn('algorithm_source_sha256', row['result']['provenance'])
        evidence['spectrum'] = row
        for name in ('results.zip', 'results.json', 'spectra.npz', 'run-manifest.json'):
            code, binary, headers = self.request('GET', PREFIX + '/read/jobs/' + row['job_id'] + '/artifacts/' + name)
            self.assertEqual(code, 200)
            self.assertEqual(hashlib.sha256(binary).hexdigest(), headers['X-Content-SHA256'])
        self.assert_error(self.request('GET', PREFIX + '/read/jobs/' + row['job_id'] + '/artifacts/owner.lock'), 404, 'NOT_FOUND')
        record_evidence('read_e2e', evidence)

    def test_control_start_status_cancel_duplicate_request_and_limits(self):
        request_id = str(uuid4())
        refs = {'operation_id': str(uuid4()), 'approval_id': str(uuid4()), 'operator_id': 'fixture-operator'}
        body = {'request_id': request_id, 'frequency_hz': 1000, 'duration_seconds': 1,
                'area_ids': ['INJ1'], 'device_ids': [DEVICE],
                'operator_id': refs['operator_id'], 'approval_id': refs['approval_id']}
        headers = approved(self.api, 'control', 'captures', body, refs=refs)
        path = PREFIX + '/control/captures'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.request('POST', path, body, scope='control', headers=headers), range(2)))
        self.assertTrue(all(r[0] == 202 for r in results), results)
        self.assertTrue(self.runner.started.wait(2))
        self.assertEqual(self.runner.calls, 1)
        self.assertEqual(self.request('GET', path + '/' + request_id, scope='control')[1]['status'], 'running')
        # New HTTP operation, same business request identity: no second runner.
        refs2 = {**refs, 'operation_id': str(uuid4()), 'approval_id': str(uuid4())}
        body2 = {**body, 'approval_id': refs2['approval_id']}
        self.assertEqual(self.post('control', 'captures', body2, refs=refs2)[0], 202)
        self.assertEqual(self.runner.calls, 1)
        code, cancel, _ = self.post('control', 'captures/' + request_id + '/cancel', {})
        listing = self.request('GET', PREFIX + '/control/operations?' + urlencode({'request_id': request_id, 'limit': 1}), scope='control')[1]
        self.assertGreaterEqual(listing['total'], 2)
        self.assertTrue(listing['truncated'])
        self.assertEqual(listing['operations'][0]['action'], 'POST ' + PREFIX + '/control/captures/' + request_id + '/cancel')
        self.assertNotIn('parameters', json.dumps(listing))
        self.assert_error(self.request('GET', PREFIX + '/data/' + listing['operations'][0]['operation_id'], scope='data'), 404, 'NOT_FOUND')

        self.assertEqual(code, 202, cancel)
        self.assertIn(cancel['status'], ('running', 'accepted'))
        self.assertTrue(cancel['cancellation_requested'])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.request('GET', path + '/' + request_id, scope='control')[1]['status']
            if status == 'cancelled':
                break
            time.sleep(.01)
        self.assertEqual(status, 'cancelled')
        record_evidence('control', {'start': results[0][1], 'cancel_reply': cancel,
                                   'terminal_status': status, 'runner_calls': self.runner.calls,
                                   'backend': 'synthetic fixture runner via persistent Candidate executor'})
        for change in ({'duration_seconds': 4000}, {'frequency_hz': 999}, {'device_ids': ['unknown']}, {'area_ids': ['L1']}):
            self.assertEqual(self.post('control', 'captures', {**body2, **change}, refs=refs2)[0], 409)
            # Fresh references reach server-side policy validation.
            refs3 = {**refs, 'operation_id': str(uuid4()), 'approval_id': str(uuid4())}
            invalid = {**body, **change, 'request_id': str(uuid4()), 'approval_id': refs3['approval_id']}
            self.assert_error(self.post('control', 'captures', invalid, refs=refs3), 400, 'INVALID_ARGUMENT')
        self.assertEqual(self.request('GET', PREFIX + '/control/health', scope='control')[0], 200)
        self.assertEqual(self.request('GET', PREFIX + '/control/timing-frequency', scope='control')[1]['frequency_hz'], 1000)

    def test_restart_journal_preserves_versions_and_never_replays(self):
        body, _ = self.preview('update', annotations={'comment': 'durable'})
        self.assertEqual(self.post('data', 'update', body)[0], 200)
        root = self.api.repo.root
        with self.api.repo.lock:
            self.api.repo.state['jobs']['unfinished'] = {'status': 'accepted', 'job_id': 'unfinished'}
            self.api.repo.save()
        self.api.repo.close()
        recovered = AgentVersionRepository(root)
        try:
            self.assertEqual(recovered.state['jobs']['unfinished']['status'], 'failed')
            self.assertEqual(next(iter(recovered.state['targets'].values()))['version'], 1)
            self.assertEqual(self.runner.calls, 0)
        finally:
            recovered.close()

    def test_bounded_analysis_queue_and_errors(self):
        with self.api.repo.lock:
            for i in range(8):
                self.api.repo.state['jobs'][str(i)] = {'status': 'accepted'}
        body = {'request_id': REQUEST_ID, 'capture_sequence': 3, 'device_ids': [DEVICE]}
        self.assert_error(self.request('POST', PREFIX + '/read/alignments', body,
                          headers={'Idempotency-Key': str(uuid4())}), 503, 'RESOURCE_BUSY')
        self.assertEqual(self.request('GET', PREFIX + '/read/health')[1]['agent_v2'], 'busy')
        with self.api.repo.lock:
            self.api.repo.state['jobs'].clear()
        self.assert_error(self.request('GET', PREFIX + '/read/runs?start_date=bad&end_date=bad'), 400, 'INVALID_ARGUMENT')
        self.assert_error(self.request('POST', PREFIX + '/read/spectrum', {'paths': ['/etc/passwd']},
                          headers={'Idempotency-Key': str(uuid4())}), 400, 'INVALID_ARGUMENT')
        with self.assertRaises(APIError):
            self.api.snapshots.snapshot(self.root.parent / 'outside.h5')

    def test_acquisition_restart_via_http_does_not_replay(self):
        from waveform_storage.acquisition.server import JsonTaskStore, PersistentTaskExecutor, CandidateAcquisitionServer
        from waveform_storage.acquisition.protocol import CaptureRequest, CaptureResponse, PROTOCOL_VERSION
        request_id = str(uuid4())
        store = JsonTaskStore(self.root / 'interrupted-capture')
        capture = CaptureRequest(PROTOCOL_VERSION, request_id, 'start_capture', 1000, 1, ('INJ1',), (DEVICE,))
        store.create(capture, CaptureResponse(PROTOCOL_VERSION, request_id, True, 'accepted', 'fixture'))
        restarted = PersistentTaskExecutor(JsonTaskStore(store.directory), self.runner)
        endpoint = 'inproc://v2-restart-' + str(uuid4())
        backend_server = CandidateAcquisitionServer(endpoint, self.api.policy, restarted, frequency_reader=lambda: 1000)
        stop = Event()
        thread = Thread(target=backend_server.serve_forever, args=(stop,), daemon=True)
        thread.start()
        previous = self.api.backend.candidate_endpoint
        self.api.backend.candidate_endpoint = endpoint
        try:
            code, row, _ = self.request('GET', PREFIX + '/control/captures/' + request_id, scope='control')
            self.assertEqual(code, 200, row)
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(row['backend_error_code'], 'INTERRUPTED_BY_RESTART')
            refs = {'operation_id': str(uuid4()), 'approval_id': str(uuid4()), 'operator_id': 'fixture-operator'}
            body = {'request_id': request_id, 'frequency_hz': 1000, 'duration_seconds': 1,
                    'area_ids': ['INJ1'], 'device_ids': [DEVICE],
                    'approval_id': refs['approval_id'], 'operator_id': refs['operator_id']}
            code, row, _ = self.post('control', 'captures', body, refs=refs)
            self.assertEqual(code, 202, row)
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(self.runner.calls, 0)
        finally:
            self.api.backend.candidate_endpoint = previous
            stop.set()
            thread.join(3)
            backend_server.close()
            restarted.close()

    def test_preview_expiry_change_in_source_and_checksum_mismatch(self):
        body, preview = self.preview('soft-delete')
        with h5py.File(self.root / 'segment-3.h5', 'a') as h5:
            h5['Data'][0, 0] += 1
        self.assert_error(self.post('data', 'soft-delete', body), 409, 'VERSION_CONFLICT')
        body, preview = self.preview('update', annotations={'comment': 'test'})
        with self.api.repo.lock:
            for row in self.api.repo.state['operations'].values():
                if row.get('result', {}).get('preview_id') == preview['preview_id']:
                    row['result']['expires_at'] = 0
        self.assert_error(self.post('data', 'update', body), 403, 'APPROVAL_INVALID')
        with self.assertRaises(APIError) as raised:
            self.api.snapshots.snapshot(self.root / 'segment-3.h5', expected_hash='bad')
        self.assertEqual(raised.exception.code, 'DATA_UNREADABLE')

    def test_hdf5_external_links_are_not_followed_by_analysis(self):
        path = self.root / 'segment-3.h5'
        with h5py.File(path, 'a') as h5:
            del h5['Data']
            h5['Data'] = h5py.ExternalLink(str(self.root / 'segment-0.h5'), '/Data')
        body = {**IDENTITY, 'channel': 'X'}
        code, job, _ = self.request('POST', PREFIX + '/read/spectrum', body,
                                   headers={'Idempotency-Key': str(uuid4())})
        self.assertEqual(code, 202, job)
        row = wait_job(self.request, job['job_id'])
        self.assertEqual(row['status'], 'failed', row)
        self.assertEqual(row['error']['code'], 'DATA_UNREADABLE')

    def test_job_cancellation_is_idempotent_and_reports_actual_state(self):
        body = {**IDENTITY, 'channel': 'X'}
        code, job, _ = self.request('POST', PREFIX + '/read/spectrum', body,
                                   headers={'Idempotency-Key': str(uuid4())})
        self.assertEqual(code, 202)
        path = PREFIX + '/read/jobs/' + job['job_id'] + '/cancel'
        headers = {'Idempotency-Key': str(uuid4())}
        code, state, _ = self.request('POST', path, {}, headers=headers)
        self.assertEqual(code, 200, state)
        self.assertIn(state['status'], ('accepted', 'running', 'succeeded', 'cancelled'))
        self.assertEqual(self.request('POST', path, {}, headers=headers)[1]['job_id'], job['job_id'])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = self.request('GET', PREFIX + '/read/jobs/' + job['job_id'])[1]
            if state['status'] in ('cancelled', 'succeeded', 'failed'):
                break
            time.sleep(.02)
        self.assertIn(state['status'], ('cancelled', 'succeeded'), state)

    def test_analysis_sampling_rate_conflict_is_explicit(self):
        with h5py.File(self.root / 'segment-3.h5', 'a') as h5:
            h5.attrs['frequency_hz'] = 500
        code, job, _ = self.request('POST', PREFIX + '/read/spectrum', {**IDENTITY, 'channel': 'X'},
                                   headers={'Idempotency-Key': str(uuid4())})
        self.assertEqual(code, 202, job)
        state = wait_job(self.request, job['job_id'])
        self.assertEqual(state['error']['code'], 'ANALYSIS_INVALID', state)


if __name__ == '__main__':
    unittest.main()
