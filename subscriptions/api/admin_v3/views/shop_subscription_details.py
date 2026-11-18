from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q, Count, Sum
from subscriptions.models import (
    SubscriptionPlan,
    Feature,
    ShopSubscription,
    ShopFeatureSubscription,
    ShopUserFeature,
    FeatureUsagePolicy,
    ShopFeatureLimit,
    ShopFeatureUsage,
)
from subscriptions.api.admin_v3.serializers import SubscriptionPlanSerializer, FeatureSerializer, ShopSubscriptionSerializer, SubscriptionPlanShopSerializer, ShopUserFeatureSerializer, FeatureUsagePolicySerializer
import json
import logging
from rest_framework.decorators import action
from django.db import IntegrityError, transaction
from subscriptions.api.admin_v3.utils.shop_subscription_service import ShopSubscriptionService
from shop.models import Shop, ShopUser
from django.utils.dateparse import parse_datetime
from django.http import Http404
from decimal import Decimal, InvalidOperation
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from django.db.models import Prefetch
from django.utils.timezone import localtime, timedelta
from django.utils.timezone import make_aware, is_naive, get_default_timezone
from safedelete.models import HARD_DELETE
from subscriptions.models import UserFeature, Feature

logger = logging.getLogger("api_requests")


class ShopSubscriptionDetailsViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Admin-only endpoint to fetch subscription details for a given shop (by shop id in URL).
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Subscriptions Admin Management V3"],
        summary="Retrieve shop subscription details",
        description="Returns shop subscription details including plan and add-on feature usage/limits by shop id.",
        parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop id.")],
        responses={
            200: OpenApiResponse(description="Success response with details or no-subscription message."),
            400: OpenApiResponse(description="Exact messages: Invalid shop id. / shop_id must be a valid integer."),
            404: OpenApiResponse(description="Exact message: Shop not found."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve subscription details. Please try again later."),
        },
        examples=[
            OpenApiExample("Success with subscription", value={"status": "success", "message": "Subscription details retrieved successfully.", "data": {"shop_id": 10, "shop_name": "Alpha Salon", "subscription": {"id": 33, "status": "ACTIVE", "plan": {"id": 2, "name": "Pro"}, "addon_features": [{"id": 7, "code": "ADV_REPORTS", "usage_limit": {"extra_limit": 1000}}]}}}, response_only=True, status_codes=["200"]),
            OpenApiExample("No subscription", value={"status": "success", "message": "No subscription found for this shop.", "data": None}, response_only=True, status_codes=["200"]),
        ],
    )
    def retrieve(self, request, pk=None):
        try:
            # validate shop id
            try:
                shop_id = int(pk)
            except (TypeError, ValueError):
                return self.validation_error_response(
                    errors={"shop_id": ["shop_id must be a valid integer."]},
                    message="Invalid shop id."
                )

            # ensure shop exists
            try:
                shop = Shop.objects.get(id=shop_id)
            except Shop.DoesNotExist:
                return self.not_found_response(resource="Shop", message="Shop not found.")

            # fetch subscription (if any) with plan, prefetch feature & bundle through objects
            sub = (
                ShopSubscription.objects
                .filter(shop_id=shop_id)
                .select_related("plan", "shop")
                .prefetch_related(
                    Prefetch(
                        "shopfeaturesubscription_set",
                        queryset=ShopFeatureSubscription.objects.select_related("feature").order_by("created_at")
                    ),
                    # Prefetch(
                    #     "shopbundlesubscription_set",
                    #     queryset=ShopBundleSubscription.objects.select_related("bundle").prefetch_related(
                    #         Prefetch("selected_features", queryset=Feature.objects.only("id", "name", "code"))
                    #     ).order_by("created_at")
                    # )
                )
                .annotate(included_features_count=Count("plan__included_features", distinct=True))
                .first()
            )

            if not sub:
                return self.success_response(
                    data=None,
                    message="No subscription found for this shop.",
                    dev_message=f"Shop {shop_id} has no ShopSubscription record."
                )

            # Build addon features list from through records (gives price_paid, dates, limits, usage)
            addons = []
            addon_qs = list(sub.shopfeaturesubscription_set.select_related('feature').all())

            # Prefetch limits and usage for these features to avoid N+1
            feature_ids = [sfs.feature_id for sfs in addon_qs]

            limits_map = {
                fl.feature_id: fl
                for fl in ShopFeatureLimit.objects.filter(
                    shop_id=shop.id,
                    feature_id__in=feature_ids,
                    is_active=True
                )
            }

            # Latest usage per feature (ordered by period_end desc)
            usage_map = {}
            for fu in (
                ShopFeatureUsage.objects
                .filter(shop_id=shop.id, feature_id__in=feature_ids)
                .order_by("feature_id", "-period_end")
            ):
                if fu.feature_id not in usage_map:
                    usage_map[fu.feature_id] = fu

            for sfs in addon_qs:
                feat = sfs.feature
                limit_obj = limits_map.get(sfs.feature_id)
                usage_obj = usage_map.get(sfs.feature_id)

                usage_limit = None
                if limit_obj:
                    usage_limit = {
                        "extra_limit": limit_obj.extra_limit,
                        "valid_until": limit_obj.valid_until.isoformat() if limit_obj.valid_until else None,
                        "is_active": bool(limit_obj.is_active),
                        "reason": limit_obj.reason or ""
                    }

                addons.append({
                    "id": feat.id,
                    "code": feat.code,
                    "name": feat.name,
                    "price_paid": str(sfs.price_paid),
                    "discount_amount": str(sfs.discount_amount or 0),
                    "start_date": localtime(sfs.start_date).strftime("%d-%m-%Y %H:%M:%S") if sfs.start_date else None,
                    "end_date": localtime(sfs.end_date).strftime("%d-%m-%Y %H:%M:%S") if sfs.end_date else None,
                    "is_active": bool(sfs.is_active),
                    "used": str(usage_obj.used) if usage_obj else "",
                    "usage_id": usage_obj.id if usage_obj else None,
                    "usage_limit": usage_limit
                })

            # Build bundles list including selected features
            # bundles = []
            # for sbs in sub.shopbundlesubscription_set.all():
            #     b = sbs.bundle
            #     selected = [{"id": f.id, "name": f.name, "code": f.code} for f in sbs.selected_features.all()]
            #     bundles.append({
            #         "id": b.id,
            #         "name": b.name,
            #         "price_paid": str(sbs.price_paid),
            #         "discount_amount": str(sbs.discount_amount or 0),
            #         "start_date": localtime(sbs.start_date).strftime("%d-%m-%Y %H:%M:%S") if sbs.start_date else None,
            #         "end_date": localtime(sbs.end_date).strftime("%d-%m-%Y %H:%M:%S") if sbs.end_date else None,
            #         "selected_features": selected,
            #         "is_active": bool(sbs.is_active)
            #     })

            plan = sub.plan
            plan_data = {
                "id": plan.id,
                "name": plan.name,
                "plan_type": plan.plan_type,
                "base_price": str(plan.base_price),
                "duration_days": plan.duration_days,
                "included_features_count": sub.included_features_count,
            }

            payload = {
                "shop_id": shop.id,
                "shop_name": shop.name,
                "subscription": {
                    "id": sub.id,
                    "status": sub.status,
                    "start_date": localtime(sub.start_date).strftime("%d-%m-%Y %H:%M:%S") if sub.start_date else None,
                    "end_date": localtime(sub.end_date).strftime("%d-%m-%Y %H:%M:%S") if sub.end_date else None,
                    "paid_amount": str(sub.total_amount),
                    "payment_status": bool(sub.payment_status),
                    "auto_renew": bool(sub.auto_renew),
                    "plan": plan_data,
                    "addon_features": addons,
                    # "bundles": bundles
                }
            }

            return self.success_response(
                data=payload,
                message="Subscription details retrieved successfully.",
                dev_message=f"Fetched ShopSubscription {sub.id} for shop {shop_id}."
            )

        except Exception as e:
            logger.error(f"Failed fetching subscription for shop {pk}: {e}")
            return self.error_response(
                message="Unable to retrieve subscription details. Please try again later.",
            )
