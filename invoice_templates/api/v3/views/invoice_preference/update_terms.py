from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from invoice_templates.v3.services.preferences import TermsError, validate_terms_payload
from invoice_templates.v3.services.preferences import update_terms as save_terms

from ...serializers import UpdateTermsRequestSerializer
from ...utils import docs, messages
from .get_preference import PREFERENCE_EXAMPLE_DATA, PREFERENCE_SHAPE
from .helpers import preference_payload


@extend_schema(
    tags=[docs.TAG],
    summary="Update Terms and Conditions per document type",
    description=(
        "Sets separate Terms and Conditions for Booking, Sale and Custom Work (tailor order) "
        "invoices of the active shop.\n\n"
        "Purpose: Each invoice type can print its own terms.\n\n"
        "Context: Only the keys sent are changed. null or [] clears a document type so its "
        "invoices use the default terms for that type (see effective_terms in the response). "
        "These terms are printed on customized invoices and on previews.\n\n"
        "Accepted inputs:\n"
        "Request body - at least one of (each optional):\n"
        "- booking (array of strings | null)\n"
        "- sale (array of strings | null)\n"
        "- tailor_order (array of strings | null)\n"
        "Each array: at most 30 items, each 1-300 characters (trimmed).\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    request=UpdateTermsRequestSerializer,
    responses={
        200: OpenApiResponse(description=f"Success. Exact message: {messages.TERMS_UPDATED}\n\n" + PREFERENCE_SHAPE),
        400: OpenApiResponse(description=(
            "Validation error. Exact messages:\n"
            + messages.bullet(messages.TERMS_REQUIRED, messages.INVALID_TERMS)
            + "\n\nPer-key messages inside data.validation_errors:\n"
            + messages.bullet(
                "'<key>' is not a document type. Allowed: booking, sale, tailor_order.",
                "Must be a list of strings or null.",
                "At most 30 terms are allowed.",
                "Item <n> must be non-empty text.",
                "Item <n> must be at most 300 characters.",
                "Request body must be an object.",
            )
        )),
        403: docs.R403,
        500: docs.R500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={
            "booking": ["No refund after cancellation.", "Return products on the agreed date."],
            "sale": None,
        }),
        docs.envelope_example("Success", messages.TERMS_UPDATED, data=PREFERENCE_EXAMPLE_DATA),
    ],
)
@action(detail=False, methods=["patch"], url_path="terms")
def update_terms(self, request):
    try:
        data = request.data
        if not isinstance(data, dict) or not data:
            return self.error_response(message=messages.TERMS_REQUIRED, dev_message=messages.TERMS_REQUIRED_DEV)
        try:
            updates = validate_terms_payload(dict(data))
        except TermsError as e:
            return self.field_errors_response(messages.INVALID_TERMS, e.errors, messages.INVALID_TERMS_DEV)
        if not updates:
            return self.error_response(message=messages.TERMS_REQUIRED, dev_message=messages.TERMS_REQUIRED_DEV)

        save_terms(self.shop_id, updates, request.user)
        return self.success_response(data=preference_payload(self.shop_id), message=messages.TERMS_UPDATED)
    except Exception:
        return self.server_error(f"Invoice terms update failed for shop {self.shop_id}")
