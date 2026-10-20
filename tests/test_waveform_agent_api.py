"""Real catalog -> Candidate -> alignment -> analysis -> Agent HTTP regression."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch
from uuid import uuid4
import json
import os
import sys
import time
import unittest

import h5py

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/operator_gui'))
from candidate_backend import CandidateOperatorBackend
from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer, JsonTaskStore, PersistentTaskExecutor, RegistryPolicy,
)
from waveform_storage.analysis.service import AnalysisJobs
from waveform_storage.application.agent_service import (
    AgentError, WaveformAgentService, HTTP_STATUS, translate_error,
)
from waveform_storage.application.agent_http_api import make_server
from waveform_storage.config.registry import load_registry
from waveform_storage.config.settings import AppSettings
from waveform_storage.metadata.memory_repository import InMemoryMetadataRepository
from waveform_storage.metadata.models import CaptureTaskMetadata, WaveformFileMetadata
from waveform_storage.metadata.service import CaptureMetadataService
from test_spectrum_analysis import hdf_fixture

REQUEST_ID = 'c4577407-3528-486f-87df-6290da28e00d'
DEVICE = 'AC_LA_INJ1_BI_SBPM01'


@contextmanager
def agent_fixture(root):
    registry = load_registry(ROOT / 'config/devices.probe47.json')
    repository = InMemoryMetadataRepository()
    now = datetime(2026, 10, 8, 8, tzinfo=timezone.utc)
    repository.upsert_task(CaptureTaskMetadata(
        REQUEST_ID, 1000, 600, ('BI',), (DEVICE, 'AC_LA_INJ1_BI_SBPM02'),
        'fixture-registry', 1, status='failed', created_at=now))
    for sequence in (0, 3):
        path = root / f'segment-{sequence}.h5'
        hdf_fixture(path, kind='single')
        with h5py.File(path, 'a') as h5:
            h5.attrs['Device'] = DEVICE
        repository.upsert_waveform_file(WaveformFileMetadata(
            REQUEST_ID, DEVICE, 'PV:FIXTURE', now + timedelta(seconds=sequence),
            '2026-10-08', f'16000{sequence}', 'Asia/Shanghai', 'fixture', str(path),
            20000, (100000007, 119999007), (2, 20000), path.stat().st_size,
            capture_sequence=sequence))
    endpoint = 'inproc://agent-fixture-' + str(uuid4())
    def reject_capture(_request):
        raise AssertionError('Agent must never acquire raw data')
    tasks = PersistentTaskExecutor(JsonTaskStore(root / 'state'), reject_capture)
    server = CandidateAcquisitionServer(
        endpoint, RegistryPolicy(registry), tasks,
        metadata=CaptureMetadataService(repository, registry, ROOT / 'config/devices.probe47.json'),
        frequency_reader=lambda: 1000)
    stop = Event()
    thread = Thread(target=server.serve_forever, args=(stop,), daemon=True)
    thread.start()
    jobs = AnalysisJobs(root / 'derived')
    try:
        with patch.dict(os.environ, {'WAVEFORM_CANDIDATE_ZMQ_ENDPOINT': endpoint,
                                     'WAVEFORM_CANDIDATE_LOCAL_FILES': '1'}):
            backend = CandidateOperatorBackend(AppSettings.from_env(project_root=ROOT))
            service = WaveformAgentService(backend, jobs)
            try:
                yield service, repository
            finally:
                service.shutdown()
    finally:
        jobs.shutdown()
        stop.set()
        thread.join(timeout=3)
        server.close()
        tasks.close()


def poll(service, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = service.get_job(job_id)
        if state['status'] in ('succeeded', 'failed', 'cancelled'):
            return state
        time.sleep(.01)
    raise AssertionError('analysis timed out')


PREFIX = '/api/agent/v1/waveform'


def spectrum_payload(**overrides):
    return {**dict(request_id=REQUEST_ID, capture_sequence=3, device_id=DEVICE,
                   channel='X', profile='high_band', idempotency_key=str(uuid4())), **overrides}


@contextmanager
def http_fixture(service):
    server = make_server(service, ('127.0.0.1', 0))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method, path, payload=None):
        connection = HTTPConnection(*server.server_address, timeout=5)
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None)
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data
    try:
        yield request
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def http_poll(request, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status, body = request('GET', f'{PREFIX}/jobs/{job_id}')
        if status != 200:
            raise AssertionError((status, body))
        state = json.loads(body)
        if state['status'] in ('succeeded', 'failed', 'cancelled'):
            return state
        time.sleep(.01)
    raise AssertionError('job timeout')


class AgentV1Tests(unittest.TestCase):
    def assert_error(self, response, status, code):
        actual_status, body = response
        self.assertEqual(actual_status, status, body)
        error = json.loads(body)['error']
        self.assertEqual(error['code'], code, error)
        self.assertEqual(set(error['diagnostics']), {'error_code', 'reason', 'detail'})
        for secret in ('/home', '/SHINE_Data', 'mongodb://', 'password=', 'Traceback', 'SECRET_TOKEN'):
            self.assertNotIn(secret, body.decode())

    def test_complete_v1_catalog_to_peak_download_and_provenance(self):
        from io import BytesIO
        from zipfile import ZipFile
        import numpy as np
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, repo), http_fixture(service) as request:
            originals = {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
                         for p in Path(directory).glob('*.h5')}
            with patch.object(repo, 'upsert_task', side_effect=AssertionError('metadata write')), patch.object(
                    repo, 'upsert_waveform_file', side_effect=AssertionError('metadata write')), patch.object(
                    service.backend, 'start_capture', side_effect=AssertionError('capture write')), patch.object(
                    service.backend, 'get_timing_frequency', side_effect=AssertionError('live timing')):
                status, body = request('GET', f'{PREFIX}/runs?date=2026-10-08')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['runs'][0]['request_id'], REQUEST_ID)
                status, body = request('GET', f'{PREFIX}/requests/{REQUEST_ID}/captures')
                self.assertEqual(status, 200)
                self.assertEqual([s['capture_sequence'] for s in json.loads(body)['captures']], [0, 3])
                status, body = request('GET', f'{PREFIX}/requests/{REQUEST_ID}/captures/3')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['device_ids'], [DEVICE])
                payload = spectrum_payload()
                status, body = request('POST', f'{PREFIX}/spectrum', payload)
                self.assertEqual(status, 202)
                accepted = json.loads(body)
                self.assertEqual(accepted['status'], 'accepted')
                self.assertIsNone(accepted['analysis_job_id'])
                state = http_poll(request, accepted['job_id'])
                self.assertEqual(state['status'], 'succeeded', state)
                result = state['result']
                expected = dict(request_id=REQUEST_ID, capture_sequence=3, device_id=DEVICE,
                                channel='X', sample_rate_hz=1000, sample_rate_source='capture_run_summary',
                                captured_at='2026-10-08T08:00:03+00:00', storage_date_local='2026-10-08',
                                data_points=20000, classification='stable_candidate', display_status='clear_peak',
                                block_hits=4, block_count=4)
                for key, value in expected.items():
                    self.assertEqual(result[key], value, key)
                self.assertAlmostEqual(result['peak_hz'], 47.6, places=4)
                self.assertAlmostEqual(result['resolution_hz'], .05)
                self.assertAlmostEqual(result['duration_seconds'], 19.999)
                self.assertTrue(result['algorithm_version'])
                self.assertNotEqual(state['job_id'], state['analysis_job_id'])
                self.assertNotIn('用户填写', json.dumps(result['warnings'], ensure_ascii=False))
                self.assertEqual(result['analysis_parameters']['search_low_hz'], 40)
                self.assertEqual(result['analysis_parameters']['search_high_hz'], 50)
                self.assertEqual(result['artifact_refs']['job_id'], state['job_id'])
                for ref in result['artifact_refs']['artifacts']:
                    self.assertEqual(ref['uri'], f"{PREFIX}/jobs/{state['job_id']}/artifacts/{ref['name']}")
                    status, data = request('GET', ref['uri'])
                    self.assertEqual(status, 200)
                    if ref['name'] == 'results.zip':
                        with ZipFile(BytesIO(data)) as archive:
                            manifest = json.loads(archive.read('run-manifest.json'))
                            self.assertEqual(manifest['sample_rate_source'], 'capture_run_summary')
                            self.assertEqual(manifest['algorithm_version'], result['algorithm_version'])
                            self.assertNotIn(directory, archive.read('results.json').decode())
                    elif ref['name'] == 'results.json':
                        rows = json.loads(data)
                        self.assertTrue(all(r['metadata']['sampling_rate_source'] == 'capture_run_summary' for r in rows))
                        self.assertNotIn('用户填写', data.decode())
                    else:
                        with np.load(BytesIO(data)) as arrays:
                            self.assertIn('frequency_0', arrays)
                retry_status, retry_body = request('POST', f'{PREFIX}/spectrum', payload)
                self.assertEqual(retry_status, 202)
                self.assertEqual(json.loads(retry_body)['job_id'], state['job_id'])
            self.assertEqual(originals, {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
                                         for p in Path(directory).glob('*.h5')})

    def test_fast_accept_lifecycle_and_concurrent_idempotency(self):
        from concurrent.futures import ThreadPoolExecutor
        gate, entered = Event(), Event()
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            original = service.backend.retrieve_segments
            def blocked(*args):
                entered.set()
                if not gate.wait(5):
                    raise TimeoutError('test gate')
                return original(*args)
            transitions = []
            update = service.registry.update
            def record(job_id, **fields):
                if 'status' in fields:
                    transitions.append(fields['status'])
                return update(job_id, **fields)
            with patch.object(service.backend, 'retrieve_segments', side_effect=blocked), patch.object(
                    service.registry, 'update', side_effect=record), patch.object(service.jobs, 'submit', wraps=service.jobs.submit) as submit:
                try:
                    payload = spectrum_payload()
                    start = time.monotonic()
                    status, body = request('POST', f'{PREFIX}/spectrum', payload)
                    self.assertEqual(status, 202)
                    self.assertLess(time.monotonic() - start, 1.0)
                    job_id = json.loads(body)['job_id']
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(service.get_job(job_id)['status'], 'resolving')
                    with ThreadPoolExecutor(max_workers=5) as pool:
                        replies = list(pool.map(lambda _: request('POST', f'{PREFIX}/spectrum', payload), range(5)))
                    self.assertEqual({json.loads(b)['job_id'] for _, b in replies}, {job_id})
                    self.assertTrue(all(s == 202 for s, _ in replies))
                    self.assert_error(request('POST', f'{PREFIX}/spectrum', {**payload, 'channel': 'Y'}), 400, 'INVALID_ARGUMENT')
                    self.assertEqual(submit.call_count, 0)
                finally:
                    gate.set()
                self.assertEqual(http_poll(request, job_id)['status'], 'succeeded')
                self.assertEqual(submit.call_count, 1)
                self.assertEqual(transitions, ['resolving', 'retrieving', 'aligning', 'analyzing', 'succeeded'])
                new = service.analyze_spectrum(REQUEST_ID, 3, DEVICE, 'Y', idempotency_key=str(uuid4()))
                self.assertNotEqual(new['job_id'], job_id)
                self.assertEqual(poll(service, new['job_id'])['result']['channel'], 'Y')

    def test_restart_recovers_idempotency_and_result(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _):
            payload = spectrum_payload()
            state = service.analyze_spectrum(**payload)
            result = poll(service, state['job_id'])
            service.shutdown()
            service.jobs.shutdown()
            jobs = AnalysisJobs(Path(directory) / 'derived')
            restored = WaveformAgentService(service.backend, jobs)
            try:
                self.assertEqual(restored.get_job(state['job_id']), result)
                self.assertEqual(restored.analyze_spectrum(**payload)['job_id'], state['job_id'])
                with self.assertRaises(AgentError):
                    restored.analyze_spectrum(**{**payload, 'channel': 'Y'})
            finally:
                restored.shutdown()
                jobs.shutdown()

    def test_restart_incomplete_job_fails_without_replay(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _):
            payload = spectrum_payload()
            state, _ = service.registry.create(payload, {}, True)
            service.registry.update(state['job_id'], status='retrieving')
            service.shutdown()
            restored = WaveformAgentService(service.backend, service.jobs)
            try:
                recovered = restored.get_job(state['job_id'])
                self.assertEqual(recovered['status'], 'failed')
                self.assertEqual(recovered['error']['code'], 'UNAVAILABLE')
                self.assertEqual(restored.analyze_spectrum(**payload)['job_id'], state['job_id'])
            finally:
                restored.shutdown()

    def test_basic_argument_errors_are_synchronous(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            valid = spectrum_payload()
            for changes in [dict(request_id='bad'), dict(request_id=REQUEST_ID.upper()),
                            dict(capture_sequence=-1), dict(capture_sequence=True),
                            dict(channel='Z'), dict(profile='unknown'), dict(idempotency_key='bad')]:
                with self.subTest(changes=changes):
                    self.assert_error(request('POST', f'{PREFIX}/spectrum', {**valid, **changes}), 400, 'INVALID_ARGUMENT')
            for payload in [{k: v for k, v in valid.items() if k != 'idempotency_key'},
                            {**valid, 'paths': ['/home/private']}, [], None]:
                self.assert_error(request('POST', f'{PREFIX}/spectrum', payload), 400, 'INVALID_ARGUMENT')
            self.assertEqual(len(service.registry.states), 0)

    def test_async_resolution_failures_preserve_domain_errors(self):
        from dataclasses import replace
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            for changes in [dict(capture_sequence=1), dict(device_id='unknown')]:
                status, body = request('POST', f'{PREFIX}/spectrum', {**spectrum_payload(), **changes})
                self.assertEqual(status, 202)
                state = http_poll(request, json.loads(body)['job_id'])
                self.assertEqual(state['status'], 'failed')
                self.assertEqual(state['error']['code'], 'NOT_FOUND')
            original = service.backend.retrieve_runs
            with patch.object(service.backend, 'retrieve_runs', side_effect=lambda d: tuple(
                    replace(r, frequency_hz=None) for r in original(d))):
                status, body = request('POST', f'{PREFIX}/spectrum', spectrum_payload())
                self.assertEqual(status, 202)
                self.assertEqual(http_poll(request, json.loads(body)['job_id'])['error']['code'], 'ANALYSIS_INVALID')
            for exc, code in [(ConnectionError('mongodb://SECRET_TOKEN /SHINE_Data'), 'UNAVAILABLE'),
                              (TimeoutError('password=SECRET_TOKEN'), 'TIMEOUT'),
                              (RuntimeError('AMBIGUOUS SECRET_TOKEN'), 'AMBIGUOUS'),
                              (RuntimeError('SECRET_TOKEN /home/private'), 'INTERNAL')]:
                with patch.object(service.backend, 'retrieve_segments', side_effect=exc):
                    status, body = request('POST', f'{PREFIX}/spectrum', spectrum_payload())
                    self.assertEqual(status, 202)
                    job_id = json.loads(body)['job_id']
                    state = http_poll(request, job_id)
                    self.assertEqual(state['error']['code'], code)
                    self.assertNotIn('SECRET_TOKEN', json.dumps(state))
                    # Query failure uses the same code with its synchronous HTTP mapping.
                    self.assert_error(request('GET', f'{PREFIX}/requests/{REQUEST_ID}/captures'), HTTP_STATUS[code], code)

    def test_hdf_conflict_and_unreadable_are_job_failures(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            path = Path(directory) / 'segment-3.h5'
            with h5py.File(path, 'a') as h5:
                h5.attrs['frequency_hz'] = 500
            status, body = request('POST', f'{PREFIX}/spectrum', spectrum_payload())
            self.assertEqual(status, 202)
            self.assertEqual(http_poll(request, json.loads(body)['job_id'])['error']['code'], 'ANALYSIS_INVALID')
            service.backend._retrieval.clear_cache()
            path.write_bytes(b'unreadable')
            status, body = request('POST', f'{PREFIX}/spectrum', spectrum_payload())
            self.assertEqual(status, 202)
            self.assertEqual(http_poll(request, json.loads(body)['job_id'])['error']['code'], 'DATA_UNREADABLE')

    def test_classification_invalid_is_succeeded_but_infrastructure_failure_is_failed(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            # 20 seconds is valid data but too short for the unchanged low profile.
            payload = {**spectrum_payload(), 'profile': 'low_v1'}
            status, body = request('POST', f'{PREFIX}/spectrum', payload)
            self.assertEqual(status, 202)
            state = http_poll(request, json.loads(body)['job_id'])
            self.assertEqual(state['status'], 'succeeded', state)
            self.assertEqual(state['result']['classification'], 'invalid')
            self.assertNotIn('error', state)
            with patch('waveform_storage.analysis.service.run_analysis', side_effect=RuntimeError('SECRET_TOKEN /home/private')):
                status, body = request('POST', f'{PREFIX}/spectrum', spectrum_payload())
                self.assertEqual(status, 202)
                state = http_poll(request, json.loads(body)['job_id'])
                self.assertEqual(state['status'], 'failed')
                self.assertEqual(state['error']['code'], 'INTERNAL')
                self.assertNotIn('SECRET_TOKEN', json.dumps(state))

    def test_health_real_candidate_closed_and_unreachable(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            with patch.object(service.backend, 'start_capture', side_effect=AssertionError('write')), patch.object(
                    service.jobs, 'submit', side_effect=AssertionError('analysis')):
                status, body = request('GET', '/health')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), dict(status='ok', api_version='v1', candidate='reachable', analysis='ready'))
                with patch.object(service.backend, 'check_health', side_effect=ConnectionError('SECRET_TOKEN')):
                    status, body = request('GET', '/health')
                    self.assertEqual(status, 503)
                    self.assertEqual(json.loads(body)['status'], 'degraded')
                    self.assertNotIn('SECRET_TOKEN', body.decode())
                service.jobs.shutdown()
                status, body = request('GET', '/health')
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body)['analysis'], 'closed')
                with patch.object(service.backend, 'check_health', return_value=False):
                    status, body = request('GET', '/health')
                    self.assertEqual(status, 503)
                    self.assertEqual(json.loads(body)['status'], 'unavailable')
                self.assert_error(request('POST', f'{PREFIX}/spectrum', spectrum_payload()), 503, 'UNAVAILABLE')

    def test_artifact_security_unknown_job_and_read_only_router(self):
        gate = Event()
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _), http_fixture(service) as request:
            self.assert_error(request('GET', f'{PREFIX}/jobs/{uuid4()}'), 404, 'NOT_FOUND')
            self.assert_error(request('GET', f'{PREFIX}/jobs/bad'), 400, 'INVALID_ARGUMENT')
            original = service.backend.retrieve_segments
            def blocked(*args):
                gate.wait(5)
                return original(*args)
            with patch.object(service.backend, 'retrieve_segments', side_effect=blocked):
                try:
                    state = service.analyze_spectrum(**spectrum_payload())
                    job_id = state['job_id']
                    self.assert_error(request('GET', f'{PREFIX}/jobs/{job_id}/artifacts/results.json'), 422, 'ANALYSIS_INVALID')
                finally:
                    gate.set()
                state = http_poll(request, job_id)
            for name in ('unknown.json', '..', '../results.json', '%2e%2e', '%2e%2e%2fresults.json',
                         '%252e%252e%252fresults.json', '/etc/passwd', '%2fetc%2fpasswd', 'run-manifest.json'):
                self.assert_error(request('GET', f'{PREFIX}/jobs/{job_id}/artifacts/{name}'), 404, 'NOT_FOUND')
            # Even a whitelisted name cannot follow a symlink out of the output tree.
            artifact = service.artifact_path(job_id, 'results.json')
            artifact.unlink()
            artifact.symlink_to(Path(directory) / 'segment-3.h5')
            self.assert_error(request('GET', f'{PREFIX}/jobs/{job_id}/artifacts/results.json'), 404, 'NOT_FOUND')
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                for suffix in ('start_capture', 'cancel', 'delete', 'rename', 'move', 'mongo/update',
                               'epics/write', f'jobs/{job_id}/cancel'):
                    self.assert_error(request(method, f'{PREFIX}/{suffix}', {}), 404, 'NOT_FOUND')
            self.assert_error(request('GET', '/api/agent/waveform/runs?date=2026-10-08'), 404, 'NOT_FOUND')

    def test_agent_queue_is_bounded(self):
        gate = Event()
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _):
            original = service.backend.retrieve_segments
            def blocked(*args):
                gate.wait(5)
                return original(*args)
            with patch.object(service.backend, 'retrieve_segments', side_effect=blocked):
                try:
                    states = [service.analyze_spectrum(**spectrum_payload()) for _ in range(8)]
                    with self.assertRaises(AgentError) as caught:
                        service.analyze_spectrum(**spectrum_payload())
                    self.assertEqual(caught.exception.code, 'UNAVAILABLE')
                    self.assertEqual(service.health()['analysis'], 'busy')
                finally:
                    gate.set()
                for state in states:
                    self.assertEqual(poll(service, state['job_id'])['status'], 'succeeded')

    def test_single_registry_owner_and_closed_service(self):
        with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _):
            with self.assertRaises(RuntimeError):
                WaveformAgentService(service.backend, service.jobs)
            payload = spectrum_payload()
            job = service.analyze_spectrum(**payload)
            poll(service, job['job_id'])
            service.shutdown()
            self.assertEqual(service.analyze_spectrum(**payload)['job_id'], job['job_id'])
            with self.assertRaises(AgentError) as caught:
                service.analyze_spectrum(**spectrum_payload())
            self.assertEqual(caught.exception.code, 'UNAVAILABLE')
