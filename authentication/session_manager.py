# session_manager.py
"""
Session create/evict/validate helpers backing the Redis-based session system.
Used by:
  - authentication/tokens.py (login: create_session)
  - ERP/middleware/enhanced_jwt_auth.py (every request: validate_session_claims)
  - authentication/refresh_token.py (refresh: validate_session_claims)
  - authentication/api/v3/views/change_password.py (logout-all: logout_all_devices)

Redis is treated as an optional performance/session-management layer, never
a hard dependency: every public function here fails OPEN (falls back to
None/False rather than raising) when Redis is disabled (REDIS_ENABLED=false)
or unreachable. Callers must distinguish these outcomes from a genuine
"invalid session" - see each function's docstring - and fall back to the
existing password_changed_at DB check, which remains the authoritative
safety net for authentication regardless of Redis's availability.
"""
import logging
import secrets
import time

from django.conf import settings
from redis.exceptions import RedisError

from authentication.redis_scripts import (
    REDIS_ENABLED,
    SESSION_KEY,
    SESSION_VERSION_KEY,
    get_redis_client,
    redis_scripts,
)

logger = logging.getLogger("api_requests")


def _decode(value):
    """
    Redis connections here don't set decode_responses, so GET/HGET return
    bytes - decode before comparing/using as text. (str(b'42') == "b'42'",
    NOT "42" - comparing raw bytes against a claim's str/int form without
    this always mismatches.)
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def generate_session_id() -> str:
    return secrets.token_urlsafe(24)


def create_session(user, platform: str = ""):
    """
    Atomically enforce the per-user session cap (evicting the oldest session
    if needed), register a new session, and read-or-seed the user's version
    counter - all in a single Lua script call (see LOGIN_WITH_EVICTION_SCRIPT),
    so this can never partially succeed (e.g. evict an old device but then
    fail to hand back a version, or vice versa).

    Returns {"session_id": ..., "session_version": ...} on success, or None
    if Redis is disabled/unavailable. Login must NEVER fail because of this -
    callers should issue a legacy-style token (no session claims) when None
    is returned, so it validates via the password_changed_at fallback
    everywhere instead. Per this design, only logins that happen while Redis
    is actually up get a Redis-backed session; a token issued during a Redis
    outage stays on the legacy path for its whole life, which is simpler and
    safer than trying to retroactively attach a session once Redis returns.
    """
    if not REDIS_ENABLED:
        return None

    session_id = generate_session_id()
    try:
        now = int(time.time())
        ttl_seconds = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
        expires_at = now + ttl_seconds

        # max_allowed_sessions=0 is a valid, deliberately-set admin value
        # meaning "block all new sessions for this user" - `or 1` would
        # silently treat that the same as a limit of 1, so only substitute
        # the default when the field is genuinely unset (None).
        max_allowed = user.max_allowed_sessions if user.max_allowed_sessions is not None else 1

        session_version = redis_scripts.login_with_eviction(
            user_id=user.id,
            session_id=session_id,
            max_allowed_sessions=max_allowed,
            now_epoch=now,
            expires_at_epoch=expires_at,
            db_session_version=user.session_version,
            platform=platform,
        )
    except RedisError as e:
        logger.warning(f"[session] Redis unavailable during login session creation for user {user.id}: {e}")
        return None

    return {"session_id": session_id, "session_version": session_version}


def logout_all_devices(user) -> bool:
    """
    Best-effort Redis write-through for logout-all-devices: write-throughs
    the (already DB-persisted, durable) session_version into Redis and
    clears the user's session registry.

    Returns True if it succeeded, False if Redis is disabled/unavailable.
    Callers must NOT fail the request either way - the caller is expected to
    have already saved user.session_version/password_changed_at to the DB
    BEFORE calling this, since that DB write is the authoritative
    invalidation mechanism; this call is purely an optimization so Redis
    reflects the change immediately instead of only converging once affected
    tokens hit the password_changed_at fallback.
    """
    if not REDIS_ENABLED:
        return False
    try:
        redis_scripts.logout_all(user_id=user.id, new_version=user.session_version)
        return True
    except RedisError as e:
        logger.warning(f"[session] Redis unavailable during logout-all for user {user.id}: {e}")
        return False


def validate_session_claims(token):
    """
    Shared hot-path check used by both CutoffJWTAuthentication (every
    request) and CutoffTokenRefreshView (refresh).

    Returns:
      True  - session is confirmed valid (exists, version matches).
      False - session is confirmed invalid/revoked/mismatched. Callers MUST
              reject the request on False.
      None  - Redis is disabled or unreachable; validity could not be
              determined. Callers MUST NOT treat this as invalid - fall back
              to the password_changed_at DB check instead. A Redis outage
              must never become an authentication outage.
    """
    if not REDIS_ENABLED:
        return None

    session_id = token.get("session_id")
    user_id = token.get("user_id")
    if not session_id or not user_id:
        return False

    try:
        client = get_redis_client()
        pipe = client.pipeline()
        pipe.get(SESSION_VERSION_KEY.format(user_id=user_id))
        pipe.hget(SESSION_KEY.format(session_id=session_id), "user_id")
        current_version, session_user_id = pipe.execute()
    except RedisError as e:
        logger.warning(f"[session] Redis unavailable during session validation for user {user_id}: {e}")
        return None

    current_version = _decode(current_version)
    session_user_id = _decode(session_user_id)

    if current_version is None:
        return False
    if session_user_id is None or str(session_user_id) != str(user_id):
        return False

    try:
        token_version = int(token.get("session_version", -1))
    except (TypeError, ValueError):
        return False

    return token_version == int(current_version)


def is_token_valid_after_password_change(user_id, iat) -> bool:
    """
    The original/legacy password_changed_at check (unchanged logic from
    before session management existed), factored out so it can serve two
    roles: (1) the sole check for pre-rollout tokens with no session_id
    claim, and (2) the fallback safety net for new-style tokens when Redis
    is disabled/unreachable. Returns True (valid) unless the token
    provably predates a password change.
    """
    from authentication.models import User  # local import: avoid any app-loading-order issues

    if not iat or not user_id:
        return True

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return True

    if not user.password_changed_at:
        return True

    import pytz
    from datetime import datetime

    token_iat = datetime.fromtimestamp(iat, tz=pytz.UTC)
    password_changed_at_ist = user.password_changed_at.astimezone(pytz.timezone("Asia/Kolkata"))
    return not (token_iat < password_changed_at_ist)
