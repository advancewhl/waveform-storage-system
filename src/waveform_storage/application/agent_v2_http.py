"""Scope-isolated v2 routing, strict bodies, bounded pages and binary responses."""
import base64
import json
import logging
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from .agent_v2_contract import APIError, PREFIX, STATUS, canonical, digest, uuid
from .agent_v2_service import safe_error


def page(items, query, name):
    limit = int(query.get('limit', '100'))
    if not 1 <= limit <= 500:
        raise APIError('INVALID_ARGUMENT', 'limit must be 1 to 500')
    fingerprint = digest({'items': items, 'query': {k: v for k, v in query.items() if k != 'cursor'}})
    offset = 0
    if query.get('cursor'):
        try:
            cursor = json.loads(base64.urlsafe_b64decode(query['cursor']))
            offset = cursor['offset']
            if type(offset) is not int or not 0 <= offset <= len(items) or cursor['fingerprint'] != fingerprint:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise APIError('CONFLICT', 'Cursor is invalid or source listing changed') from None
    end = min(len(items), offset + limit)
    next_cursor = (base64.urlsafe_b64encode(canonical({'offset': end, 'fingerprint': fingerprint})).decode()
                   if end < len(items) else None)
    return {name: items[offset:end], 'total': len(items), 'truncated': end < len(items), 'next_cursor': next_cursor}


