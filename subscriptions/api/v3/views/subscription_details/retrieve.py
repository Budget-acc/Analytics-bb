from django.db.models import Count, OuterRef, Prefetch, Subquery
from django.utils.timezone import localtime
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from subscriptions.models import (
    FeatureUsagePolicy,
    ShopFeatureLimit,
    ShopFeatureSubscription,
    ShopFeatureUsage,
    ShopSubscription,
)
import logging

logger = logging.getLogger("api_requests")

_SUCCESS_EXAMPLE = {
    "status": "success",
    "message": "Subscription details retrieved successfully.",
    "data": {
        "shop_id": 10,
        "shop_name": "Alpha Salon",
        "subscription": {
            "id": 33,
            "status": "ACTIVE",
            "start_date": "01-01-2025 00:00:00",
            "end_date": "31-12-2025 23:59:59",
            "paid_amount": "999.00",
            "payment_status": True,
            "auto_renew": True,
            "plan": {
                "id": 2,
                "name": "Pro",
                "plan_type": "STANDARD",
                "base_price": "999.00",
                "duration_days": 365,
                "included_features_count": 5,
            },
                "addon_features_count": 1,
            "addon_features": [
                {
                    "id": 7,
                    "code": "ADV_REPORTS",
                    "name": "Advanced Reports",
                    "price_paid": "199.00",
                    "discount_amount": "0",
                    "start_date": "01-01-2025 00:00:00",
                    "end_date": "31-12-2025 23:59:59",
                    "is_active": True,
                    "used": "45",
                    "usage_limit": {
                        "extra_limit": 1000,
                        "standard_limit": 5000,
                        # "valid_until": "2025-12-31T23:59:59",
                        "is_active": True,
                        # "reason": "",
                    },
                }
            ],
        },
    },
}

_NO_SUB_EXAMPLE = {
    "status": "success",
    "message": "No subscription found for this shop.",
    "data": None,
}

_PERMISSION_EXAMPLE = {
    "status": "permission_denied",
    "message": "Access to this shop has been deactivated.",
    "data": None,
}

_ERROR_EXAMPLE = {
    "status": "error",
    "message": "Unable to retrieve subscription details. Please try again later.",
    "data": None,
}


