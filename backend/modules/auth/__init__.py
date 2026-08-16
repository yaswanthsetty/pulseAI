"""Auth module — Phase 1.5 (spec §21-23).

Managed-auth-provider integration (Clerk/Auth0) with identity sync into the
``users`` table, local register/login as a fallback, 15-minute access JWTs with
rotating 30-day refresh tokens (httpOnly cookie), hashed-at-rest API keys,
the ``require_role``/``require_scope`` RBAC dependencies (§22), Redis
sliding-window rate limiting, and double-submit CSRF protection.
"""
