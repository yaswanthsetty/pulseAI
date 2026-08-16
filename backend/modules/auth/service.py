"""Auth service — user lifecycle, token issuance, and API-key management.

Implements the spec §21 flow: local register/login (``auth_provider=none``)
or managed-provider identity sync (``auth_provider=clerk|auth0``), 15-minute
access tokens, rotating 30-day refresh tokens, and hashed-at-rest API keys.
Every auth event is written to ``audit_log`` (§23).
"""

import ipaddress
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.audit import write_audit
from backend.core.config import settings
from backend.db.models import ApiKey, RefreshToken, User
from backend.modules.auth import security

logger = logging.getLogger(__name__)

# RBAC role hierarchy (spec §22): user < analyst < admin. Guests are simply
# unauthenticated and handled by the optional-user dependency.
ROLE_ORDER = {"user": 1, "analyst": 2, "admin": 3}
VALID_SCOPES = ("read", "chat", "reports")


class AuthError(ValueError):
    """Base for expected auth failures (mapped to HTTP 4xx by the router)."""


class InvalidCredentialsError(AuthError):
    """Email/password did not match (401)."""


class UserExistsError(AuthError):
    """Registration email already taken (409)."""


class InvalidTokenError(AuthError):
    """Refresh token missing, revoked, or expired (401)."""


class UserNotFoundError(AuthError):
    """Role-change target does not exist (404)."""


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str  # raw — carried only in the httpOnly cookie
    expires_in: int
    user: User


def _client_ip(host: str | None) -> str | None:
    """Return *host* only when it is a real IP literal (``audit_log.ip_address`` is INET)."""
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Local user lifecycle (auth_provider=none)
# ---------------------------------------------------------------------------


def register_user(
    db: Session, *, email: str, password: str, display_name: str | None = None
) -> User:
    """Create a local account with role ``user`` (spec §20)."""
    if settings.auth_provider != "none":
        raise AuthError("Registration is handled by the managed identity provider")
    email = email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise UserExistsError("An account with this email already exists")
    user = User(
        email=email,
        password_hash=security.hash_password(password),
        display_name=display_name,
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    write_audit(db, "user_registered", user_id=str(user.id), metadata={"email": email})
    logger.info("user registered: %s (%s)", email, user.id)
    return user


def authenticate_user(
    db: Session, *, email: str, password: str, ip_address: str | None = None
) -> User:
    """Verify credentials; raises InvalidCredentialsError on any mismatch."""
    email = email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if (
        user is None
        or not user.password_hash
        or not security.verify_password(password, user.password_hash)
    ):
        write_audit(
            db,
            "login_failed",
            ip_address=_client_ip(ip_address),
            metadata={"email": email},
        )
        raise InvalidCredentialsError("Invalid email or password")
    if not user.is_active:
        raise InvalidCredentialsError("Account is disabled")
    return user


# ---------------------------------------------------------------------------
# Token issuance / rotation
# ---------------------------------------------------------------------------


def issue_tokens(
    db: Session,
    user: User,
    *,
    ip_address: str | None = None,
    audit_action: str = "login",
) -> IssuedTokens:
    """Create an access JWT and a new refresh-token row (hashed at rest).

    The caller names the audit event (``login`` for login/session,
    ``refresh`` for rotation) so the audit log records what actually happened.
    """
    access_token, expires_in = security.create_access_token(
        user_id=user.id, email=user.email, role=user.role
    )
    raw_refresh, refresh_hash = security.generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_ttl_days),
        )
    )
    db.commit()
    write_audit(db, audit_action, user_id=str(user.id), ip_address=_client_ip(ip_address))
    return IssuedTokens(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
        user=user,
    )


def rotate_refresh_token(
    db: Session, raw_refresh: str, *, ip_address: str | None = None
) -> IssuedTokens:
    """Verify a refresh token, revoke it, and issue a fresh pair (§20 rotation)."""
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(raw_refresh))
    )
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise InvalidTokenError("Invalid or expired refresh token")
    row.revoked_at = datetime.now(UTC)
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        db.commit()
        raise InvalidTokenError("Account is disabled")
    return issue_tokens(db, user, ip_address=ip_address, audit_action="refresh")


def revoke_refresh_token(db: Session, raw_refresh: str, *, ip_address: str | None = None) -> bool:
    """Revoke a refresh token (logout). False when it is already gone/revoked."""
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(raw_refresh))
    )
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(UTC)
    db.commit()
    write_audit(db, "logout", user_id=str(row.user_id), ip_address=_client_ip(ip_address))
    return True


# ---------------------------------------------------------------------------
# Managed-provider identity sync (Clerk / Auth0, §21)
# ---------------------------------------------------------------------------


