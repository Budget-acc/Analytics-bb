from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from django.db import transaction
from subscriptions.models import ShopSubscription, SubscriptionPlan
from subscriptions.api.admin_v3.serializers import ShopSubscriptionSerializer
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware, is_naive, get_default_timezone
from django.utils.timezone import timedelta
from decimal import Decimal
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Update subscription",
    description="Partially updates plan, status, dates, paid amount, payment status, and auto-renew for a subscription.",
    parameters=[OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop subscription id.")],
    request=inline_serializer(
        name="SubscriptionsAdminV3UpdateSubscriptionRequest",
        fields={
            "plan_id": serializers.IntegerField(required=False),
            "status": serializers.CharField(required=False),
            "start_date": serializers.DateTimeField(required=False),
            "end_date": serializers.DateTimeField(required=False),
            "plan_price_paid": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "payment_status": serializers.BooleanField(required=False),
            "auto_renew": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Subscription updated successfully. or No updatable fields provided."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Invalid subscription id.\n"
                "- Invalid plan_id provided.\n"
                "- Invalid status. Must be one of: {valid statuses}.\n"
                "- Invalid start_date format. Use ISO format (YYYY-MM-DDTHH:MM:SS).\n"
                "- Invalid end_date format. Use ISO format (YYYY-MM-DDTHH:MM:SS).\n"
                "- End date must be same or after start date.\n"
                "- Invalid plan_price_paid."
            )
        ),
        404: OpenApiResponse(description="Exact message: Subscription not found."),
        500: OpenApiResponse(description="Exact message: Unable to update subscription. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"plan_id": 3, "status": "ACTIVE", "start_date": "2026-01-01T00:00:00", "end_date": "2026-02-01T00:00:00", "plan_price_paid": "1299.00", "payment_status": True, "auto_renew": False}, request_only=True),
        OpenApiExample("Success response", value={"message": "Subscription updated successfully.", "data": {"id": 40, "status": "ACTIVE"}, "dev_message": "Updated fields: status->ACTIVE"}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["patch"], url_path=r"update/(?P<pk>[^/.]+)")
@transaction.atomic
def update_subscription(self, request, pk=None):
    """
    Update ShopSubscription details.

    Allowed fields (request.data):
     - plan_id (int)            -> switch plan (optional)
     - status (str)             -> ACTIVE | EXPIRED | SUSPENDED | CANCELLED (optional)
     - start_date (ISO datetime) -> update start date (optional)
     - end_date (ISO datetime)   -> update end date (optional)
     - plan_price_paid (decimal) -> update paid/total amount (optional)
     - payment_status (bool)     -> mark paid/unpaid (optional)
     - auto_renew (bool)         -> toggle auto-renew (optional)

    Notes:
     - end_date must be >= start_date (validation)
     - uses select_for_update to avoid races
     - returns user-friendly message in "message" and dev details in "dev_message"
    """
    try:
        # validate pk
        try:
            sub_id = int(pk)
        except (TypeError, ValueError):
            return Response({"message": "Invalid subscription id."}, status=status.HTTP_400_BAD_REQUEST)

        # Lock subscription row
        sub_qs = ShopSubscription.objects.select_for_update().select_related("plan", "shop").filter(pk=sub_id)
        subscription = sub_qs.first()
        if not subscription:
            return Response({"message": "Subscription not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        update_fields = []
        dev_changes = []

        # Plan change
        plan_id = data.get("plan_id")
        if plan_id is not None:
            try:
                plan_obj = SubscriptionPlan.objects.get(pk=int(plan_id))
            except (SubscriptionPlan.DoesNotExist, ValueError):
                return Response({"message": "Invalid plan_id provided."}, status=status.HTTP_400_BAD_REQUEST)
            if subscription.plan_id != plan_obj.id:
                subscription.plan = plan_obj
                update_fields.append("plan")
                dev_changes.append(f"plan_id->{plan_obj.id}")

        # Status update
        if "status" in data:
            status_value = (data.get("status") or "").strip().upper()
            valid_statuses = [choice[0] for choice in ShopSubscription.STATUS_CHOICES]
            if status_value not in valid_statuses:
                return Response({"message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}."}, status=status.HTTP_400_BAD_REQUEST)
            if subscription.status != status_value:
                subscription.status = status_value
                update_fields.append("status")
                dev_changes.append(f"status->{status_value}")

        # Parse dates
        start_date_str = data.get("start_date")
        end_date_str = data.get("end_date")
        parsed_start = None
        parsed_end = None

        if start_date_str:
            parsed_start = parse_datetime(start_date_str)
            if not parsed_start:
                return Response({"message": "Invalid start_date format. Use ISO format (YYYY-MM-DDTHH:MM:SS)."}, status=status.HTTP_400_BAD_REQUEST)

        if end_date_str:
            parsed_end = parse_datetime(end_date_str)
            if not parsed_end:
                return Response({"message": "Invalid end_date format. Use ISO format (YYYY-MM-DDTHH:MM:SS)."}, status=status.HTTP_400_BAD_REQUEST)

        def _ensure_aware(dt):
            if dt is None:
                return None
            try:
                if is_naive(dt):
                    return make_aware(dt, get_default_timezone())
            except Exception:
                return dt
            return dt

        parsed_start = _ensure_aware(parsed_start)
        parsed_end = _ensure_aware(parsed_end)

        # If plan changed and end_date not provided, optionally adjust end_date based on plan.duration_days
        if plan_id and not parsed_end:
            base_start = parsed_start or subscription.start_date
            if base_start and subscription.plan and subscription.plan.duration_days:
                parsed_end = base_start + timedelta(days=subscription.plan.duration_days)

        # Validate date ordering
        effective_start = _ensure_aware(parsed_start or subscription.start_date)
        effective_end = _ensure_aware(parsed_end or subscription.end_date)
        if effective_start and effective_end and effective_end < effective_start:
            return Response({"message": "End date must be same or after start date."}, status=status.HTTP_400_BAD_REQUEST)

        if parsed_start:
            subscription.start_date = parsed_start
            update_fields.append("start_date")
            dev_changes.append(f"start_date->{parsed_start.isoformat()}")

        if parsed_end:
            subscription.end_date = parsed_end
            update_fields.append("end_date")
            dev_changes.append(f"end_date->{parsed_end.isoformat()}")

        # plan_price_paid -> total_amount
        if "plan_price_paid" in data:
            total_amount_raw = data.get("plan_price_paid")
            try:
                total_amount = Decimal(total_amount_raw)
                if total_amount < 0:
                    raise ValueError()
            except Exception:
                return Response({"message": "Invalid plan_price_paid."}, status=status.HTTP_400_BAD_REQUEST)
            subscription.total_amount = total_amount
            update_fields.append("total_amount")
            dev_changes.append(f"total_amount->{total_amount}")

        # payment_status
        if "payment_status" in data:
            ps = data.get("payment_status")
            new_ps = True if str(ps).lower() in ("1", "true", "yes") else False
            if subscription.payment_status != new_ps:
                subscription.payment_status = new_ps
                update_fields.append("payment_status")
                dev_changes.append(f"payment_status->{new_ps}")

        # auto_renew
        if "auto_renew" in data:
            ar = data.get("auto_renew")
            new_ar = True if str(ar).lower() in ("1", "true", "yes") else False
            if subscription.auto_renew != new_ar:
                subscription.auto_renew = new_ar
                update_fields.append("auto_renew")
                dev_changes.append(f"auto_renew->{new_ar}")

        if not update_fields:
            return Response({"message": "No updatable fields provided."}, status=status.HTTP_200_OK)

        # Persist changes atomically
        subscription.save(update_fields=update_fields)

        # Refresh from db for response
        subscription.refresh_from_db()

        return Response(
            {
                "message": "Subscription updated successfully.",
                "data": ShopSubscriptionSerializer(subscription).data,
                "dev_message": f"Updated fields: {', '.join(dev_changes)}"
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        logger.exception(f"Failed updating ShopSubscription {pk}: {e}")
        return Response(
            {"message": "Unable to update subscription. Please try again later.", "dev_message": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
