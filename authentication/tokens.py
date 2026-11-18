# tokens.py
"""
Login/refresh token-building helpers. Wraps SimpleJWT's RefreshToken/AccessToken
(no hand-rolled JWT encoding) and embeds the session claims used by the
Redis-backed session system (see session_manager.py). Access and refresh
tokens carry the SAME session_id/session_version, so an evicted session's
access token is rejected on its very next request, not only on refresh.
"""
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.session_manager import create_session


def issue_session_tokens(user, platform: str = ""):
    """
    Create a new session (enforcing the per-user concurrent-session cap,
    evicting the oldest session if needed) and return (refresh, access)
    SimpleJWT token objects.

    If Redis is disabled/unavailable, create_session returns None and this
    issues plain, legacy-style tokens (no session_id/session_version claims)
    instead - login must never fail because Redis is down. Such tokens are
    validated via the password_changed_at fallback for their whole life (see
    CutoffJWTAuthentication) rather than gaining a session once Redis
    returns - simpler and safer than retroactively attaching a session.
    """
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token

    claims = create_session(user, platform=platform)
    if claims is not None:
        refresh["session_id"] = claims["session_id"]
        refresh["session_version"] = claims["session_version"]
        access["session_id"] = claims["session_id"]
        access["session_version"] = claims["session_version"]

    return refresh, access