def _body(handler):
    try:
        if len(handler.headers.get_all('Content-Length', [])) != 1 or handler.headers.get('Transfer-Encoding'):
            raise ValueError()
        length = int(handler.headers['Content-Length'])
        if not 0 < length <= 16384:
            raise ValueError()
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        value = json.loads(handler.rfile.read(length), object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise APIError('INVALID_ARGUMENT', 'Expected bounded JSON object with unique fields') from None


def _send_download(handler, blob):
    try:
        handler.headers_sent = True
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/octet-stream')
        handler.send_header('Content-Length', str(blob.size))
        handler.send_header('Content-Disposition', f'attachment; filename="{blob.name}"')
        handler.send_header('X-Content-SHA256', blob.sha256)
        handler.send_header('ETag', '"sha256:' + blob.sha256 + '"')
        handler.end_headers()
        while block := blob.stream.read(1024 * 1024):
            handler.wfile.write(block)
    finally:
        blob.stream.close()


def dispatch(handler, api, method):
    """Return true for every v2 path, including safe error responses."""
    parsed = urlparse(handler.path)
    if not (parsed.path == PREFIX or parsed.path.startswith(PREFIX + '/')):
        return False
    operation_id = None
    try:
        if unquote(parsed.path) != parsed.path or '..' in parsed.path.split('/') or '\\' in parsed.path:
            raise APIError('INVALID_ARGUMENT', 'Encoded or traversal paths are not accepted')
        parts = parsed.path[len(PREFIX) + 1:].split('/')
        if len(parts) < 2 or parts[0] not in ('read', 'control', 'data'):
            raise APIError('NOT_FOUND')
        scope, route = parts[0], parts[1:]
        for name in ('Authorization', 'Idempotency-Key', 'X-Operation-Id', 'X-Approval-Id', 'X-Operator-Id', 'X-Approval-Token'):
            if len(handler.headers.get_all(name, [])) > 1:
                raise APIError('INVALID_ARGUMENT', 'Duplicate security header')
        api.access.authenticate(scope, handler.headers)
        api.access.rate_limit(scope, method, parsed.path)
        if handler.headers.get('X-Operation-Id'):
            operation_id = uuid(handler.headers['X-Operation-Id'])
        query_multi = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(v) != 1 for v in query_multi.values()):
            raise APIError('INVALID_ARGUMENT')
        query = {k: v[0] for k, v in query_multi.items()}
        allowed = set()
        if scope == 'read':
            if route == ['runs']:
                allowed = {'start_date', 'end_date', 'device_id', 'status', 'limit', 'cursor'}
            elif route == ['devices']:
                allowed = {'section', 'acquisition_only', 'limit', 'cursor'}
            elif len(route) == 3 and route[0] == 'requests' and route[2] == 'captures':
                allowed = {'limit', 'cursor'}
            elif len(route) == 3 and route[0] == 'alignments' and route[2] == 'export':
                allowed = {'format'}
        if scope == 'data' and len(route) == 2 and route[1] == 'versions':
            allowed = {'limit', 'cursor'}
        if scope in ('data', 'control') and route == ['operations']:
            allowed = {'limit', 'cursor', 'operator_id', 'action',
                       'target_id' if scope == 'data' else 'request_id'}
        if set(query) - allowed or method != 'GET' and query:
            raise APIError('INVALID_ARGUMENT', 'Unknown query parameters')
        if method == 'POST':
            body = _body(handler)
            action = method + ' ' + parsed.path
            if scope == 'read' and route in (['alignments'], ['spectrum']):
                kind = 'alignment' if route == ['alignments'] else 'spectrum'
                result = api.post(scope, action, body, handler.headers,
                                  lambda op: api.submit(kind, body, op['operation_id']))
                handler._json(202, result)
                return True
            if scope == 'read' and len(route) == 3 and route[0] == 'jobs' and route[2] == 'cancel':
                execute = lambda op: api.cancel_job(route[1], body)
            elif scope == 'control' and route == ['captures']:
                execute = lambda op: api.start(body)
            elif scope == 'control' and len(route) == 3 and route[0] == 'captures' and route[2] == 'cancel':
                execute = lambda op: api.cancel(route[1], body)
            elif scope == 'data' and route == ['preview']:
                execute = lambda op: api.preview(body)
            elif scope == 'data' and len(route) == 1 and route[0] in ('update', 'soft-delete', 'restore', 'overwrite'):
                execute = lambda op: api.mutate(route[0], body, op)
            else:
                raise APIError('NOT_FOUND')
            result = api.post(scope, action, body, handler.headers, execute)
            handler._json(202 if scope == 'control' else 200, result)
            return True
        if method != 'GET':
            raise APIError('NOT_FOUND')
        if scope == 'read':
            if route == ['health']:
                health = api.health()
                handler._json(200 if health['status'] == 'ok' else 503, health)
                return True
            if route == ['runs']:
                result = page(api.runs(query), query, 'runs')
            elif len(route) == 2 and route[0] == 'runs':
                result = api.run(route[1])
            elif route == ['devices']:
                result = page(api.devices(query), query, 'devices')
            elif len(route) in (3, 4, 6) and route[0] == 'requests' and route[2] == 'captures':
                if len(route) == 3:
                    result = page(api.captures(route[1]), query, 'captures')
                elif len(route) == 4:
                    result = api.inspect(route[1], int(route[3]))
                elif route[4] == 'raw':
                    blob = api.raw({'request_id': route[1], 'capture_sequence': int(route[3]), 'device_id': route[5]})
                    _send_download(handler, blob)
                    return True
                else:
                    raise APIError('NOT_FOUND')
            elif len(route) in (2, 3, 4) and route[0] in ('jobs', 'alignments'):
                state = api.job(route[1])
                if route[0] == 'alignments' and state['kind'] != 'alignment':
                    raise APIError('NOT_FOUND')
                if len(route) == 2:
                    result = state
                elif len(route) == 3 and route[2] == 'result':
                    if state['status'] != 'succeeded':
                        raise APIError('OPERATION_IN_PROGRESS' if state['status'] in ('accepted', 'running') else 'ANALYSIS_INVALID')
                    result = state['result']
                elif route[0] == 'alignments' and len(route) == 3 and route[2] == 'export':
                    format_name = query.get('format', 'v2')
                    if format_name not in ('legacy', 'v2'):
                        raise APIError('INVALID_ARGUMENT')
                    _send_download(handler, api.artifact(route[1], 'alignment-' + format_name + '.h5'))
                    return True
                elif len(route) == 4 and route[2] == 'artifacts':
                    _send_download(handler, api.artifact(route[1], route[3]))
                    return True
                else:
                    raise APIError('NOT_FOUND')
            else:
                raise APIError('NOT_FOUND')
        elif route == ['operations']:
            result = page(api.operations(scope, query), query, 'operations')
        elif scope == 'control':
            if route == ['health']:
                health = api.backend.check_health()
                handler._json(200 if health else 503, {'status': 'ok' if health else 'unavailable', 'api_version': 'v2'})
                return True
            if route == ['timing-frequency']:
                reply = api.backend._candidate_transport(timeout_ms=25000).get_timing_frequency(str(uuid4()))
                if not reply.accepted:
                    api._capture_reply(reply)
                result = {'frequency_hz': reply.payload['frequency_hz'], 'source': 'timing_pv'}
            elif len(route) == 2 and route[0] == 'captures':
                result = api.capture_status(route[1])
            else:
                raise APIError('NOT_FOUND')
        elif len(route) == 2 and route[1] == 'versions':
            result = page(api.versions(route[0]), query, 'versions')
            result.update(effect_scope='agent-v2-overlay', source_modified=False, source_authoritative=True)
        elif len(route) == 1:
            result = api.operation(route[0])
        else:
            raise APIError('NOT_FOUND')
        handler._json(200, result)
    except (BrokenPipeError, ConnectionResetError):
        handler.close_connection = True
        logging.getLogger(__name__).warning('Agent connection interrupted; closing connection')
        return True
    except Exception as exc:
        error = safe_error(exc)
        handler.close_connection = True
        if getattr(handler, 'headers_sent', False):
            logging.getLogger(__name__).warning('Agent download interrupted; closing connection')
        else:
            handler._json(getattr(error, 'http_status', STATUS[error.code]), error.payload(operation_id))
    return True
