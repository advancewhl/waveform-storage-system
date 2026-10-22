"""Loopback Agent HTTP boundary with compatible v1 and optional scoped v2."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import json
import logging
import os

from waveform_storage.analysis.service import _json_value
from .agent_service import AgentError, HTTP_STATUS, PREFIX, translate_error


def make_server(service, address=('127.0.0.1', 8771), *, v2=None, v2_only=None):
    if v2_only is None:
        v2_only = os.environ.get('WAVEFORM_AGENT_V2_ONLY', 'false').lower() == 'true'
    if v2_only and v2 is None:
        raise ValueError('V2_ONLY requires an enabled v2 service')
    if v2 is not None and not v2_only:
        logging.getLogger(__name__).warning('v1 无认证，必须由网络 ACL/代理限制；生产推荐 WAVEFORM_AGENT_V2_ONLY=true 或独立端口')
    if address[0] not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Agent HTTP must bind loopback; use a TLS reverse proxy for remote access')
    class Handler(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, format, *args):
            # No raw URLs, request headers or exception text in access logs.
            pass

        def _json(self, status, payload):
            body = json.dumps(_json_value(payload), ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self, method):
            if v2 is not None:
                from .agent_v2_http import dispatch
                if dispatch(self, v2, method):
                    return
            parsed = urlparse(self.path)
            if method == 'GET' and parsed.path == '/health':
                health = service.health()
                self._json(200 if health['status'] == 'ok' else 503, health)
                return
            if v2_only:
                self.close_connection = True
                raise AgentError('NOT_FOUND', 'Endpoint not found')
            parts = parsed.path.removeprefix(PREFIX + '/').split('/')
            if not parsed.path.startswith(PREFIX + '/'):
                raise AgentError('NOT_FOUND', 'Endpoint not found')
            if method == 'POST' and parts == ['spectrum']:
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384 or self.headers.get('Transfer-Encoding'):
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    required = {'request_id', 'capture_sequence', 'device_id', 'channel', 'idempotency_key'}
                    if not isinstance(data, dict) or not required <= data.keys() or data.keys() - (required | {'profile'}):
                        raise ValueError()
                except (ValueError, TypeError, UnicodeError):
                    raise AgentError('INVALID_ARGUMENT', 'Expected bounded spectrum request JSON') from None
                self._json(202, service.analyze_spectrum(**data))
                return
            if method == 'GET':
                if parts == ['runs']:
                    query = parse_qs(parsed.query)
                    if set(query) != {'date'} or len(query['date']) != 1:
                        raise AgentError('INVALID_ARGUMENT', 'Provide one date filter')
                    self._json(200, {'runs': service.list_capture_runs(query['date'][0])})
                    return
                if len(parts) in (3, 4) and parts[0] == 'requests' and parts[2] == 'captures':
                    if len(parts) == 3:
                        self._json(200, {'captures': service.list_captures(parts[1])})
                    else:
                        try:
                            sequence = int(parts[3])
                        except ValueError:
                            raise AgentError('INVALID_ARGUMENT', 'Invalid capture_sequence') from None
                        self._json(200, service.inspect_capture(parts[1], sequence))
                    return
                if len(parts) == 2 and parts[0] == 'jobs':
                    self._json(200, service.get_job(parts[1]))
                    return
                if len(parts) == 4 and parts[0] == 'jobs' and parts[2] == 'artifacts':
                    path = service.artifact_path(parts[1], parts[3])
                    with path.open('rb') as stream:
                        self.headers_sent = True
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(path.stat().st_size))
                        self.send_header('Content-Disposition', f'attachment; filename="{parts[3]}"')
                        self.end_headers()
                        while block := stream.read(1024 * 1024):
                            self.wfile.write(block)
                    return
            raise AgentError('NOT_FOUND', 'Endpoint not found')

        def _handle(self, method):
            self.headers_sent = False
            try:
                self._dispatch(method)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                if self.headers_sent:
                    self.close_connection = True
                    logging.getLogger(__name__).warning('Agent response stream interrupted; closing connection')
                    return
                error = translate_error(exc)
                self.close_connection = True
                self._json(HTTP_STATUS[error.code], error.payload())

        def do_GET(self):
            self._handle('GET')

        def do_POST(self):
            self._handle('POST')

        def do_DELETE(self):
            self._handle('DELETE')

        def do_PUT(self):
            self._handle('PUT')

        def do_PATCH(self):
            self._handle('PATCH')

    return ThreadingHTTPServer(address, Handler)
