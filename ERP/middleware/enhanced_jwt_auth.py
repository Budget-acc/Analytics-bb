from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from authentication.session_manager import (
    is_token_valid_after_password_change,
    validate_session_claims,
)


class CutoffJWTAuthentication(JWTAuthentication):
    """
    Session-aware JWT authentication. Redis is an optional acceleration
    layer, never a hard dependency - see authentication/session_manager.py.

    - `session_id` present + Redis available -> validated against Redis
      (session existence + session_version), no DB query on this hot path.
    - `session_id` present + Redis disabled/unreachable -> falls back to the
      password_changed_at DB check rather than rejecting the token or
      raising a 500. A Redis outage must never become an authentication
      outage.
    - `session_id` absent (pre-rollout token, or issued while Redis was down
      at login time) -> password_changed_at DB check, same as before this
      feature existed.
    """

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)

        session_id = token.get("session_id")
        if session_id is not None:
            result = validate_session_claims(token)
            if result is True:
                return token
            if result is False:
                raise AuthenticationFailed(
                    "Session invalidated", code="token_invalidated"
                )
            # result is None -> Redis disabled/unreachable, fall through to
            # the legacy DB check below rather than rejecting outright.

        user_id = token.get("user_id")
        iat = token.get("iat")
        if not is_token_valid_after_password_change(user_id, iat):
            raise AuthenticationFailed(
                "Token invalidated by password change", code="token_invalidated"
            )

        return token
