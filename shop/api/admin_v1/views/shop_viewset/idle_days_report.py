from rest_framework.decorators import action
from rest_framework import status
from django.db import models
from django.db.models import Count, Q, OuterRef, Subquery
from django.db.models.functions import TruncDate
from shop.models import Shop
from booking_v2.models import BookingV2
from sales.models import Sale
from tailored.models import TailorOrder
from subscriptions.models import ShopSubscription
from ERP.utils.custom_pagination import StandardPageNumberPagination
from django.utils import timezone
from datetime import datetime, timedelta
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Generate idle days report",
    description="Returns shops that satisfy idle day threshold within an analyzed period, including activity counters.",
    parameters=[
        OpenApiParameter("period_days", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="1-365. Default 30."),
        OpenApiParameter("threshold", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="0-period_days. Default 15."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated report response or standard success response."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Invalid query parameters\n"
                "- Invalid period range\n"
                "- Threshold out of range\n"
                "- period_days and threshold must be integers\n"
                "- Must be between 1 and 365\n"
                "- Must be between 0 and period_days"
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"period_days": 30, "threshold": 10, "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": {"shops": [{"id": 4, "name": "Prime Tailors", "idle_days": 9}], "filters": {"period_days": 30, "threshold": 10}, "summary": {"total_idle_shops": 1, "returned": 1}}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="idle-days-report")
def idle_days_report(self, request):
    """
    Generate idle days report for shops

    Query Params:
        - period_days: Number of days to analyze (1-365, default: 30)
        - threshold: Maximum idle days threshold (0-period_days, default: 15)

    Example: /idle-days-report/?period_days=30&threshold=10
    Returns: Shops with idle_days <= 10 in the last 30 days
    """
    # ==================== PHASE 1: INPUT VALIDATION ====================
    try:
        period_days = int(request.query_params.get("period_days", 30))
        threshold = int(request.query_params.get("threshold", 15))
    except (TypeError, ValueError):
        logger.warning(
            f"[Idle Days Report] Invalid numeric parameters",
            extra={"period_days": request.query_params.get("period_days"),
                   "threshold": request.query_params.get("threshold")}
        )
        return self.validation_error_response(
            errors={"params": ["period_days and threshold must be integers"]},
            message="Invalid query parameters"
        )

    # Validate period_days range
    if not (1 <= period_days <= 365):
        logger.warning(
            f"[Idle Days Report] period_days out of range: {period_days}"
        )
        return self.validation_error_response(
            errors={"period_days": ["Must be between 1 and 365"]},
            message="Invalid period range"
        )

    # Validate threshold range
    if not (0 <= threshold <= period_days):
        logger.warning(
            f"[Idle Days Report] threshold out of range: {threshold}, period_days: {period_days}"
        )
        return self.validation_error_response(
            errors={"threshold": ["Must be between 0 and period_days"]},
            message="Threshold out of range"
        )

    # ==================== PHASE 2: DATE RANGES ====================
    now = timezone.now()
    # Ensure period_days is inclusive of today (e.g., Jan 20 with period_days=30 gives Dec 22 to Jan 20)
    period_start = now.date() - timedelta(days=period_days - 1)
    period_end = now.date()
    activity_window_start = now - timedelta(days=90)  # Only shops active in last 90 days

    # ==================== PHASE 3: COLLECT SHOP IDS WITH ACTIVITY IN LAST 90 DAYS ====================
    booking_shop_ids = set(
        BookingV2.objects.filter(
            created_at__gte=activity_window_start,
            created_at__lte=now
        ).values_list("shop_id", flat=True)
    )

    sale_shop_ids = set(
        Sale.objects.filter(
            created_at__gte=activity_window_start,
            created_at__lte=now
        ).values_list("shop_id", flat=True)
    )

    tailor_shop_ids = set(
        TailorOrder.objects.filter(
            created_at__gte=activity_window_start,
            created_at__lte=now
        ).values_list("shop_id", flat=True)
    )

    # Combine all shop IDs with activity in last 90 days
    active_shop_ids = booking_shop_ids | sale_shop_ids | tailor_shop_ids

    if not active_shop_ids:
        logger.info("[Idle Days Report] No shops with activity found in last 90 days")
        return self.success_response(
            data={
                "shops": [],
                "filters": {
                    "period_days": period_days,
                    "threshold": threshold,
                    "period_start": period_start.date().isoformat(),
                    "period_end": now.date().isoformat()
                },
                "summary": {
                    "total_idle_shops": 0,
                    "returned": 0
                }
            },
            message="Idle days report generated successfully"
        )

    # ==================== PHASE 4: BUILD ACTIVITY DATA FOR PERIOD ====================
    period_activity = {}

    # Bookings
    booking_dates = BookingV2.objects.filter(
        shop_id__in=active_shop_ids,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end
    ).values("shop_id").annotate(
        activity_date=TruncDate("created_at")
    ).values("shop_id", "activity_date")

    for record in booking_dates:
        shop_id = record["shop_id"]
        if shop_id not in period_activity:
            period_activity[shop_id] = set()
        period_activity[shop_id].add(record["activity_date"])

    # Sales
    sale_dates = Sale.objects.filter(
        shop_id__in=active_shop_ids,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end
    ).values("shop_id").annotate(
        activity_date=TruncDate("created_at")
    ).values("shop_id", "activity_date")

    for record in sale_dates:
        shop_id = record["shop_id"]
        if shop_id not in period_activity:
            period_activity[shop_id] = set()
        period_activity[shop_id].add(record["activity_date"])

    # Tailor Orders
    tailor_dates = TailorOrder.objects.filter(
        shop_id__in=active_shop_ids,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end
    ).values("shop_id").annotate(
        activity_date=TruncDate("created_at")
    ).values("shop_id", "activity_date")

    for record in tailor_dates:
        shop_id = record["shop_id"]
        if shop_id not in period_activity:
            period_activity[shop_id] = set()
        period_activity[shop_id].add(record["activity_date"])

    # ==================== PHASE 5: GET ACTIVITY COUNTS FOR PERIOD ====================
    activity_counts = {
        "bookings": {},
        "sales": {},
        "tailor_orders": {}
    }

    # Bookings count by shop
    for item in BookingV2.objects.filter(
        shop_id__in=active_shop_ids,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end
    ).values("shop_id").annotate(count=Count("id")).values("shop_id", "count"):
        activity_counts["bookings"][item["shop_id"]] = item["count"]

    # Sales count by shop
    for item in Sale.objects.filter(
        shop_id__in=active_shop_ids,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end
    ).values("shop_id").annotate(count=Count("id")).values("shop_id", "count"):
        activity_counts["sales"][item["shop_id"]] = item["count"]

    # Tailor orders count by shop
    for item in TailorOrder.objects.filter(
        shop_id__in=active_shop_ids,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end
    ).values("shop_id").annotate(count=Count("id")).values("shop_id", "count"):
        activity_counts["tailor_orders"][item["shop_id"]] = item["count"]

    # ==================== PHASE 6: BUILD RESULTS WITH ORM ANNOTATIONS ====================
    results = []

    # Subquery to get the latest subscription status for each shop
    latest_sub_status = Subquery(
        ShopSubscription.objects.filter(
            shop_id=OuterRef('pk')
        ).order_by('-end_date').values('status')[:1]
    )

    shops = Shop.objects.filter(
        id__in=active_shop_ids,
        is_active=True,
        deleted__isnull=True
    ).annotate(
        subscription_status_db=models.F('subscription_status'),
        latest_subscription_status=latest_sub_status
    ).values("id", "name", "img", "place", "city", "latest_subscription_status", "subscription_status_db", "created_at")

    for shop in shops:
        shop_id = shop["id"]
        active_days = len(period_activity.get(shop_id, set()))
        idle_days = period_days - active_days

        # Get last activity date
        last_activity_date = shop["created_at"]
        if shop_id in period_activity and period_activity[shop_id]:
            last_activity_date = max(period_activity[shop_id])

        # Use latest_subscription_status from ShopSubscription if present, else fallback to Shop.subscription_status
        try:
            sub_status = self.safe_subscription_status(shop.get("latest_subscription_status"), shop.get("subscription_status_db"))
        except ValueError as e:
            logger.error(f"[Idle Days Report] {e} for shop_id={shop_id}")
            continue

        # Filter by threshold
        if idle_days <= threshold:
            results.append({
                "id": shop_id,
                "name": shop["name"],
                "img": shop["img"],
                "place": shop["place"],
                "city": shop["city"],
                "subscription_status": sub_status,
                "created_at": shop["created_at"].strftime("%Y-%m-%dT%H:%M:%S") if shop["created_at"] else None,
                "active_days": active_days,
                "last_activity_date": last_activity_date.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(last_activity_date, datetime) else (last_activity_date.strftime("%Y-%m-%dT00:00:00") if hasattr(last_activity_date, 'strftime') else None),
                "total_bookings": activity_counts["bookings"].get(shop_id, 0),
                "total_sales": activity_counts["sales"].get(shop_id, 0),
                "total_tailor_orders": activity_counts["tailor_orders"].get(shop_id, 0),
                "idle_days": idle_days
            })

    # Sort by idle_days descending
    results.sort(key=lambda x: x["idle_days"], reverse=True)

    # ==================== PHASE 7: PAGINATION ====================
    paginator = StandardPageNumberPagination()
    page = paginator.paginate_queryset(results, request)

    response = {
        "shops": page if page else results,
        "filters": {
            "period_days": period_days,
            "threshold": threshold,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat()
        },
        "summary": {
            "total_idle_shops": len(results),
            "returned": len(page) if page else len(results)
        }
    }

    # ==================== PHASE 8: RESPONSE ====================
    if page is not None:
        return paginator.get_paginated_response(response)

    return self.success_response(
        data=response,
        message="Idle days report generated successfully"
    )
