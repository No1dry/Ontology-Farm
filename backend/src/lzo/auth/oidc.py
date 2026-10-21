"""Authlib authorization-code + S256 and strict OIDC ID-token verification."""
import ssl

import httpx
from authlib.integrations.httpx_client import OAuth2Client
from authlib.integrations.base_client.errors import OAuthError
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry
from joserfc.errors import JoseError
from authlib.oidc.core import CodeIDToken
from authlib.oauth2 import OAuth2Error

from lzo.errors import ApiError


def tls_verify(settings):
    return ssl.create_default_context(cafile=str(settings.ca_bundle)) if settings.ca_bundle else True


def validate_id_token(encoded, jwks, issuer, client_id, nonce, access_token=None):
    try:
        token = jwt.decode(encoded, KeySet.import_key_set(jwks),
                           registry=JWSRegistry(algorithms=["RS256"], strict_check_header=False))
        claims = CodeIDToken(token.claims, token.header,
            options={"iss": {"essential": True, "value": issuer},
                            "aud": {"essential": True, "value": client_id}},
            params={"nonce": nonce, "client_id": client_id, "access_token": access_token})
        claims.validate(leeway=30)
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError()
        return dict(claims)
    except (JoseError, ValueError, TypeError, KeyError):
        raise ApiError("OIDC_TOKEN_INVALID", 401, "Identity token validation failed") from None


class OIDCClient:
    def __init__(self, settings):
        self.settings = settings

    def backchannel(self, url):
        if self.settings.oidc_backchannel_base and url.startswith(self.settings.oidc_issuer + "/"):
            return self.settings.oidc_backchannel_base.rstrip("/") + url[len(self.settings.oidc_issuer):]
        return url

    def metadata(self):
        try:
            with httpx.Client(verify=tls_verify(self.settings), timeout=10) as client:
                response = client.get(self.settings.oidc_discovery_url)
                response.raise_for_status()
                metadata = response.json()
            if metadata["issuer"] != self.settings.oidc_issuer:
                raise ValueError()
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri", "end_session_endpoint"):
                if not metadata[field].startswith(self.settings.oidc_issuer + "/"):
                    raise ValueError()
            return metadata
        except (httpx.HTTPError, ValueError, KeyError):
            raise ApiError("OIDC_UNAVAILABLE", 503, "Identity provider unavailable") from None

    def client(self, **kwargs):
        return OAuth2Client(self.settings.oidc_client_id, self.settings.oidc_client_secret,
            scope="openid profile email", redirect_uri=self.settings.public_origin.rstrip("/") + "/api/v1/session/callback",
            code_challenge_method="S256", verify=tls_verify(self.settings), timeout=10, **kwargs)

    def authorization_url(self, state, nonce, verifier):
        metadata = self.metadata()
        with self.client() as client:
            url, _ = client.create_authorization_url(metadata["authorization_endpoint"],
                state=state, nonce=nonce, code_verifier=verifier)
        return url

    def exchange(self, code, nonce, verifier):
        metadata = self.metadata()
        try:
            with self.client() as client:
                tokens = dict(client.fetch_token(self.backchannel(metadata["token_endpoint"]), code=code, code_verifier=verifier))
            claims = self.claims(tokens, nonce, metadata)
            return tokens, claims
        except (httpx.HTTPError, OAuth2Error, OAuthError, KeyError, ValueError):
            raise ApiError("OIDC_TOKEN_INVALID", 401, "Authorization exchange failed") from None

    def claims(self, tokens, nonce, metadata=None):
        metadata = metadata or self.metadata()
        try:
            with httpx.Client(verify=tls_verify(self.settings), timeout=10) as client:
                response = client.get(self.backchannel(metadata["jwks_uri"]))
                response.raise_for_status()
                keys = response.json()
            return validate_id_token(tokens["id_token"], keys, self.settings.oidc_issuer,
                                     self.settings.oidc_client_id, nonce, tokens.get("access_token"))
        except (httpx.HTTPError, KeyError, ValueError):
            raise ApiError("OIDC_TOKEN_INVALID", 401, "Identity token validation failed") from None

    def refresh(self, tokens):
        metadata = self.metadata()
        try:
            with self.client(token=tokens) as client:
                refreshed = dict(client.refresh_token(self.backchannel(metadata["token_endpoint"]), refresh_token=tokens["refresh_token"]))
            claims = self.claims(refreshed, None, metadata)
            return refreshed, claims
        except (httpx.HTTPError, OAuth2Error, OAuthError, KeyError, ValueError):
            raise ApiError("SESSION_EXPIRED", 401, "Session refresh failed") from None

    def logout(self, tokens):
        try:
            metadata = self.metadata()
            with httpx.Client(verify=tls_verify(self.settings), timeout=10) as client:
                response = client.post(self.backchannel(metadata["end_session_endpoint"]), data={
                    "client_id": self.settings.oidc_client_id,
                    "client_secret": self.settings.oidc_client_secret,
                    "refresh_token": tokens["refresh_token"]})
                response.raise_for_status()
            return True
        except (httpx.HTTPError, ApiError, KeyError):
            return False
