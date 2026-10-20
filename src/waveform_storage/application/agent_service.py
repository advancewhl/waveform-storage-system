"""Read-only Agent domain facade over Candidate retrieval and spectrum jobs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from threading import RLock
from uuid import UUID
import time
import logging

from waveform_storage.analysis.loaders import _sampling_rate
from waveform_storage.analysis.models import AnalysisConfig, AnalysisRequest
from .agent_jobs import AgentJobRegistry, TERMINAL


HTTP_STATUS = {
    'NOT_FOUND': 404, 'AMBIGUOUS': 409, 'INVALID_ARGUMENT': 400,
    'UNAVAILABLE': 503, 'TIMEOUT': 504, 'DATA_UNREADABLE': 422,
    'ANALYSIS_INVALID': 422, 'INTERNAL': 500,
}
ARTIFACTS = ('results.json', 'results.zip', 'spectra.npz')
PREFIX = '/api/agent/v1/waveform'


class AgentError(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason

    def payload(self):
        return {'error': {'code': self.code, 'reason': self.reason,
                          'diagnostics': {'error_code': self.code,
                                          'reason': self.reason, 'detail': self.reason}}}


def canonical_uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise AgentError('INVALID_ARGUMENT', 'Expected a canonical UUID') from None
    return value


def translate_error(exc):
    if isinstance(exc, AgentError):
        return exc
    # Candidate currently encodes upstream protocol error codes in RuntimeError.
    text = str(exc)
    cause = str(exc.__cause__) if exc.__cause__ else ''
    if (isinstance(exc, TimeoutError) or type(exc).__name__ == 'ZmqTimeout'
            or 'timed out' in text.lower() or 'timeout' in text.lower()):
        return AgentError('TIMEOUT', 'Waveform backend timed out')
    for token, code in [('NOT_FOUND', 'NOT_FOUND'), ('AMBIGUOUS', 'AMBIGUOUS'),
                        ('UNAVAILABLE', 'UNAVAILABLE'), ('TIMEOUT', 'TIMEOUT'),
                        ('INVALID_ARGUMENT', 'INVALID_ARGUMENT')]:
        if token in text:
            return AgentError(code, 'Waveform backend returned ' + code)
    if 'duplicate' in text.lower() or 'duplicate' in cause.lower():
        return AgentError('AMBIGUOUS', 'Backend returned duplicate capture records')
    if 'returned invalid' in text or 'mismatched segment' in text:
        return AgentError('DATA_UNREADABLE', 'Backend returned invalid metadata')
    if isinstance(exc, (ConnectionError, OSError)) or type(exc).__name__ == 'SshStorageError':
        return AgentError('UNAVAILABLE', 'Waveform backend unavailable')
    logging.getLogger(__name__).exception('Agent operation failed')
    return AgentError('INTERNAL', 'Waveform operation failed; consult server logs')


class WaveformAgentService:
    """No capture mutations; raw storage and transport details stay behind backend."""

    def __init__(self, backend, jobs):
        self.backend = backend
        self.jobs = jobs
        self.registry = AgentJobRegistry(jobs.output_root)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='waveform-agent')
        # RetrievalService has mutable caches shared by HTTP threads.
        self._lock = RLock()

    def list_capture_runs(self, selected_date):
        try:
            selected = date.fromisoformat(selected_date)
            if selected.isoformat() != selected_date:
                raise ValueError()
        except (ValueError, TypeError):
            raise AgentError('INVALID_ARGUMENT', 'date must be YYYY-MM-DD') from None
        with self._lock:
            return [run.to_payload() for run in self.backend.retrieve_runs(selected)]

    def list_captures(self, request_id):
        canonical_uuid(request_id)
        with self._lock:
            segments = self.backend.retrieve_segments(request_id)
        if not segments:
            raise AgentError('NOT_FOUND', 'No captures for this request')
        return [segment.to_payload() for segment in segments]

    def _segment(self, request_id, capture_sequence):
        canonical_uuid(request_id)
        if type(capture_sequence) is not int or capture_sequence < 0:
            raise AgentError('INVALID_ARGUMENT', 'capture_sequence must be a nonnegative integer')
        segments = self.backend.retrieve_segments(request_id)
        matches = [s for s in segments if s.capture_sequence == capture_sequence]
        if not matches:
            raise AgentError('NOT_FOUND', 'Capture sequence not found')
        if len(matches) != 1:
            raise AgentError('AMBIGUOUS', 'Capture sequence is not unique')
        return matches[0]

    def inspect_capture(self, request_id, capture_sequence):
        with self._lock:
            return self._segment(request_id, capture_sequence).to_payload()

    def analyze_spectrum(self, request_id, capture_sequence, device_id, channel,
                         profile='high_band', *, idempotency_key):
        """Accept durable work; peak_hz is a peak within the profile search band."""
        canonical_uuid(request_id)
        canonical_uuid(idempotency_key)
        if type(capture_sequence) is not int or capture_sequence < 0:
            raise AgentError('INVALID_ARGUMENT', 'capture_sequence must be a nonnegative integer')
        if not isinstance(device_id, str) or not device_id or channel not in ('X', 'Y'):
            raise AgentError('INVALID_ARGUMENT', 'Provide device_id and channel X or Y')
        try:
            config = AnalysisConfig.for_profile(profile)
        except (ValueError, TypeError):
            raise AgentError('INVALID_ARGUMENT', 'Unknown spectrum profile') from None
        request = dict(request_id=request_id, capture_sequence=capture_sequence,
                       device_id=device_id, channel=channel, profile=profile,
                       idempotency_key=idempotency_key)
        # Registry lock covers acceptance and executor dispatch against shutdown.
        with self.registry.lock:
            state, created = self.registry.create(request, asdict(config),
                                                   self.jobs.availability() == 'ready')
            if created:
                self._executor.submit(self._execute, state['job_id'], request, config)
        return self._public(state)

    def _execute(self, job_id, values, config):
        try:
            self.registry.update(job_id, status='resolving')
            request_id = values['request_id']
            device_id = values['device_id']
            with self._lock:
                segment = self._segment(request_id, values['capture_sequence'])
                if device_id not in segment.device_ids:
                    raise AgentError('NOT_FOUND', 'Device is not available in this capture')
                selected_date = segment.storage_date_local
                if selected_date is None:
                    from zoneinfo import ZoneInfo
                    selected_date = segment.captured_at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
                runs = [r for r in self.backend.retrieve_runs(date.fromisoformat(selected_date))
                        if r.request_id == request_id]
                if len(runs) > 1:
                    raise AgentError('AMBIGUOUS', 'Capture run is not unique')
                if not runs or runs[0].frequency_hz is None:
                    raise AgentError('ANALYSIS_INVALID', 'Historical run frequency_hz is missing')
                frequency = runs[0].frequency_hz
                self.registry.update(job_id, status='retrieving')
                prepared = self.backend.prepare_segment(segment, (device_id,))
                if prepared.missing_devices:
                    raise AgentError('NOT_FOUND', 'Waveform file is missing')
                if prepared.skipped_devices or not prepared.records:
                    details = ' '.join(s.detail for s in prepared.skipped_devices).lower()
                    if 'timed out' in details or 'timeout' in details:
                        raise AgentError('TIMEOUT', 'Waveform read timed out')
                    raise AgentError('DATA_UNREADABLE', 'Waveform could not be read')
                try:
                    for record in prepared.records:
                        _sampling_rate(record.metadata, frequency)
                except ValueError:
                    raise AgentError('ANALYSIS_INVALID', 'HDF5 sampling rate is invalid or conflicts with historical frequency_hz') from None
                self.registry.update(job_id, status='aligning')
                alignment = self.backend.align_prepared_segment(prepared)
                if device_id not in alignment.device_ids or not alignment.bunch_ids.size:
                    raise AgentError('DATA_UNREADABLE', 'Waveform could not be aligned')
            request = AnalysisRequest('alignment', config=config, sample_rate_hz=frequency,
                                      alignment=alignment, sample_rate_source='capture_run_summary')
            try:
                analysis_id = self.jobs.submit(request)
            except (ValueError, RuntimeError):
                raise AgentError('UNAVAILABLE', 'Analysis queue unavailable') from None
            self.registry.update(job_id, status='analyzing', analysis_job_id=analysis_id)
            while self.jobs.get(analysis_id)['status'] in ('accepted', 'running'):
                time.sleep(.02)
            analysis_state = self.jobs.get(analysis_id)
            if analysis_state['status'] != 'succeeded':
                # AnalysisJobs retains the internal exception and traceback privately.
                logging.getLogger(__name__).error('Analysis job %s failed: %s',
                                                  analysis_id, analysis_state.get('error'))
                raise AgentError('INTERNAL', 'Spectrum analysis infrastructure failed; consult server logs')
            run = self.jobs.result(analysis_id)
            channels = [c for c in run.channels if c.channel == device_id + '_' + values['channel']]
            if len(channels) != 1:
                raise AgentError('ANALYSIS_INVALID', 'Expected analysis channel is missing or ambiguous')
            channel = channels[0]
            keys = ('sample_rate_hz', 'data_points', 'duration_seconds', 'classification',
                    'display_status', 'reason', 'peak_hz', 'peak_to_background_db',
                    'resolution_hz', 'block_hits', 'block_count', 'block_ratio', 'quality', 'warnings')
            result = {**values, **{k: getattr(channel, k) for k in keys},
                      'captured_at': segment.captured_at.isoformat(),
                      'storage_date_local': selected_date,
                      'sample_rate_hz': frequency,
                      'sample_rate_source': 'capture_run_summary',
                      'algorithm_version': run.manifest['algorithm_version'],
                      'analysis_parameters': asdict(config), 'analysis_job_id': analysis_id,
                      'artifact_refs': self.artifact_refs(job_id)}
            self.registry.update(job_id, status='succeeded', result=result)
        except Exception as exc:
            error = translate_error(exc)
            self.registry.update(job_id, status='failed', **error.payload())

    @staticmethod
    def _public(state):
        return {**{k: v for k, v in state.items() if k != 'request'}, **state['request']}

    def get_job(self, job_id):
        canonical_uuid(job_id)
        return self._public(self.registry.get(job_id))

    def health(self):
        try:
            candidate = 'reachable' if self.backend.check_health() else 'unavailable'
        except Exception:
            candidate = 'unavailable'
        with self.registry.lock:
            analysis = self.jobs.availability()
            if self.registry.closed:
                analysis = 'closed'
            elif sum(s['status'] not in TERMINAL for s in self.registry.states.values()) >= 8:
                analysis = 'busy'
        status = ('ok' if candidate == 'reachable' and analysis == 'ready' else
                  'unavailable' if candidate == 'unavailable' and analysis != 'ready' else 'degraded')
        return {'status': status, 'api_version': 'v1', 'candidate': candidate, 'analysis': analysis}

    def shutdown(self):
        with self.registry.lock:
            self.registry.closed = True
        # Gracefully drain accepted jobs before AnalysisJobs and backend shutdown.
        self._executor.shutdown(wait=True)
        self.registry.close()

    @staticmethod
    def artifact_refs(job_id):
        return {'job_id': job_id, 'artifacts': [
            {'name': name, 'uri': f'{PREFIX}/jobs/{job_id}/artifacts/{name}'}
            for name in ARTIFACTS]}

    def artifact_path(self, job_id, artifact):
        if artifact not in ARTIFACTS:
            raise AgentError('NOT_FOUND', 'Artifact not found')
        state = self.get_job(job_id)
        if state['status'] != 'succeeded':
            raise AgentError('ANALYSIS_INVALID', 'Artifacts require a succeeded job')
        root = self.jobs.output_root.resolve()
        directory = root / canonical_uuid(state['analysis_job_id'])
        path = directory / artifact
        if (directory.is_symlink() or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root)):
            raise AgentError('NOT_FOUND', 'Artifact not found')
        return path
