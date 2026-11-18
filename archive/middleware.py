"""
ANALYTICS app only: block every change from the API, so the copied data can
only be read. Turned on by ANALYTICS_MODE=true in .env.
"""

from django.conf import settings
from django.http import JsonResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Write calls that must still work (login, token refresh, Django admin).
ALLOWED_PATH_PARTS = ("/auth/", "/token/refresh/", "/admin-token/refresh/", "/admin-dashboard/")


class AnalyticsReadOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "ANALYTICS_MODE", False)

    def __call__(self, request):
        if (
            self.enabled
            and request.method not in SAFE_METHODS
            and not any(part in request.path for part in ALLOWED_PATH_PARTS)
        ):
            return JsonResponse(
                {"status": "error", "message": "This is the analytics app. Changes are not allowed.", "data": None},
                status=403,
            )
        return self.get_response(request)