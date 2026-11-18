import logging

from django.db import IntegrityError, OperationalError
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status

from ERP.utils.shop_activity_logs import create_activity_log
from services.api.v3.serializers.stock_adjustment import StockAdjustmentRequestSerializer
from services.constants.stock_adjustment import ACTIVITY_LOG_ACTION, StockAdjustmentMessages as M
from services.models import ProductVariantStockAdjustment
from services.utils.stock_adjustment import StockAdjustmentError, apply_stock_adjustments

from .base import decimal_str
from .docs import POST_RESPONSES, PRODUCT_ID_PARAMETER, TAG

logger = logging.getLogger("api_requests")

Action = ProductVariantStockAdjustment.Action

SUCCESS_MESSAGES = {
    Action.MARK_UNAVAILABLE: M.MARKED_UNAVAILABLE,
    Action.RESTORE: M.RESTORED,
}


@extend_schema(
    tags=[TAG],
    summary="Mark variant stock unavailable or restore it",
    description=(
        "Moves units (stock) and/or fabric length of one or more variants of a product out of service, or back "
        "into service.\n\n"
        "Purpose: Lets a shop take damaged items or items under maintenance out of the usable stock without "
        "losing the physical quantity, and return them once repaired.\n\n"
        "Context: `stock` and `fabric_length` stay the usable amounts that the booking, availability and "
        "tailor-order APIs already use, so those APIs need no change. mark_unavailable subtracts the amount from "
        "`stock` / `fabric_length` and adds it to `unavailable_stock` / `unavailable_fabric_length`; restore does "
        "the reverse. Every moved item is recorded in the adjustment history with its reason. "
        "All items are applied together or none are (no partial updates), and concurrent requests on the same "
        "variants are serialised, so values can never become negative.\n\n"
        "Bookings are not checked: existing bookings are never read or changed. After marking stock unavailable, "
        "dates that are already booked beyond the new usable stock show no availability for new bookings.\n\n"
        "Accepted inputs (JSON body, Content-Type: application/json):\n"
        "Path parameters (required):\n"
        "- product_id: the product to adjust.\n\n"
        "Body - Required:\n"
        "- action (string): mark_unavailable or restore.\n"
        "- variants (array, 1-100 items): each item has variant_id (integer, required) and at least one of "
        "quantity (integer >= 1) or fabric_length (decimal > 0, up to 2 dp).\n\n"
        "Body - Optional:\n"
        "- reason (string | null): damage, maintenance or other. Default: null - no reason is stored.\n"
        "- note (string, <= 500 chars): required when the reason is other.\n"
        "- variants[].reason, variants[].note: per-item overrides of the top-level values."
    ),
    parameters=[PRODUCT_ID_PARAMETER],
    request=StockAdjustmentRequestSerializer,
    responses=POST_RESPONSES,
    examples=[
        OpenApiExample(
            "Mark damaged units of several variants",
            value={
                "action": "mark_unavailable",
                "reason": "damage",
                "note": "Damaged during wedding event",
                "variants": [
                    {"variant_id": 101, "quantity": 2},
                    {"variant_id": 102, "quantity": 1, "reason": "maintenance", "note": "Zip repair"},
                ],
            },
            request_only=True,
        ),
        OpenApiExample(
            "Send fabric for maintenance",
            value={
                "action": "mark_unavailable",
                "reason": "maintenance",
                "note": "Water stain, sent for cleaning",
                "variants": [{"variant_id": 310, "fabric_length": "4.50"}],
            },
            request_only=True,
        ),
        OpenApiExample(
            "Mark unavailable without a reason",
            value={"action": "mark_unavailable", "variants": [{"variant_id": 101, "quantity": 2}]},
            request_only=True,
        ),
        OpenApiExample(
            "Restore one repaired unit",
            value={"action": "restore", "reason": "damage", "note": "Stitched", "variants": [{"variant_id": 101, "quantity": 1}]},
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": M.MARKED_UNAVAILABLE,
                "data": {
                    "batch_id": "9a1e6c1e-3f2b-4c7d-9a43-2f1d8c6b5e10",
                    "action": "mark_unavailable",
                    "product_id": 41,
                    "variants": [{
                        "variant_id": 101, "attribute": "M", "reason": "damage", "note": "Damaged during wedding event",
                        "quantity": 2, "fabric_length": "0.00",
                        "stock": 3, "unavailable_stock": 2, "total_stock": 5,
                        "available_fabric_length": None, "unavailable_fabric_length": "0.00", "total_fabric_length": None,
                    }],
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Not enough usable stock",
            value={
                "status": "validation_error",
                "message": "Only 1 unit(s) of Wedding Gown (M) are available.",
                "data": None,
                "dev_message": "Variant 102: quantity 3 exceeds stock 1.",
            },
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
def create(self, request, product_id=None):
    product = self._get_product(product_id)
    if product is None:
        return self._product_not_found_response()

    serializer = StockAdjustmentRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return self._request_error_response(serializer.errors)

    data = serializer.validated_data
    action = data["action"]
    variant_ids = [item["variant_id"] for item in data["variants"]]

    try:
        batch_id, results = apply_stock_adjustments(
            product=product, action=action, items=data["variants"], user=request.user,
        )
    except StockAdjustmentError as exc:
        logger.warning(
            "Stock adjustment rejected: product=%s action=%s variants=%s reason=%s",
            product.id, action, variant_ids, exc.dev_message,
        )
        return self.validation_error_response(errors=exc.errors, message=exc.message, dev_message=exc.dev_message)
    except (OperationalError, IntegrityError):
        # Lock timeout / deadlock, or a DB check constraint caught a value
        # the validation should have rejected. The transaction was rolled back.
        logger.warning(
            "Stock adjustment conflict: product=%s action=%s variants=%s",
            product.id, action, variant_ids, exc_info=True,
        )
        return self.error_response(
            message=M.CONFLICT.message, dev_message=M.CONFLICT.dev_message, http_status=status.HTTP_409_CONFLICT,
        )
    except Exception:
        logger.exception(
            "Stock adjustment failed: product=%s action=%s variants=%s", product.id, action, variant_ids,
        )
        return self.error_response(
            message=M.SERVER_ERROR.message, dev_message=M.SERVER_ERROR.dev_message,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _log_activity(request.user, product, action, results, batch_id)

    for result in results:
        for field in ("fabric_length", "available_fabric_length", "unavailable_fabric_length", "total_fabric_length"):
            result[field] = decimal_str(result[field])

    return self.success_response(
        data={"batch_id": str(batch_id), "action": action, "product_id": product.id, "variants": results},
        message=SUCCESS_MESSAGES[action],
    )


def _log_activity(user, product, action, results, batch_id):
    """Record the change in the shop activity log; never fails the request."""
    verb = "marked unavailable" if action == Action.MARK_UNAVAILABLE else "restored"
    reasons = ", ".join(sorted({result["reason"] for result in results if result["reason"]}))
    reasons_text = f" ({reasons})" if reasons else ""
    try:
        create_activity_log(
            shop=product.shop_service.shop,
            user=user,
            instance=product,
            action=ACTIVITY_LOG_ACTION,
            message=f"Stock {verb} for {len(results)} variant(s) of '{product.name}'{reasons_text}.",
        )
    except Exception:
        logger.exception("Activity log failed for stock adjustment %s (product=%s)", batch_id, product.id)
