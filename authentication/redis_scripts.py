# redis_scripts.py
"""
Redis connection + Lua scripts backing the session-management system (see
authentication/session_manager.py and authentication/tokens.py).

Key layout (all keys embed user_id and/or a globally-unique session_id, so no
operation here can ever touch another user's keys):

    auth:user:{user_id}:version    STRING(int) - all-devices invalidation counter
    auth:user:{user_id}:sessions   ZSET(member=session_id, score=created_at epoch) - ordering for oldest-eviction
    auth:session:{session_id}      HASH(user_id, created_at, expires_at, platform) - per-session record + existence check
"""
import logging

from django.conf import settings
from django_redis import get_redis_connection

logger = logging.getLogger("api_requests")

# Redis is an optional acceleration layer for session management, never a
# hard dependency - see authentication/session_manager.py for the fallback
# behavior when this is False or when Redis is simply unreachable. Set
# REDIS_ENABLED=false to skip attempting Redis entirely (e.g. local dev
# without a Redis server running) rather than waiting on a connection error.
REDIS_ENABLED = getattr(settings, "REDIS_ENABLED", True)

SESSION_VERSION_KEY = "auth:user:{user_id}:version"
USER_SESSIONS_KEY = "auth:user:{user_id}:sessions"
SESSION_KEY = "auth:session:{session_id}"


def get_redis_client():
    """Raw redis-py client from the 'default' django-redis cache, for pipelines/Lua scripts."""
    return get_redis_connection("default")


def _refresh_lifetime_seconds() -> int:
    return int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())


# --- Login-with-eviction script -------------------------------------------------
#
# Atomically: count active sessions -> if at/over the cap, evict the oldest
# (lowest-score ZSET member) and delete its session record -> add the new
# session -> refresh TTLs -> read-or-seed the user's version counter and
# return it. Running this as one EVAL makes concurrent logins for the same
# user race-free: Redis executes Lua scripts as a single atomic unit under
# its single-threaded command execution, so two simultaneous logins can
# never both observe "under the cap" and both add a session, blowing past
# the limit. Folding the version read/seed into the SAME script as the
# eviction also means a login can never partially succeed - either the
# whole thing (eviction + new session + version) commits, or none of it
# does, so a Redis error can't leave an old device evicted while the new
# login silently falls back to a non-Redis-tracked (legacy) token.
#
# The version key (KEYS[2]) intentionally gets NO TTL here - it's a small
# per-user counter, negligible even unbounded at this scale, and giving it
# an expiry (as an earlier version of this script did, matching
# logout_all's TTL) meant it would silently disappear ~logout_all's TTL
# after the LAST logout-all event, mass-invalidating every session
# (including ones created well after that event) the moment it expired.
#
# KEYS[1] = user_sessions zset key
# KEYS[2] = version key
# ARGV[1] = max_allowed_sessions
# ARGV[2] = new_session_id
# ARGV[3] = now (epoch seconds, used as the ZADD score)
# ARGV[4] = ttl_seconds (applied to the zset key and the new session hash)
# ARGV[5] = user_id
# ARGV[6] = expires_at (epoch seconds, stored in the session hash)
# ARGV[7] = platform (may be empty string)
# ARGV[8] = db_session_version (used to seed the version key only if it doesn't exist yet)
LOGIN_WITH_EVICTION_SCRIPT = """
local zset_key = KEYS[1]
local version_key = KEYS[2]
local max_allowed = tonumber(ARGV[1])
local new_sid = ARGV[2]
local now = ARGV[3]
local ttl = ARGV[4]
local user_id = ARGV[5]
local expires_at = ARGV[6]
local platform = ARGV[7]
local db_version = ARGV[8]

local count = redis.call('ZCARD', zset_key)
if count >= max_allowed then
    local oldest = redis.call('ZRANGE', zset_key, 0, 0)
    if oldest[1] then
        redis.call('ZREM', zset_key, oldest[1])
        redis.call('DEL', 'auth:session:' .. oldest[1])
    end
end

redis.call('ZADD', zset_key, now, new_sid)
redis.call('EXPIRE', zset_key, ttl)

local session_key = 'auth:session:' .. new_sid
redis.call('HSET', session_key, 'user_id', user_id, 'created_at', now, 'expires_at', expires_at, 'platform', platform)
redis.call('EXPIRE', session_key, ttl)

local current_version = redis.call('GET', version_key)
if current_version == false then
    redis.call('SET', version_key, db_version)
    current_version = db_version
end

return current_version
"""

# --- Logout-all-devices script ---------------------------------------------------
#
# Write-through the new (already DB-persisted, durable) session_version into
# Redis and clear the user's session registry so "list active sessions"
# immediately reports zero. Individual auth:session:{sid} hashes are left to
# their own TTL rather than enumerated/deleted here — harmless, since the
# version-mismatch check alone already rejects every one of them at the next
# request regardless of whether the hash itself still exists.
#
# The version key is deliberately given NO TTL (see LOGIN_WITH_EVICTION_SCRIPT's
# comment above) - it must persist indefinitely, not expire and silently
# mass-invalidate future sessions that have nothing to do with this event.
#
# KEYS[1] = version key
# KEYS[2] = user_sessions zset key
# ARGV[1] = new_version
LOGOUT_ALL_SCRIPT = """
redis.call('SET', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[2])
return redis.status_reply('OK')
"""


class RedisScripts:
    """Lazily-registered Lua scripts, bound to a single redis-py client."""

    def __init__(self):
        self._client = None
        self._login_with_eviction = None
        self._logout_all = None

    def _ensure_registered(self):
        if self._client is not None:
            return
        self._client = get_redis_client()
        self._login_with_eviction = self._client.register_script(LOGIN_WITH_EVICTION_SCRIPT)
        self._logout_all = self._client.register_script(LOGOUT_ALL_SCRIPT)

    def login_with_eviction(self, user_id, session_id, max_allowed_sessions, now_epoch, expires_at_epoch, db_session_version, platform=""):
        """
        Atomically evicts the oldest session if the user is at/over their
        cap, registers the new session, and reads-or-seeds the user's
        version counter - all in one Lua script (see LOGIN_WITH_EVICTION_SCRIPT
        for why this is folded into a single atomic call). Returns the
        current session_version (int).
        """
        self._ensure_registered()
        ttl = _refresh_lifetime_seconds()
        zset_key = USER_SESSIONS_KEY.format(user_id=user_id)
        version_key = SESSION_VERSION_KEY.format(user_id=user_id)
        result = self._login_with_eviction(
            keys=[zset_key, version_key],
            args=[max_allowed_sessions, session_id, now_epoch, ttl, user_id, expires_at_epoch, platform or "", db_session_version],
        )
        return int(result)

    def logout_all(self, user_id, new_version):
        self._ensure_registered()
        version_key = SESSION_VERSION_KEY.format(user_id=user_id)
        zset_key = USER_SESSIONS_KEY.format(user_id=user_id)
        self._logout_all(keys=[version_key, zset_key], args=[new_version])


redis_scripts = RedisScripts()
