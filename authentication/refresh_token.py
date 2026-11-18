from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from authentication.session_manager import is_token_valid_after_password_change, validate_session_claims



class AdminTokenRefreshView(TokenRefreshView):
    @extend_schema(
        tags=["Token"],
        summary="Admin token refresh",
        description="Refreshes admin access token using refresh token from the refresh_token cookie.",
        parameters=[
            OpenApiParameter(
                name="refresh_token",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.COOKIE,
                required=True,
                description="Admin refresh token stored as HttpOnly cookie.",
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(description="SimpleJWT refresh success payload with a new access token."),
            400: OpenApiResponse(
                description=(
                    "Error messages (point by point):\n"
                    "- No refresh token provided.\n"
                    "- Invalid/expired refresh token errors from SimpleJWT (raised as InvalidToken)."
                )
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication errors from JWT auth layer when credentials are missing or invalid."
                )
            ),
        },
        examples=[
            OpenApiExample(
                "Success response",
                value={"access": "<new_access_token>"},
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing cookie",
                value={"error": "No refresh token provided."},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Invalid token",
                value={
                    "detail": "Token is invalid or expired",
                    "code": "token_not_valid"
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        refresh = request.COOKIES.get('refresh_token')

        if not refresh:
            return Response(
                {"error": "No refresh token provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data={"refresh": refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        return Response(serializer.validated_data, status=status.HTTP_200_OK)



class CutoffTokenRefreshView(TokenRefreshView):
    """
    Custom refresh view that checks password_changed_at
    before issuing a new access token.
    """
    @extend_schema(
        tags=["Token"],
        summary="Custom token refresh with password cutoff",
        description="Refreshes access token using refresh token from body after validating token payload and password_changed_at cutoff.",
        request=inline_serializer(
            name="CutoffTokenRefreshRequest",
            fields={
                "refresh": serializers.CharField(required=True, help_text="JWT refresh token string."),
            },
        ),
        responses={
            200: OpenApiResponse(description="SimpleJWT refresh success payload with a new access token."),
            401: OpenApiResponse(
                description=(
                    "Error messages (point by point):\n"
                    "- No refresh token provided\n"
                    "- Invalid refresh token\n"
                    "- Invalid refresh token payload\n"
                    "- User not found\n"
                    "- Refresh token invalidated by password change\n"
                    "- Additional SimpleJWT refresh validation errors returned by super().post(...)"
                )
            ),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={"refresh": "<refresh_token>"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={"access": "<new_access_token>"},
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing refresh",
                value={"detail": "No refresh token provided", "code": "no_refresh"},
                response_only=True,
                status_codes=["401"],
            ),
            OpenApiExample(
                "Password changed cutoff",
                value={"detail": "Refresh token invalidated by password change", "code": "refresh_invalidated"},
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        # Grab the raw refresh token from the request body
        refresh_token_str = request.data.get("refresh")
        if not refresh_token_str:
            raise AuthenticationFailed("No refresh token provided", code="no_refresh")

        try:
            token = RefreshToken(refresh_token_str)
        except Exception:
            raise AuthenticationFailed("Invalid refresh token", code="invalid_refresh")

        user_id = token.get("user_id")
        iat = token.get("iat")
        if not user_id or not iat:
            raise AuthenticationFailed("Invalid refresh token payload", code="invalid_payload")

        session_id = token.get("session_id")

        if session_id is not None:
            # New-style token: a revoked session's refresh token must not be
            # able to mint a working access token again.
            result = validate_session_claims(token)
            if result is False:
                raise AuthenticationFailed(
                    "Session invalidated", code="refresh_invalidated"
                )
            if result is None:
                # Redis disabled/unreachable - fall back to the DB check
                # below rather than rejecting outright (must not turn a
                # Redis outage into an auth outage).
                if not is_token_valid_after_password_change(user_id, iat):
                    raise AuthenticationFailed(
                        "Refresh token invalidated by password change",
                        code="refresh_invalidated"
                    )
            # result is True -> valid, fall through to the refresh below.
        else:
            # Pre-rollout token (or one issued while Redis was down at
            # login): fall back to the original password_changed_at check
            # exactly as before, so already-logged-in users keep working.
            if not is_token_valid_after_password_change(user_id, iat):
                raise AuthenticationFailed(
                    "Refresh token invalidated by password change",
                    code="refresh_invalidated"
                )

        response = super().post(request, *args, **kwargs)

        # Stock TokenRefreshSerializer doesn't copy custom claims from the
        # refresh token onto the newly-minted access token, so re-embed them
        # explicitly here rather than relying on any implicit copy behavior.
        if session_id is not None and response.status_code == status.HTTP_200_OK:
            access_str = response.data.get("access")
            if access_str:
                access = AccessToken(access_str)
                access["session_id"] = session_id
                access["session_version"] = token.get("session_version")
                response.data["access"] = str(access)

        return response

