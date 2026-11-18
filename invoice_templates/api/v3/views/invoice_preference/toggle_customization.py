from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from invoice_templates.v3.services.preferences import set_customization_enabled

from ...serializers import ToggleCustomizationRequestSerializer
from ...utils import docs, messages
from .get_preference import PREFERENCE_EXAMPLE_DATA, PREFERENCE_SHAPE
from .helpers import preference_payload


@extend_schema(
    tags=[docs.TAG],
    summary="Turn customized invoices on or off for the shop",
    description=(
        "Switches the active shop between its default customized invoice and the existing "
        "invoice design.\n\n"
        "Purpose: Lets the owner go back to the existing invoice at any time, and return to the "
        "customized one later.\n\n"
        "Context: Nothing is deleted - customized invoices, the default selection, terms and "
        "share codes are all kept, so turning it on again restores the customized invoices. "
        "When enabled, real invoices use the default customized invoice only while the platform-wide "
        "switch is also on (global_customization_enabled).\n\n"
        "Accepted inputs:\n"
        "Request body - Required:\n"
        "- enabled (boolean).\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    request=ToggleCustomizationRequestSerializer,
    responses={
        200: OpenApiResponse(description=f"Success. Exact message: {messages.CUSTOMIZATION_UPDATED}\n\n" + PREFERENCE_SHAPE),
        400: OpenApiResponse(description=f"Validation error. Exact message: {messages.ENABLED_REQUIRED}"),
        403: docs.R403,
        500: docs.R500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={"enabled": False}),
        docs.envelope_example("Success", messages.CUSTOMIZATION_UPDATED, data=PREFERENCE_EXAMPLE_DATA),
    ],
)
@action(detail=False, methods=["patch"], url_path="customization")
def toggle_customization(self, request):
    try:
        enabled = request.data.get("enabled") if isinstance(request.data, dict) else None
        if not isinstance(enabled, bool):
            # Strict JSON boolean - "false"/0 strings are rejected, never guessed.
            return self.error_response(message=messages.ENABLED_REQUIRED, dev_message=messages.ENABLED_REQUIRED_DEV)
        set_customization_enabled(self.shop_id, enabled, request.user)
        return self.success_response(data=preference_payload(self.shop_id), message=messages.CUSTOMIZATION_UPDATED)
    except Exception:
        return self.server_error(f"Invoice customization toggle failed for shop {self.shop_id}")
