from rest_framework.throttling import UserRateThrottle


class PreviewRateThrottle(UserRateThrottle):
    """PDF preview rendering is CPU heavy - limit per user."""
    scope = "invoice_template_preview"


class ShareLookupRateThrottle(UserRateThrottle):
    """Share-code lookup/import - slows down brute-force guessing of codes."""
    scope = "invoice_template_share_lookup"