def sync_managed_user(db: Session, claims: dict) -> User:
    """Upsert a user from verified provider claims.

    Matched by email (unique). New accounts get the role from provider
    metadata (``public_metadata.role`` / ``app_metadata.role``) or ``user``;
    existing accounts keep their role unless the provider explicitly claims
    one, and always get their display name refreshed.
    """
    email = (claims.get("email") or "").lower().strip()
    if not email:
        raise InvalidTokenError("Provider token carries no email claim")
    name = claims.get("name") or claims.get("nickname") or claims.get("given_name")
    claimed_role = security.provider_role(claims)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name=name, role=claimed_role or "user")
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("managed user synced (new): %s", email)
        write_audit(db, "user_synced", user_id=str(user.id), metadata={"email": email})
    else:
        changed = False
        if name and user.display_name != name:
            user.display_name = name
            changed = True
        if claimed_role and user.role != claimed_role:
            user.role = claimed_role
            changed = True
        if changed:
            db.commit()
            db.refresh(user)
            write_audit(db, "user_synced", user_id=str(user.id), metadata={"email": email})
    return user


# ---------------------------------------------------------------------------
# Principal resolution (Bearer header or access cookie)
# ---------------------------------------------------------------------------


def resolve_principal(db: Session, token: str) -> tuple[User | None, str, ApiKey | None]:
    """Resolve a credential to ``(user, method, api_key)``.

    ``method`` is one of ``"jwt"`` (PulseAI access token), ``"provider"``
    (managed-provider JWT, verified via JWKS and synced), or ``"api_key"``
    (``pls_`` prefixed). Returns ``(None, method, None)`` for invalid tokens.
    """
    if not token:
        return None, "jwt", None

    if token.startswith(security.API_KEY_PREFIX):
        row = db.scalar(select(ApiKey).where(ApiKey.key_hash == security.hash_token(token)))
        if row is None or row.revoked_at is not None:
            return None, "api_key", None
        user = db.get(User, row.user_id)
        if user is None or not user.is_active:
            return None, "api_key", None
        row.last_used_at = datetime.now(UTC)
        db.commit()
        return user, "api_key", row

    user: User | None = None
    try:
        claims = security.decode_access_token(token)
        subject = claims.get("sub")
        if subject:
            user = db.get(User, uuid.UUID(str(subject)))
    except security.TokenError, ValueError:
        user = None

    if user is None and settings.auth_provider != "none":
        try:
            claims = security.decode_provider_token(token)
            user = sync_managed_user(db, claims)
            if user is not None:
                return user, "provider", None
        except security.TokenError, InvalidTokenError:
            # Bad signature/issuer, or a valid token whose claims cannot map to
            # a user (e.g. no email) — treat as unauthenticated, never a 500.
            user = None

    if user is not None and not user.is_active:
        return None, "jwt", None
    return user, "jwt", None


# ---------------------------------------------------------------------------
# API keys (developer persona, §5/§21)
# ---------------------------------------------------------------------------


def create_api_key(
    db: Session, user: User, *, label: str | None = None, scopes: list[str] | None = None
) -> tuple[ApiKey, str]:
    """Create an API key; returns ``(row, raw_key)`` — raw shown exactly once."""
    key_scopes = list(scopes) if scopes else ["read"]
    invalid = [s for s in key_scopes if s not in VALID_SCOPES]
    if invalid:
        raise AuthError(f"Invalid scope(s): {', '.join(invalid)}")
    raw, key_hash = security.generate_api_key()
    row = ApiKey(user_id=user.id, key_hash=key_hash, label=label, scopes=key_scopes)
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        "api_key_created",
        user_id=str(user.id),
        target_type="api_key",
        target_id=str(row.id),
        metadata={"label": label, "scopes": key_scopes},
    )
    return row, raw


def list_api_keys(db: Session, user: User) -> list[ApiKey]:
    """Active (non-revoked) keys for *user*, newest first. Never returns raw keys."""
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
            .order_by(ApiKey.created_at.desc())
        )
    )


def revoke_api_key(db: Session, user: User, key_id: uuid.UUID) -> bool:
    """Revoke a key owned by *user*. False when it does not exist / is revoked."""
    row = db.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)
        )
    )
    if row is None:
        return False
    row.revoked_at = datetime.now(UTC)
    db.commit()
    write_audit(
        db,
        "api_key_revoked",
        user_id=str(user.id),
        target_type="api_key",
        target_id=str(row.id),
    )
    return True


# ---------------------------------------------------------------------------
# Admin: user/role management (§22 — Manage users/roles)
# ---------------------------------------------------------------------------


def set_user_role(db: Session, actor: User, target_user_id: uuid.UUID, role: str) -> User:
    """Change a user's role; writes a ``role_change`` audit event (§23)."""
    if role not in ROLE_ORDER:
        raise AuthError(f"Unknown role: {role}")
    target = db.get(User, target_user_id)
    if target is None:
        raise UserNotFoundError("User not found")
    if target.id == actor.id and role != "admin":
        raise AuthError("Admins cannot demote themselves")
    previous = target.role
    target.role = role
    db.commit()
    db.refresh(target)
    write_audit(
        db,
        "role_change",
        user_id=str(actor.id),
        target_type="user",
        target_id=str(target.id),
        metadata={"from": previous, "to": role},
    )
    logger.info("role changed: %s %s -> %s (by %s)", target.id, previous, role, actor.id)
    return target
