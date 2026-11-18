import logging
from django.db import transaction
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from subscriptions.models import ShopFeatureUsage, ShopFeatureUsageResetLog
from .list import _serialize

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Reset shop feature usage",
    description=(
        "Resets the usage count for a feature to 0 or any specified non-negative value. "
        "The original value is preserved in an audit log (ShopFeatureUsageResetLog) "
        "with the admin user, timestamp, and optional reason."
    ),
    parameters=[
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopFeatureUsage id."),
    ],
    request=inline_serializer(
        name="SubscriptionsAdminV3ResetShopFeatureUsageRequest",
        fields={
            "value": serializers.IntegerField(
                required=True,
                help_text="Target usage value. Must be a non-negative integer. Pass 0 to fully reset.",
            ),
            "reason": serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="Optional reason for the reset (stored in audit log).",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Usage reset successfully."),
        400: OpenApiResponse(description="Exact messages: value is required. / value must be a non-negative integer."),
        404: OpenApiResponse(description="Exact message: Record not found."),
        500: OpenApiResponse(description="Exact message: Could not reset usage."),
    },
    examples=[
        OpenApiExample(
            "Reset to zero",
            value={"value": 0, "reason": "End of billing period"},
            request_only=True,
        ),
        OpenApiExample(
            "Set to specific value",
            value={"value": 50, "reason": "Correcting data entry error"},
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Usage reset successfully.",
                "data": {
                    "id": 1, "shop": 3, "shop_name": "ABC Boutique",
                    "feature": 4, "feature_code": "WHATSAPP_MSG", "feature_name": "WhatsApp Messaging",
                    "used": 0, "period_start": "2026-06-01T00:00:00Z", "period_end": "2026-06-30T23:59:59Z",
                    "reset_log": {
                        "id": 7,
                        "previous_value": 12,
                        "new_value": 0,
                        "reset_by": 5,
                        "reason": "End of billing period",
                        "reset_at": "2026-06-26T10:30:00Z",
                    },
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@action(detail=False, methods=["post"], url_path=r"reset/(?P<pk>[^/.]+)")
@transaction.atomic
def reset(self, request, pk=None):
    try:
        # Validate `value`
        raw_value = request.data.get("value")
        if raw_value is None or raw_value == "":
            return self.validation_error_response(
                errors={"value": ["This field is required."]},
                message="value is required.",
            )

        try:
            new_value = int(raw_value)
            if new_value < 0:
                raise ValueError
        except (ValueError, TypeError):
            return self.validation_error_response(
                errors={"value": ["Must be a non-negative integer."]},
                message="value must be a non-negative integer.",
            )

        reason = request.data.get("reason", "") or ""

        # Fetch record
        try:
            obj = (
                ShopFeatureUsage.objects
                .select_related("shop", "feature")
                .get(pk=pk)
            )
        except ShopFeatureUsage.DoesNotExist:
            return self.not_found_response("ShopFeatureUsage", message="Record not found.")

        previous_value = obj.used

        # Apply reset
        obj.used = new_value
        obj.save(update_fields=["used"])

        # Write audit log
        log = ShopFeatureUsageResetLog.objects.create(
            usage=obj,
            reset_by=request.user,
            previous_value=previous_value,
            new_value=new_value,
            reason=reason,
        )

        return self.success_response(
            data={
                **_serialize(obj),
                "reset_log": {
                    "id": log.id,
                    "previous_value": log.previous_value,
                    "new_value": log.new_value,
                    "reset_by": log.reset_by_id,
                    "reason": log.reason,
                    "reset_at": log.reset_at,
                },
            },
            message="Usage reset successfully.",
            dev_message=f"ShopFeatureUsage #{pk}: {previous_value} → {new_value}",
        )

    except Exception as e:
        logger.error(f"ShopFeatureUsageViewSet.reset:\n{e}", exc_info=True)
        return self.error_response(
            message="Could not reset usage.",
            dev_message=str(e),
        )
