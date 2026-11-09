"""FastAPI transport for the existing Waveform v1 and v2 HTTP contracts."""

from email.message import Message
from io import BytesIO
import socket
from threading import Event

from fastapi import FastAPI, Request
import uvicorn
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from .agent_http_api import make_handler


def create_app(service, *, v2=None, v2_only=None):
    handler_type = make_handler(service, v2=v2, v2_only=v2_only)
    app = FastAPI(title="Waveform Agent API", version="2", docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def dispatch(request: Request, path: str):
        # Preserve the original raw path and duplicate security headers for the
        # existing contract's validation, rather than normalizing them away.
        headers = Message()
        for key, value in request.scope['headers']:
            headers.add_header(key.decode('latin-1'), value.decode('latin-1'))
        raw_path = request.scope.get('raw_path', request.url.path.encode('utf-8'))
        request_path = raw_path.decode('ascii')
        query = request.scope['query_string']
        if query:
            request_path += '?' + query.decode('latin-1')

        lengths = headers.get_all('Content-Length', [])
        try:
            bounded_body = len(lengths) == 1 and 0 < int(lengths[0]) <= 16384
        except ValueError:
            bounded_body = False
        body = await request.body() if request.method == 'POST' and bounded_body else b''
        output = BytesIO()

        class Adapter(handler_type):
            def send_response(self, status, message=None):
                self.response_status = status

            def send_header(self, name, value):
                self.response_headers[name] = value

            def end_headers(self):
                pass

            def send_artifact(self, path, name):
                stream = path.open('rb')
                self.stream = stream
                self.headers_sent = True
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(path.stat().st_size))
                self.send_header('Content-Disposition', f'attachment; filename="{name}"')

            def send_download(self, blob):
                self.stream = blob.stream
                self.headers_sent = True
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(blob.size))
                self.send_header('Content-Disposition', f'attachment; filename="{blob.name}"')
                self.send_header('X-Content-SHA256', blob.sha256)
                self.send_header('ETag', '"sha256:' + blob.sha256 + '"')

        handler = object.__new__(Adapter)
        handler.path = request_path
        handler.headers = headers
        handler.rfile = BytesIO(body)
        handler.wfile = output
        handler.response_headers = {}
        handler.close_connection = False
        handler.stream = None
        try:
            await run_in_threadpool(handler._handle, request.method)
            if handler.stream is None:
                output.seek(0)
        except BaseException:
            output.close()
            raise

        def chunks():
            stream = handler.stream or output
            try:
                while block := stream.read(1024 * 1024):
                    yield block
            finally:
                stream.close()
                if stream is not output:
                    output.close()

        response = StreamingResponse(chunks(), status_code=handler.response_status)
        response.raw_headers = [(key.encode('latin-1'), value.encode('latin-1'))
                                for key, value in handler.response_headers.items()]
        return response

    return app


def make_server(service, address=('127.0.0.1', 8771), *, v2=None, v2_only=None):
    if address[0] not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Agent HTTP must bind loopback; use a TLS reverse proxy for remote access')
    app = create_app(service, v2=v2, v2_only=v2_only)
    family = socket.AF_INET6 if address[0] == '::1' else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(address)
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
    stopped = Event()
    started = Event()

    class Server:
        server_address = sock.getsockname()[:2]

        def serve_forever(self):
            started.set()
            try:
                server.run(sockets=[sock])
            finally:
                stopped.set()

        def shutdown(self):
            server.should_exit = True
            if started.is_set():
                stopped.wait()

        def server_close(self):
            sock.close()

    return Server()
