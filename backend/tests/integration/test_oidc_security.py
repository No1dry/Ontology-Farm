"""Real signatures exercise the ID-token boundary without an OAuth mock."""
import time

import pytest
from joserfc.jwk import RSAKey
from joserfc import jwt
from lzo.errors import ApiError


@pytest.mark.parametrize("claim,value", [
    ("iss", "https://wrong-issuer"), ("aud", "other-client"), ("nonce", "wrong-nonce"),
    ("azp", "other-client"), ("exp", 1),
])
def test_rejects_signed_tokens_with_wrong_binding(claim, value):
    from lzo.auth.oidc import validate_id_token
    key = RSAKey.generate_key(2048)
    payload = {"iss": "https://issuer", "sub": "synthetic", "aud": "lzo-api",
               "nonce": "expected-nonce", "iat": int(time.time()), "exp": int(time.time()) + 60}
    payload[claim] = value
    encoded = jwt.encode({"alg": "RS256"}, payload, key)
    with pytest.raises(ApiError) as failure:
        validate_id_token(encoded, {"keys": [key.as_dict(private=False)]},
                          "https://issuer", "lzo-api", "expected-nonce")
    assert failure.value.code == "OIDC_TOKEN_INVALID"


def test_valid_signed_token_preserves_subject():
    from lzo.auth.oidc import validate_id_token
    key = RSAKey.generate_key(2048)
    payload = {"iss": "https://issuer", "sub": "synthetic", "aud": "lzo-api",
               "nonce": "expected-nonce", "iat": int(time.time()), "exp": int(time.time()) + 60}
    encoded = jwt.encode({"alg": "RS256"}, payload, key)
    assert validate_id_token(encoded, {"keys": [key.as_dict(private=False)]},
                            "https://issuer", "lzo-api", "expected-nonce")["sub"] == "synthetic"


def test_untrusted_signature_is_rejected_even_when_claims_match():
    from lzo.auth.oidc import validate_id_token
    signing_key, trusted_key = RSAKey.generate_key(2048), RSAKey.generate_key(2048)
    payload = {"iss": "https://issuer", "sub": "synthetic", "aud": "lzo-api",
               "nonce": "expected-nonce", "iat": int(time.time()), "exp": int(time.time()) + 60}
    encoded = jwt.encode({"alg": "RS256"}, payload, signing_key)
    with pytest.raises(ApiError) as invalid:
        validate_id_token(encoded, {"keys": [trusted_key.as_dict(private=False)]},
                          "https://issuer", "lzo-api", "expected-nonce")
    assert invalid.value.code == "OIDC_TOKEN_INVALID"


def test_session_tokens_are_encrypted_and_expired_sessions_rejected(scope_scenario, settings):
    from cryptography.fernet import Fernet
    from sqlalchemy import select, update
    from datetime import datetime, timedelta, timezone
    from lzo.auth.session import SessionService
    from lzo.auth.models import sessions
    s = scope_scenario
    config = settings.model_copy(update={"session_encryption_key": Fernet.generate_key().decode()})
    with s.transaction() as uow:
        service = SessionService(uow, config)
        cookie = service.create(s.users["alice"], {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"})
        row = uow.session.execute(select(sessions)).mappings().one()
        assert "synthetic" not in row["encrypted_tokens"]
        assert cookie not in row["id_hash"]
        assert service.read(cookie)["user_id"] == s.users["alice"]
        uow.session.execute(update(sessions).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        with pytest.raises(ApiError) as failure:
            service.read(cookie)
        assert failure.value.status == 401


def test_login_state_is_browser_bound_and_consumed_after_mismatch(scope_scenario, settings):
    from lzo.auth.session import SessionService
    from urllib.parse import urlparse, parse_qs
    s = scope_scenario
    with s.transaction() as uow:
        service = SessionService(uow, settings)
        browser, url = service.login()
        state = parse_qs(urlparse(url).query)["state"][0]
        assert parse_qs(urlparse(url).query)["code_challenge_method"] == ["S256"]
        uow.commit()
        with pytest.raises(ApiError) as invalid:
            service.consume_login(browser, "wrong-state")
        assert invalid.value.code == "OIDC_STATE_INVALID"
        with pytest.raises(ApiError):
            service.consume_login(browser, state)


def test_identity_service_account_cannot_administer_clients(settings):
    import httpx
    from lzo.auth.oidc import tls_verify
    with httpx.Client(verify=tls_verify(settings), timeout=15) as client:
        token = client.post((settings.oidc_backchannel_base or settings.oidc_issuer) + "/protocol/openid-connect/token",
            data={"grant_type": "client_credentials", "client_id": settings.identity_admin_client_id,
                  "client_secret": settings.identity_admin_client_secret})
        assert token.status_code == 200
        response = client.get(settings.identity_admin_url + "/clients",
            headers={"Authorization": "Bearer " + token.json()["access_token"]})
        assert response.status_code == 403


def test_real_provider_refresh_rejection_invalidates_local_session(scope_scenario, settings):
    from lzo.auth.session import SessionService
    s = scope_scenario
    with s.transaction() as uow:
        service = SessionService(uow, settings)
        cookie = service.create(s.users["alice"], {"access_token": "synthetic-invalid",
                                                 "refresh_token": "synthetic-invalid", "token_type": "Bearer"})
        uow.commit()
        row = service.read(cookie, lock=True)
        with pytest.raises(ApiError) as failed:
            service.refresh(row)
        assert failed.value.status == 401
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            SessionService(uow, settings).read(cookie)
