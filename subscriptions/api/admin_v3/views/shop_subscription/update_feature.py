from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from django.db import transaction
from subscriptions.models import ShopSubscription, ShopFeatureSubscription, ShopFeatureLimit
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware, is_naive, get_default_timezone
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Update subscribed feature",
    description="Updates one feature attached to a subscription and optionally updates/creates usage_limit.",
    parameters=[
        OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop subscription id."),
        OpenApiParameter("feature_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Feature id linked to subscription."),
    ],
    request=inline_serializer(
        name="SubscriptionsAdminV3UpdateFeatureRequest",
        fields={
            "price_paid": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "discount_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "start_date": serializers.DateTimeField(required=False),
            "end_date": serializers.DateTimeField(required=False),
            "is_active": serializers.BooleanField(required=False),
            "discount_reason": serializers.CharField(required=False, allow_blank=True),
            "usage_limit": serializers.JSONField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Feature subscription updated successfully. or No updatable fields provided."),
        400: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- Subscription ID is required.\n"
                "- Feature ID is required.\n"
                "- Price cannot be negative.\n"
                "- Invalid price_paid value.\n"
                "- Discount amount cannot be negative.\n"
                "- Invalid discount_amount value.\n"
                "- Invalid start_date format. Use ISO format (YYYY-MM-DDTHH:MM:SSZ).\n"
                "- Invalid end_date format. Use ISO format (YYYY-MM-DDTHH:MM:SSZ).\n"
                "- End date must be same or after start date.\n"
                "- usage_limit must be an object.\n"
                "- usage_limit.extra_limit is required.\n"
                "- usage_limit.extra_limit must be an integer.\n"
                "- usage_limit.valid_until has invalid format (ISO 8601 required).\n"
                "- usage_limit.is_active must be a boolean.\n"
                "- usage_limit.reason cannot exceed 200 characters."
            )
        ),
        404: OpenApiResponse(description="Exact messages: Subscription not found. / Feature not found on this subscription."),
        500: OpenApiResponse(description="Exact message: Unable to update feature right now. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"price_paid": "79.00", "discount_amount": "10.00", "is_active": True, "usage_limit": {"extra_limit": 500, "valid_until": "2026-02-01T00:00:00", "is_active": True, "reason": "Retention"}}, request_only=True),
        OpenApiExample("Success response", value={"message": "Feature subscription updated successfully.", "data": {"feature_id": 7, "feature_name": "Advanced Reports", "price_paid": "79.00", "usage_limit": {"action": "updated", "extra_limit": 500}}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=True, methods=['patch'], url_path=r'update-feature/(?P<feature_id>[^/.]+)')
@transaction.atomic
def update_feature(self, request, pk=None, feature_id=None):
    """
    PATCH /api/v3/subscriptions/<subscription_id>/features/<feature_id>/

    Body (any of):
    {
      "price_paid": "199.00",
      "discount_amount": "100.00",
      "start_date": "2025-11-15T00:00:00Z",
      "end_date": "2026-11-15T00:00:00Z",
      "is_active": true,
      "discount_reason": "Loyalty",
      "usage_limit": {  // OPTIONAL - to update or create usage limit
        "extra_limit": 1000,
        "valid_until": "2026-11-15T00:00:00",  // optional
        "is_active": true,                      // optional
        "reason": "Promotional bonus"           // optional
      }
    }

    """
    try:
        # Validate pk (subscription id)
        if not pk:
            return Response(
                {"message": "Subscription ID is required.", "dev_message": "pk parameter missing"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            subscription = ShopSubscription.objects.select_for_update().select_related("shop").get(pk=int(pk))
        except (ShopSubscription.DoesNotExist, ValueError):
            return Response(
                {"message": "Subscription not found.", "dev_message": f"ShopSubscription id={pk} not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate feature_id
        if not feature_id:
            return Response(
                {"message": "Feature ID is required.", "dev_message": "feature_id parameter missing"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch the through-model row and lock it
        try:
            sfs = ShopFeatureSubscription.objects.select_for_update().select_related("feature").get(
                shop_subscription=subscription,
                feature_id=int(feature_id)
            )
        except (ShopFeatureSubscription.DoesNotExist, ValueError):
            return Response(
                {"message": "Feature not found on this subscription.", "dev_message": f"feature_id={feature_id} not linked to subscription {pk}"},
                status=status.HTTP_404_NOT_FOUND
            )

        data = request.data
        update_fields = []
        dev_changes = []

        # price_paid
        if "price_paid" in data:
            try:
                pp = Decimal(str(data.get("price_paid")))
                if pp < 0:
                    return Response(
                        {"message": "Price cannot be negative."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                sfs.price_paid = pp
                update_fields.append("price_paid")
                dev_changes.append(f"price_paid->{pp}")
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {"message": "Invalid price_paid value."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # discount_amount
        if "discount_amount" in data:
            try:
                da = Decimal(str(data.get("discount_amount")))
                if da < 0:
                    return Response(
                        {"message": "Discount amount cannot be negative."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                sfs.discount_amount = da
                update_fields.append("discount_amount")
                dev_changes.append(f"discount_amount->{da}")
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {"message": "Invalid discount_amount value."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # start_date
        if "start_date" in data:
            start_date_str = data.get("start_date")
            if start_date_str:
                sd = parse_datetime(start_date_str)
                if not sd:
                    return Response(
                        {"message": "Invalid start_date format. Use ISO format (YYYY-MM-DDTHH:MM:SSZ)."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if is_naive(sd):
                    sd = make_aware(sd, get_default_timezone())
                sfs.start_date = sd
                update_fields.append("start_date")
                dev_changes.append(f"start_date->{sd.isoformat()}")

        # end_date
        if "end_date" in data:
            end_date_str = data.get("end_date")
            if end_date_str:
                ed = parse_datetime(end_date_str)
                if not ed:
                    return Response(
                        {"message": "Invalid end_date format. Use ISO format (YYYY-MM-DDTHH:MM:SSZ)."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if is_naive(ed):
                    ed = make_aware(ed, get_default_timezone())
                sfs.end_date = ed
                update_fields.append("end_date")
                dev_changes.append(f"end_date->{ed.isoformat()}")

        # Validate date ordering
        effective_start = sfs.start_date
        effective_end = sfs.end_date
        if effective_start and effective_end and effective_end < effective_start:
            return Response(
                {"message": "End date must be same or after start date."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # is_active
        if "is_active" in data:
            new_active = True if str(data.get("is_active")).lower() in ("1", "true", "yes") else False
            if sfs.is_active != new_active:
                sfs.is_active = new_active
                update_fields.append("is_active")
                dev_changes.append(f"is_active->{new_active}")

        # discount_reason
        if "discount_reason" in data:
            reason = (data.get("discount_reason") or "").strip()
            sfs.discount_reason = reason if reason else None
            update_fields.append("discount_reason")
            dev_changes.append(f"discount_reason->{sfs.discount_reason}")

        # Handle usage_limit (OPTIONAL)
        usage_limit_response = None
        if "usage_limit" in data:
            usage_limit_data = data.get("usage_limit")

            if usage_limit_data is not None:
                if not isinstance(usage_limit_data, dict):
                    return Response(
                        {"message": "usage_limit must be an object."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate extra_limit (required if usage_limit provided)
                extra_limit_raw = usage_limit_data.get('extra_limit')
                if extra_limit_raw is None:
                    return Response(
                        {"message": "usage_limit.extra_limit is required."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                try:
                    extra_limit = int(extra_limit_raw)
                    # extra_limit can be positive or negative (no range restriction)
                except (TypeError, ValueError):
                    return Response(
                        {"message": "usage_limit.extra_limit must be an integer."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate valid_until (optional)
                valid_until = None
                valid_until_str = usage_limit_data.get('valid_until') or None
                if valid_until_str:
                    valid_until = parse_datetime(valid_until_str)
                    if not valid_until:
                        return Response(
                            {"message": "usage_limit.valid_until has invalid format (ISO 8601 required)."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    # Ensure timezone awareness
                    if is_naive(valid_until):
                        valid_until = make_aware(valid_until, get_default_timezone())

                # Validate is_active (optional, defaults to True)
                limit_is_active = usage_limit_data.get('is_active', True)
                if not isinstance(limit_is_active, bool):
                    return Response(
                        {"message": "usage_limit.is_active must be a boolean."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate reason (optional, max 200 chars)
                limit_reason = (usage_limit_data.get('reason') or '').strip()
                if len(limit_reason) > 200:
                    return Response(
                        {"message": "usage_limit.reason cannot exceed 200 characters."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Try to fetch existing limit or create new one
                try:
                    shop_feature_limit = ShopFeatureLimit.objects.select_for_update().get(
                        shop=subscription.shop,
                        feature=sfs.feature
                    )
                    # Update existing limit
                    limit_update_fields = []
                    if shop_feature_limit.extra_limit != extra_limit:
                        shop_feature_limit.extra_limit = extra_limit
                        limit_update_fields.append("extra_limit")

                    if shop_feature_limit.valid_until != valid_until:
                        shop_feature_limit.valid_until = valid_until
                        limit_update_fields.append("valid_until")

                    if shop_feature_limit.is_active != limit_is_active:
                        shop_feature_limit.is_active = limit_is_active
                        limit_update_fields.append("is_active")

                    if shop_feature_limit.reason != limit_reason:
                        shop_feature_limit.reason = limit_reason
                        limit_update_fields.append("reason")

                    if limit_update_fields:
                        shop_feature_limit.save(update_fields=limit_update_fields)
                        dev_changes.append(f"usage_limit_updated: {', '.join(limit_update_fields)}")

                    usage_limit_response = {
                        "feature_id": sfs.feature.id,
                        "feature_name": sfs.feature.name,
                        "extra_limit": shop_feature_limit.extra_limit,
                        "valid_until": shop_feature_limit.valid_until.isoformat() if shop_feature_limit.valid_until else None,
                        "is_active": shop_feature_limit.is_active,
                        "reason": shop_feature_limit.reason,
                        "action": "updated"
                    }

                except ShopFeatureLimit.DoesNotExist:
                    # Create new limit
                    shop_feature_limit = ShopFeatureLimit.objects.create(
                        shop=subscription.shop,
                        feature=sfs.feature,
                        extra_limit=extra_limit,
                        valid_until=valid_until,
                        is_active=limit_is_active,
                        reason=limit_reason
                    )
                    dev_changes.append("usage_limit_created")

                    usage_limit_response = {
                        "feature_id": sfs.feature.id,
                        "feature_name": sfs.feature.name,
                        "extra_limit": shop_feature_limit.extra_limit,
                        "valid_until": shop_feature_limit.valid_until.isoformat() if shop_feature_limit.valid_until else None,
                        "is_active": shop_feature_limit.is_active,
                        "reason": shop_feature_limit.reason,
                        "action": "created"
                    }

        if not update_fields and not usage_limit_response:
            return Response(
                {"message": "No updatable fields provided."},
                status=status.HTTP_200_OK
            )

        # Save feature subscription with only specified fields
        if update_fields:
            sfs.save(update_fields=update_fields)

        # Build response
        resp = {
            "feature_id": sfs.feature.id,
            "feature_name": sfs.feature.name,
            "price_paid": str(sfs.price_paid),
            "discount_amount": str(sfs.discount_amount),
            "discount_reason": sfs.discount_reason,
            "start_date": sfs.start_date.isoformat() if sfs.start_date else None,
            "end_date": sfs.end_date.isoformat() if sfs.end_date else None,
            "is_active": bool(sfs.is_active),
            "base_price_at_purchase": str(sfs.base_price_at_purchase)
        }

        # Add usage_limit info to data if it was updated/created
        if usage_limit_response:
            resp["usage_limit"] = usage_limit_response

        response_data = {
            "message": "Feature subscription updated successfully.",
            "data": resp,
            "dev_message": f"Updated fields: {', '.join(dev_changes)}"
        }

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Failed updating feature {feature_id} for subscription {pk}: {e}")
        return Response(
            {
                "message": "Unable to update feature right now. Please try again later.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
