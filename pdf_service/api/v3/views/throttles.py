from rest_framework.throttling import UserRateThrottle


class PDFGenerationThrottle(UserRateThrottle):
    """Throttle for PDF generation endpoints to prevent DOS attacks.

    Rate: 20 PDF generations per hour per user.
    This prevents spam requests while allowing legitimate usage.
    """
    scope = 'pdf_generation'


class PDFStatusCheckThrottle(UserRateThrottle):
    """Throttle for PDF status check endpoint.

    Rate: 60 status checks per minute per user.
    More permissive than generation since status checks are lightweight.
    """
    scope = 'pdf_status_check'
