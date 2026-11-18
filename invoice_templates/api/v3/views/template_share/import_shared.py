from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action

from invoice_templates.v3.services.configuration import ConfigurationError
from invoice_templates.v3.services.custom_invoices import CustomInvoiceError
from invoice_templates.v3.services.sharing import ShareError, import_share

from ...serializers import ImportShareRequestSerializer
from ...utils import docs, messages
from ...utils.throttles import ShareLookupRateThrottle
from ..custom_invoice.helpers import DETAIL_EXAMPLE, DETAIL_SHAPE, USAGE_SHAPE, default_invoice_id, invoice_detail


@extend_schema(
    tags=[docs.TAG],
    summary="Import a share code as a new customized invoice",
    description=(
        "Creates a NEW customized invoice in the active shop from a share code - same template, "
        "copied settings, and a name.\n\n"
        "Purpose: Reuse another shop's / branch's customized invoice (code typed or scanned from "
        "its QR).\n\n"
        "Context: The import counts toward the shop's limit of 4 customized invoices and is "
        "rejected when the shop already has 4. The new invoice is an independent copy - later "
        "changes in either shop never affect the other. Existing customized invoices are not "
        "touched. The default changes only when set_as_default is true. A shop cannot import its "
        "own code. A code is valid for 24 hours after it is generated (expires_at from generate): "
        "an expired code is rejected with its own error (error_code "
        f"\"{messages.SHARE_CODE_EXPIRED_ERROR_CODE}\") and nothing is imported. Validity is checked "
        "again at the moment of import, so a code that expires or is revoked during the request "
        "imports nothing. Rate limited to 20 lookups/imports per minute per user.\n\n"
        "Accepted inputs:\n"
        "Request body - Required:\n"
        "- share_code (string).\n"
        "Request body - Optional:\n"
        "- name (string, 1-60 characters, unique in the shop). Default: the shared invoice's name, "
        "made unique.\n"
        "- set_as_default (boolean, default false).\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    request=ImportShareRequestSerializer,
    responses={
        201: OpenApiResponse(description=(
            f"Created. Exact message: {messages.SHARE_IMPORTED}\n\n"
            "Response shape (data): the new customized invoice -\n" + DETAIL_SHAPE + "\n" + USAGE_SHAPE
        )),
        400: OpenApiResponse(description=(
            "Validation error. Exact messages:\n"
            + messages.bullet(
                messages.SHARE_CODE_REQUIRED, messages.INVALID_SHARE_CODE, messages.SHARE_CODE_EXPIRED,
                messages.OWN_SHARE_CODE, messages.limit_reached_message(), messages.NAME_TAKEN,
                messages.INVALID_NAME, messages.INVALID_SETTINGS,
            )
            + f"\n\nThe expired response also carries error_code \"{messages.SHARE_CODE_EXPIRED_ERROR_CODE}\"."
            + "\nWhen the limit is reached, data.usage carries {used, limit, remaining}."
        )),
        403: docs.R403,
        429: OpenApiResponse(description=f"Rate limited. Exact message: {messages.SHARE_THROTTLED}"),
        500: docs.R500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={
            "share_code": "K7QM-2XRP-9HTD", "name": "Wedding (from main branch)", "set_as_default": False,
        }),
        docs.envelope_example("Created", messages.SHARE_IMPORTED, status_codes=["201"], data={
            **DETAIL_EXAMPLE, "usage": {"used": 2, "limit": 4, "remaining": 2},
        }),
        OpenApiExample("Expired code", response_only=True, status_codes=["400"], value={
            "status": "error",
            "message": messages.SHARE_CODE_EXPIRED,
            "data": None,
            "dev_message": messages.SHARE_CODE_EXPIRED_DEV,
            "error_code": messages.SHARE_CODE_EXPIRED_ERROR_CODE,
        }),
    ],
)
@action(detail=False, methods=["post"], url_path="import", throttle_classes=[ShareLookupRateThrottle])
def import_shared(self, request):
    try:
        serializer = ImportShareRequestSerializer(data=request.data)
        if not serializer.is_valid():
            if "name" in serializer.errors:
                return self.error_response(message=messages.INVALID_NAME, dev_message=messages.INVALID_NAME_DEV)
            return self.error_response(message=messages.SHARE_CODE_REQUIRED, dev_message=messages.SHARE_CODE_REQUIRED_DEV)
        data = serializer.validated_data
        try:
            share, invoice, shop_usage = import_share(
                self.shop_id, data["share_code"], request.user,
                name=data.get("name"), set_as_default=data["set_as_default"],
            )
        except ShareError as e:
            return self.share_error_response(e)
        except ConfigurationError as e:
            return self.field_errors_response(messages.INVALID_SETTINGS, e.errors, messages.INVALID_SETTINGS_DEV)
        except CustomInvoiceError as e:
            return self.custom_invoice_error_response(e)

        return self.success_response(
            data={**invoice_detail(invoice, default_invoice_id(self.shop_id)), "usage": shop_usage},
            message=messages.SHARE_IMPORTED,
            http_status=status.HTTP_201_CREATED,
        )
    except Exception:
        return self.server_error(f"Invoice share import failed for shop {self.shop_id}")
