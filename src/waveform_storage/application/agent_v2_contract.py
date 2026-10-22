"""Strict v2 wire primitives, service scopes and signed approval references."""
import base64
import hashlib
import hmac
import json
import math
import re
import time
from threading import Lock
from uuid import UUID

PREFIX = '/api/agent/v2'
STATUS = {
    'INVALID_ARGUMENT': 400, 'NOT_FOUND': 404, 'AMBIGUOUS': 409,
    'CONFLICT': 409, 'IDEMPOTENCY_CONFLICT': 409, 'UNAUTHORIZED': 401,
    'FORBIDDEN': 403, 'CAPABILITY_UNAVAILABLE': 503, 'RESOURCE_BUSY': 503,
    'TIMEOUT': 504, 'DATA_UNREADABLE': 422, 'ANALYSIS_INVALID': 422,
    'APPROVAL_REQUIRED': 403, 'APPROVAL_INVALID': 403, 'VERSION_CONFLICT': 409,
    'OPERATION_IN_PROGRESS': 409, 'INTERNAL': 500,
}


class APIError(Exception):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message or code.replace('_', ' ').capitalize()
        super().__init__(self.message)

    def payload(self, operation_id=None):
        return {'error': {'code': self.code, 'message': self.message,
                          'operation_id': operation_id,
                          'retryable': self.code in {'RESOURCE_BUSY', 'TIMEOUT'}}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise APIError('INVALID_ARGUMENT', 'Expected canonical UUID')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise APIError('INVALID_ARGUMENT', 'Invalid identifier')
    return value


def exact(body, required, optional=()):
    if not isinstance(body, dict) or not set(required) <= body.keys() or body.keys() - (set(required) | set(optional)):
        raise APIError('INVALID_ARGUMENT', 'Missing or unknown request fields')


def target(value):
    exact(value, ('request_id', 'capture_sequence', 'device_id'))
    uuid(value['request_id'])
    identifier(value['device_id'])
    if type(value['capture_sequence']) is not int or value['capture_sequence'] < 0:
        raise APIError('INVALID_ARGUMENT', 'capture_sequence must be nonnegative integer')
    return digest(value)


def action_hash(action, body):
    return digest({'action': action, 'body': body})


def sign_approval(payload, secret):
    """Shared signing format for the independent approval issuer and fixtures."""
    encoded = base64.urlsafe_b64encode(canonical(payload)).rstrip(b'=').decode()
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


class AccessPolicy:
    def __init__(self, tokens, *, control=False, data=False, secret='', previous_secret='',
                 rate_per_minute=60, write_rate_per_minute=10, clock=time.monotonic):
        self.tokens = dict(tokens)
        self.enabled = {'read': True, 'control': control, 'data': data}
        self.secret = secret
        self.previous_secret = previous_secret
        self.rate_per_minute = int(rate_per_minute)
        self.write_rate_per_minute = int(write_rate_per_minute)
        if self.rate_per_minute < 0 or self.write_rate_per_minute < 0:
            raise ValueError('Rate limits must be nonnegative')
        self._clock = clock
        self._rate_lock = Lock()
        self._windows = {}
        nonempty = [v for v in tokens.values() if v]
        if len(nonempty) != len(set(nonempty)):
            raise ValueError('Service scopes must have different tokens')
        if (control or data) and not secret:
            raise ValueError('Enabled writes require WAVEFORM_AGENT_APPROVAL_SECRET')
        for scope, enabled in self.enabled.items():
            if enabled and not tokens.get(scope):
                raise ValueError('Enabled v2 scope requires a service token: ' + scope)

    @classmethod
    def from_env(cls, env):
        return cls({s: env.get('WAVEFORM_AGENT_' + s.upper() + '_TOKEN', '')
                    for s in ('read', 'control', 'data')},
                   control=env.get('WAVEFORM_AGENT_CONTROL_ENABLED', '').lower() == 'true',
                   data=env.get('WAVEFORM_AGENT_DATA_ENABLED', '').lower() == 'true',
                   secret=env.get('WAVEFORM_AGENT_APPROVAL_SECRET', ''),
                   previous_secret=env.get('WAVEFORM_AGENT_APPROVAL_SECRET_PREVIOUS', ''),
                   rate_per_minute=int(env.get('WAVEFORM_AGENT_RATE_LIMIT_PER_MINUTE', '60')),
                   write_rate_per_minute=int(env.get('WAVEFORM_AGENT_WRITE_RATE_LIMIT_PER_MINUTE', '10')))

    def rate_limit(self, scope, method, path):
        # One configured token per scope; no credential is retained in counters.
        limits = [('all', self.rate_per_minute)]
        if method == 'POST' and scope != 'read' and path != PREFIX + '/data/preview':
            limits.append(('write', self.write_rate_per_minute))
        stamp = self._clock()
        with self._rate_lock:
            for kind, maximum in limits:
                start, count = self._windows.get((scope, kind), (stamp, 0))
                if stamp - start >= 60:
                    start, count = stamp, 0
                if maximum and count >= maximum:
                    error = APIError('RESOURCE_BUSY', 'Request rate limit exceeded')
                    error.http_status = 429
                    raise error
                self._windows[(scope, kind)] = (start, count)
            for kind, _ in limits:
                start, count = self._windows[(scope, kind)]
                self._windows[(scope, kind)] = (start, count + 1)

    def authenticate(self, scope, headers):
        authorization = headers.get('Authorization', '')
        if not authorization.startswith('Bearer ') or not authorization[7:]:
            raise APIError('UNAUTHORIZED')
        supplied = authorization[7:]
        expected = self.tokens.get(scope, '')
        if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
            known = any(v and hmac.compare_digest(supplied.encode(), v.encode()) for v in self.tokens.values())
            raise APIError('FORBIDDEN' if known else 'UNAUTHORIZED')
        if not self.enabled[scope]:
            raise APIError('CAPABILITY_UNAVAILABLE', 'Scope is disabled')

    def approval(self, scope, action, body, headers, *, allow_expired=False):
        names = ('X-Operation-Id', 'X-Approval-Id', 'X-Operator-Id', 'X-Approval-Token')
        if any(not headers.get(n) for n in names):
            raise APIError('APPROVAL_REQUIRED')
        try:
            operation_id = uuid(headers['X-Operation-Id'])
            approval_id = uuid(headers['X-Approval-Id'])
            operator_id = identifier(headers['X-Operator-Id'])
            encoded, signature = headers['X-Approval-Token'].split('.')
            valid = False
            for secret in (self.secret, self.previous_secret):
                if secret:
                    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
                    if hmac.compare_digest(signature, expected):
                        valid = True
                        break
            if not valid:
                raise ValueError()
            payload = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
            expires = payload['expires_at']
            if (type(expires) not in (int, float) or not math.isfinite(expires)
                    or (expires <= time.time() and not allow_expired) or payload['approval_id'] != approval_id
                    or payload['operator_id'] != operator_id or payload['scope'] != scope
                    or payload['action'] != action or payload['action_hash'] != action_hash(action, body)):
                raise ValueError()
            for field, value in (('operator_id', operator_id), ('approval_id', approval_id)):
                if field in body and body[field] != value:
                    raise ValueError()
        except (ValueError, KeyError, TypeError, APIError):
            raise APIError('APPROVAL_INVALID') from None
        return {'operation_id': operation_id, 'approval_id': approval_id, 'operator_id': operator_id}