@extend_schema(
    tags=["Subscription V3"],
    summary="Retrieve current shop subscription details",
    description=(
        "Returns the active subscription details for the shop identified by the "
        "`X-Active-Shop-ID` request header (falls back to the authenticated user's "
        "first active shop when the header is absent). "
        "Includes plan information and all add-on feature subscriptions with their "
        "current usage and limit data.\n\n"
        "**Headers**\n"
        "- `X-Active-Shop-ID` *(optional integer)* — The shop whose subscription to "
        "retrieve. When omitted the API resolves the user's first active shop.\n\n"
        "**Authentication**\n"
        "JWT bearer token required (`Authorization: Bearer <token>`).\n\n"
        "**Possible error messages**\n"
        "- `Access to this shop has been deactivated.`\n"
        "- `Invalid Shop ID format. Must be an integer.`\n"
        "- `Invalid or deleted Shop ID provided.`\n"
        "- `This shop is inactive.`\n"
        "- `You do not have permission to access this shop.`\n"
        "- `Your access to this shop has been deactivated.`\n"
        "- `User is not associated with any active shop.`\n"
        "- `Associated shop is inactive or deleted.`\n"
        "- `Unable to retrieve subscription details. Please try again later.`"
    ),
    parameters=[
        OpenApiParameter(
            name="X-Active-Shop-ID",
            location=OpenApiParameter.HEADER,
            required=False,
            description=(
                "Integer ID of the shop whose subscription you want to retrieve. "
                "Omit to use the authenticated user's first active shop."
            ),
        )
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Subscription details retrieved, or `data: null` when the shop "
                "has no subscription."
            )
        ),
        401: OpenApiResponse(description="Authentication credentials were not provided or are invalid."),
        403: OpenApiResponse(
            description=(
                "Permission denied. Exact messages:\n"
                "- Access to this shop has been deactivated.\n"
                "- Invalid Shop ID format. Must be an integer.\n"
                "- Invalid or deleted Shop ID provided.\n"
                "- This shop is inactive.\n"
                "- You do not have permission to access this shop.\n"
                "- Your access to this shop has been deactivated.\n"
                "- User is not associated with any active shop.\n"
                "- Associated shop is inactive or deleted."
            )
        ),
        500: OpenApiResponse(
            description="Exact message: Unable to retrieve subscription details. Please try again later."
        ),
    },
    examples=[
        OpenApiExample(
            "Success — subscription found",
            value=_SUCCESS_EXAMPLE,
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Success — no subscription",
            value=_NO_SUB_EXAMPLE,
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Permission denied",
            value=_PERMISSION_EXAMPLE,
            response_only=True,
            status_codes=["403"],
        ),
        OpenApiExample(
            "Server error",
            value=_ERROR_EXAMPLE,
            response_only=True,
            status_codes=["500"],
        ),
    ],
)
def retrieve(self, request):
    try:
        shop_id = self.shop_id

        sub = (
            ShopSubscription.objects
            .filter(shop_id=shop_id)
            .select_related("plan", "shop")
            .prefetch_related(
                Prefetch(
                    "shopfeaturesubscription_set",
                    queryset=ShopFeatureSubscription.objects.select_related("feature").order_by("created_at"),
                )
            )
            .annotate(included_features_count=Count("plan__included_features", distinct=True))
            .first()
        )

        if not sub:
            return self.success_response(
                data=None,
                message="No subscription found for this shop.",
                dev_message=f"Shop {shop_id} has no ShopSubscription record.",
            )

        addon_qs = list(sub.shopfeaturesubscription_set.all())
        now = timezone.localtime(timezone.now())
        feature_ids = [sfs.feature_id for sfs in addon_qs]

        limits_map = {
            fl.feature_id: fl
            for fl in ShopFeatureLimit.objects.filter(
                shop_id=shop_id,
                feature_id__in=feature_ids,
                is_active=True,
            )
        } if feature_ids else {}

        standard_limit_map = {
            policy.feature_id: policy.limit
            for policy in FeatureUsagePolicy.objects.filter(
                plan_id=sub.plan_id,
                feature_id__in=feature_ids,
            )
        } if feature_ids else {}

        usage_map = {}
        if feature_ids:
            latest_usage_id = (
                ShopFeatureUsage.objects
                .filter(
                    shop_id=shop_id,
                    feature_id=OuterRef("feature_id"),
                )
                .order_by("-period_end", "-id")
                .values("id")[:1]
            )

            usage_qs = (
                ShopFeatureUsage.objects
                .filter(shop_id=shop_id, feature_id__in=feature_ids)
                .filter(id=Subquery(latest_usage_id))
            )
            usage_map = {fu.feature_id: fu for fu in usage_qs}

        addons = []
        for sfs in addon_qs:
            feat = sfs.feature
            limit_obj = limits_map.get(sfs.feature_id)
            usage_obj = usage_map.get(sfs.feature_id)

            usage_limit = None
            if limit_obj:
                usage_limit = {
                    "extra_limit": limit_obj.extra_limit,
                    "standard_limit": standard_limit_map.get(sfs.feature_id),
                    "valid_until": limit_obj.valid_until.isoformat() if limit_obj.valid_until else None,
                    "is_active": bool(limit_obj.is_active),
                }

            addons.append({
                "id": feat.id,
                "code": feat.code,
                "name": feat.name,
                # "price_paid": str(sfs.price_paid),
                # "discount_amount": str(sfs.discount_amount or 0),
                "start_date": localtime(sfs.start_date).strftime("%d-%m-%Y %H:%M:%S") if sfs.start_date else None,
                "end_date": localtime(sfs.end_date).strftime("%d-%m-%Y %H:%M:%S") if sfs.end_date else None,
                "is_active": bool(sfs.is_active),
                "status": "expired" if (sfs.end_date and timezone.localtime(sfs.end_date) <= now) else "active",
                "used": str(usage_obj.used) if usage_obj else "",
                "usage_limit": usage_limit,
            })

        plan = sub.plan
        plan_data = {
            "id": plan.id,
            "name": plan.name,
            "plan_type": plan.plan_type,
            # "base_price": str(plan.base_price),
            # "duration_days": plan.duration_days,
            # "included_features_count": sub.included_features_count,
        }

        payload = {
            "shop_id": sub.shop.id,
            "shop_name": sub.shop.name,
            "subscription": {
                "id": sub.id,
                "status": sub.get_runtime_status(),
                "start_date": localtime(sub.start_date).strftime("%d-%m-%Y %H:%M:%S") if sub.start_date else None,
                "end_date": localtime(sub.end_date).strftime("%d-%m-%Y %H:%M:%S") if sub.end_date else None,
                # "paid_amount": str(sub.total_amount),
                # "payment_status": bool(sub.payment_status),
                # "auto_renew": bool(sub.auto_renew),
                "plan": plan_data,
                "addon_features_count": len(addons),
                "addon_features": addons,
            },
        }

        return self.success_response(
            data=payload,
            message="Subscription details retrieved successfully.",
            dev_message=f"Fetched ShopSubscription {sub.id} for shop {shop_id}.",
        )

    except Exception as e:
        logger.error(f"Failed fetching subscription for shop {self.shop_id}: {e}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve subscription details. Please try again later.",
        )
