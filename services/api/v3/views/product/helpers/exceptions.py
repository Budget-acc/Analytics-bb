import logging
from rest_framework.throttling import UserRateThrottle

logger = logging.getLogger("api_requests")


# ============================================================================
# VALIDATION ERROR HANDLER — shared by create.py and partial_update.py
# ============================================================================
def _handle_product_validation_error(self, ve, context="Product"):
    """
    Centralised handler for ValidationError exceptions raised during product
    create and update flows (e.g. from _validate_external_qr_codes and
    _validate_registration_number_uniqueness).

    Args:
        ve: The caught ValidationError instance.
        context: Log prefix string, e.g. "Product Create" or "Product Update".
    """
    logger.error(f"[{context}] Validation error: {ve}", exc_info=True)
    detail = ve.detail if hasattr(ve, "detail") else str(ve)

    if isinstance(detail, dict) and "external_qr_code" in detail:
        return self.validation_error_response(
            errors=detail,
            message="Code already exists."
        )
    if isinstance(detail, dict) and "registration_number" in detail:
        return self.validation_error_response(
            errors=detail,
            message="A product with this registration number already exists in your shop."
        )

    errors = detail if isinstance(detail, dict) else {"non_field_errors": [str(detail)]}
    return self.validation_error_response(
        errors=errors,
        message="Validation failed. Please check your input and try again."
    )


# ============================================================================
# CUSTOM EXCEPTIONS FOR BETTER ERROR HANDLING
# ============================================================================
class InvalidAttributeKeyError(Exception):
    """Raised when an invalid or malicious attribute key is provided."""
    pass


class AttributeSearchError(Exception):
    """Raised when attribute search encounters an error."""
    pass


class ServiceMismatchError(Exception):
    """Raised when attribute doesn't belong to expected service (security issue)."""
    pass


# ============================================================================
# RATE LIMITING FOR SEARCH OPERATIONS
# ============================================================================
class ProductSearchThrottle(UserRateThrottle):
    """
    Rate limit for product search operations.

    Protects against:
    - Search spam/abuse
    - Expensive database queries from complex attribute searches
    - CPU-intensive Cast and Exists operations
    - DoS attacks via repeated complex queries

    Rate: configured in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["product_search"].
    """
    scope = 'product_search'
