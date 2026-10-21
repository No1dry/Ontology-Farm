"""Presentation negotiation only; real provider security is exercised in E2E."""
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from starlette.requests import Request
from lzo.auth import router
from lzo.errors import ApiError


def request(accept):
    result = Request({'type': 'http', 'method': 'GET', 'path': '/api/v1/session/callback',
        'headers': [(b'accept', accept.encode())], 'query_string': b'',
        'app': SimpleNamespace(state=SimpleNamespace(engine=None, settings=SimpleNamespace(session_lifetime_seconds=300)))})
    result.state.request_id = 'request-123'
    return result


@pytest.fixture
def protocol(monkeypatch):
    monkeypatch.setattr(router, 'PgUnitOfWork', lambda _: nullcontext(SimpleNamespace(commit=lambda: None)))
    def configure(error=None):
        class Service:
            def __init__(self, *_): pass
            def callback(self, *_):
                if error: raise error
                return 'opaque-test-cookie'
        monkeypatch.setattr(router, 'SessionService', Service)
    return configure


@pytest.mark.parametrize('accept,location', [
    ('text/html,application/xhtml+xml,*/*;q=0.8', '/auth/complete'),
    ('text/html;q=0.5', '/auth/complete'), ('*/*', '/api/v1/session'),
    ('application/json', '/api/v1/session'), ('text/html;q=0,*/*', '/api/v1/session'),
    ('text/html;q=invalid', '/api/v1/session'),
])
def test_callback_negotiates_explicit_acceptable_html(protocol, accept, location):
    protocol()
    response = router.callback(request(accept), 'state', 'code')
    assert response.status_code == 303
    assert response.headers['location'] == location
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'


@pytest.mark.parametrize('code', ['OIDC_STATE_INVALID', 'OIDC_TOKEN_INVALID', 'OIDC_UNAVAILABLE', 'UNAUTHENTICATED'])
def test_known_browser_failures_recover_without_echoing_protocol_values(protocol, code):
    protocol(ApiError(code, 401, 'private-provider-body'))
    response = router.callback(request('text/html'), 'private-state', 'private-code')
    assert response.headers['location'] == '/auth/error?code=' + code + '&request_id=request-123'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert '__Host-lzo_session' not in str(response.headers)
    assert '__Host-lzo_login=' in response.headers['set-cookie']


def test_non_html_callback_preserves_json_error_contract(protocol):
    protocol(ApiError('OIDC_STATE_INVALID', 401, 'Invalid state'))
    with pytest.raises(ApiError) as error:
        router.callback(request('*/*'), 'state', 'code')
    assert error.value.code == 'OIDC_STATE_INVALID'
