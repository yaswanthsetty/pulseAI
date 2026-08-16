"""Unit tests for auth cryptographic primitives (spec §21)."""

import hashlib
import time
import uuid

import jwt
import pytest
from backend.core.config import settings
from backend.modules.auth import security
from backend.modules.auth.security import TokenError


class TestPasswords:
    def test_hash_and_verify_roundtrip(self):
        password = "correct horse battery staple"
        hashed = security.hash_password(password)
        assert hashed != password
        assert security.verify_password(password, hashed)

    def test_wrong_password_fails(self):
        hashed = security.hash_password("right-password")
        assert not security.verify_password("wrong-password", hashed)

    def test_malformed_hash_is_false(self):
        assert not security.verify_password("anything", "not-a-bcrypt-hash")

    def test_hashes_are_salted(self):
        assert security.hash_password("same") != security.hash_password("same")


class TestAccessTokens:
    def test_create_and_decode_roundtrip(self):
        user_id = uuid.uuid4()
        token, expires_in = security.create_access_token(
            user_id=user_id, email="a@example.com", role="analyst"
        )
        claims = security.decode_access_token(token)
        assert claims["sub"] == str(user_id)
        assert claims["email"] == "a@example.com"
        assert claims["role"] == "analyst"
        assert claims["type"] == "access"
        assert claims["iss"] == settings.jwt_issuer
        assert claims["aud"] == settings.jwt_audience
        assert expires_in == settings.jwt_access_ttl_minutes * 60

    def test_expired_token_rejected(self):
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "type": "access",
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": now - 3600,
                "exp": now - 60,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            security.decode_access_token(token)

    def test_wrong_secret_rejected(self):
        token, _ = security.create_access_token(
            user_id=uuid.uuid4(), email="a@example.com", role="user"
        )
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": int(time.time()) + 600},
            "a-different-secret-that-is-long-enough-0123456789",
            algorithm="HS256",
        )
        assert security.decode_access_token(token)  # real one still works
        with pytest.raises(TokenError):
            security.decode_access_token(forged)

    def test_tampered_token_rejected(self):
        token, _ = security.create_access_token(
            user_id=uuid.uuid4(), email="a@example.com", role="user"
        )
        tampered = token[:-4] + ("abcd" if not token.endswith("abcd") else "wxyz")
        with pytest.raises(TokenError):
            security.decode_access_token(tampered)

    def test_wrong_token_type_rejected(self):
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "type": "refresh",
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": now,
                "exp": now + 600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(TokenError, match="access token"):
            security.decode_access_token(token)


class TestHashedTokens:
    def test_refresh_token_raw_and_hash(self):
        raw, digest = security.generate_refresh_token()
        assert raw != digest
        assert digest == hashlib.sha256(raw.encode()).hexdigest()
        assert len(raw) > 32

    def test_api_key_format_and_hash(self):
        raw, digest = security.generate_api_key()
        assert raw.startswith(security.API_KEY_PREFIX)
        assert len(raw) > len(security.API_KEY_PREFIX) + 20
        assert digest == hashlib.sha256(raw.encode()).hexdigest()

    def test_hashes_are_unique(self):
        assert security.hash_token("a") != security.hash_token("b")


class TestProviderRoleClaims:
    def test_clerk_public_metadata(self):
        assert security.provider_role({"public_metadata": {"role": "analyst"}}) == "analyst"

    def test_auth0_app_metadata(self):
        assert security.provider_role({"app_metadata": {"role": "admin"}}) == "admin"

    def test_flat_and_namespaced_claims(self):
        assert security.provider_role({"role": "user"}) == "user"
        assert security.provider_role({"https://pulseai/role": "analyst"}) == "analyst"

    def test_missing_or_invalid_role_returns_none(self):
        assert security.provider_role({}) is None
        assert security.provider_role({"public_metadata": {}}) is None
        assert security.provider_role({"role": "superuser"}) is None
        assert security.provider_role({"role": 42}) is None
