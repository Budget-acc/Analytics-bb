import logging
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import ShopFeatureSubscription, Feature, ShopFeatureLimit, ShopFeatureUsage
from django.db.models import F, OuterRef, Subquery
from django.utils.timezone import localtime
from django.utils import timezone

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="List shops assigned to a feature",
    description=(
        "Returns all shops that have the given feature assigned as an add-on subscription "
        "via ShopFeatureSubscription. Includes per-shop feature assignment details "
        "(start/end date, status) and a summary with total/active/inactive counts. "
        "Paginated.\n\n"
        "**Sorting**\n\n"
        "By default, results are ordered by assignment date (newest first). "
        "To sort by shop feature usage, pass `sort_by=shop_feature_usage` along with an optional "
        "`sort_order` parameter (`asc` or `desc`, default `desc`).\n\n"
        "- `sort_by=shop_feature_usage&sort_order=asc` — shops with the lowest usage first.\n"
        "- `sort_by=shop_feature_usage&sort_order=desc` — shops with the highest usage first.\n\n"
        "Shops that have no usage records are always placed at the end of the list regardless of sort direction. "
        "If `sort_by` is omitted or set to any unsupported value, the default date ordering is used."
    ),
    parameters=[
        OpenApiParameter(
            "feature_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True,
            description="Feature ID to filter by.",
        ),
        OpenApiParameter(
            "page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
            description="Pagination page.",
        ),
        OpenApiParameter(
            "sort_by", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description="Sort field. Supported value: `shop_feature_usage`. Omit to keep default ordering by assignment date.",
        ),
        OpenApiParameter(
            "sort_order", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description="Sort direction when sort_by is set. `asc` or `desc` (default: `desc`). Shops with no usage records always appear last.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Shops fetched for feature."),
        400: OpenApiResponse(
            description=(
                "Exact messages:\n"
                "- feature_id is required.\n"
                "- Feature not found or inactive."
            )
        ),
        500: OpenApiResponse(description="Exact message: Unable to fetch shops for feature."),
    },
)
@action(detail=False, methods=["get"], url_path="shops-by-feature")
def list_shops_by_feature(self, request):
    try:
        feature_id = request.query_params.get("feature_id")
        if not feature_id:
            return self.validation_error_response(
                message="feature_id is required.",
                errors={"feature_id": ["This field is required."]}
            )
        if not str(feature_id).isdigit():
            return self.validation_error_response(
                message="feature_id must be a valid integer.",
                errors={"feature_id": ["Must be a valid integer."]}
            )

        try:
            feature = Feature.objects.get(id=feature_id, is_active=True)
        except Feature.DoesNotExist:
            return self.validation_error_response(
                message="Feature not found or inactive.",
                errors={"feature_id": ["Feature not found or inactive."]}
            )

        # Read optional sorting params
        sort_by = request.query_params.get("sort_by", "").strip().lower()
        sort_order = request.query_params.get("sort_order", "desc").strip().lower()
        if sort_order not in ("asc", "desc"):
            sort_order = "desc"

        qs = (
            ShopFeatureSubscription.objects
            .filter(feature_id=feature.id)
            .select_related(
                "shop_subscription",
                "shop_subscription__shop",
            )
            .order_by("-created_at")
        )

        # Apply usage-based ordering when requested.
        # A Subquery annotation pulls the most-recent ShopFeatureUsage.used value
        # per shop so ordering is done at DB level — required for correct pagination.
        if sort_by == "shop_feature_usage":
            usage_subquery = Subquery(
                ShopFeatureUsage.objects
                .filter(
                    shop_id=OuterRef("shop_subscription__shop_id"),
                    feature_id=feature.id,
                )
                .order_by("-period_end")
                .values("used")[:1]
            )
            qs = qs.annotate(latest_usage=usage_subquery)
            # Shops with no usage record (NULL) always sort to the end
            qs = qs.order_by(
                F("latest_usage").asc(nulls_last=True)
                if sort_order == "asc"
                else F("latest_usage").desc(nulls_last=True)
            )

        now = timezone.now()

        def _is_feature_active(item):
            """
            Feature is active only when:
              - DB is_active flag is True, AND
              - end_date has not yet passed
            If is_active is False → False (regardless of end_date).
            If end_date is expired → False (regardless of is_active flag).
            """
            if not item.is_active:
                return False
            if item.end_date and item.end_date <= now:
                return False
            return True

        # Summary counts via DB — avoids loading all rows into memory
        # active_count mirrors _is_feature_active: is_active=True AND end_date in future
        # end_date is non-nullable on the model so end_date__gt=now is always safe
        total_assignments = qs.count()
        active_count = qs.filter(is_active=True, end_date__gt=now).count()

        summary = {
            "feature_id": feature.id,
            "feature_code": feature.code,
            "feature_name": feature.name,
            "feature_type": feature.feature_type,
            "total_assignments": total_assignments,
            "active_assignments": active_count,
            "inactive_assignments": total_assignments - active_count,
        }

        # Determine if this feature tracks usage (COUNT / STORAGE / TIME)
        # NONE = boolean/flat feature → no limits or usage records apply
        feature_is_metered = feature.usage_type != "NONE"

        # Paginate the queryset directly — no full-table load
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        items_to_render = page if page is not None else list(qs)

        # Build shop-level lookup maps for the current page only (2 flat queries, no N+1)
        limits_map = {}  # shop_id → ShopFeatureLimit
        usage_map = {}   # shop_id → most recent ShopFeatureUsage

        if feature_is_metered:
            shop_ids = [i.shop_subscription.shop_id for i in items_to_render]

            limits_map = {
                fl.shop_id: fl
                for fl in ShopFeatureLimit.objects.filter(
                    shop_id__in=shop_ids,
                    feature_id=feature.id,
                    is_active=True,
                )
            }

            for fu in (
                ShopFeatureUsage.objects
                .filter(shop_id__in=shop_ids, feature_id=feature.id)
                .order_by("shop_id", "-period_end")
            ):
                if fu.shop_id not in usage_map:
                    usage_map[fu.shop_id] = fu

        results = []
        for item in items_to_render:
            shop = item.shop_subscription.shop

            # Resolve usage limit and usage count per shop
            if feature_is_metered:
                fl = limits_map.get(shop.id)
                fu = usage_map.get(shop.id)
                # No ShopFeatureLimit record → 0 (not null) for metered features
                extra_usage_limit = fl.extra_limit if fl else 0
                shop_feature_usage = fu.used if fu else None
            else:
                # Non-metered (boolean) feature → both fields are null
                extra_usage_limit = None
                shop_feature_usage = None

            results.append({
                "id": item.id,
                "shop_id": shop.id,
                "shop_name": shop.name,
                "shop_phone": str(shop.phone) if shop.phone else None,
                "shop_email": shop.email,
                "shop_place": shop.place,
                "shop_is_active": shop.is_active,
                "feature_assigned_at": localtime(item.created_at).strftime("%Y-%m-%dT%H:%M:%S") if item.created_at else None,
                "feature_start_date": localtime(item.start_date).strftime("%Y-%m-%dT%H:%M:%S") if item.start_date else None,
                "feature_end_date": localtime(item.end_date).strftime("%Y-%m-%dT%H:%M:%S") if item.end_date else None,
                "feature_is_active": _is_feature_active(item),
                "subscription_status": item.shop_subscription.get_runtime_status(),
                "extra_usage_limit": extra_usage_limit,
                "shop_feature_usage": shop_feature_usage,
            })

        return self.success_response(
            message="Shops fetched for feature.",
            data={
                "count": total_assignments,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "summary": summary,
                "results": results,
            }
        )

    except Exception as e:
        logger.error(f"ShopUserFeatureViewSet.list_shops_by_feature:\n{e}", exc_info=True)
        return self.error_response(
            message="Unable to fetch shops for feature.",
            dev_message=str(e)
        )
