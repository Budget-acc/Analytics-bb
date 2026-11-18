import logging

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

from purchase.models import Purchase

logger = logging.getLogger("api_requests")

# Field priority: Purchase Order ID > Vendor Name > Invoice Number.
# Match priority within each field: exact > starts-with > contains.
# Case/When evaluates top-to-bottom and stops at the first match, so this
# list's order directly encodes the full 9-tier ranking.
SEARCH_RANK_CASES = [
    ("purchase_code", "iexact", 1),
    ("purchase_code", "istartswith", 2),
    ("purchase_code", "icontains", 3),
    ("vendor__name", "iexact", 4),
    ("vendor__name", "istartswith", 5),
    ("vendor__name", "icontains", 6),
    ("invoice_number", "iexact", 7),
    ("invoice_number", "istartswith", 8),
    ("invoice_number", "icontains", 9),
]


def _format_datetime(value):
    """Render a datetime as 'YYYY-MM-DD HH:MM:SS' in local time, or None."""
    if not value:
        return None
    return localtime(value).strftime("%Y-%m-%d %H:%M:%S")


def _parse_list_date(value):
    from datetime import datetime
    if not value:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


@extend_schema(
    tags=["Purchase Management V3"],
    summary="List purchases",
    description="Returns a paginated list of purchases for the active shop, newest first.",
    parameters=[
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Page number."),
        OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Page size."),
        OpenApiParameter(name="vendor_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Filter by vendor."),
        OpenApiParameter(name="is_completed", type=OpenApiTypes.BOOL, location=OpenApiParameter.QUERY, required=False, description="Filter by completion status."),
        OpenApiParameter(name="start_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter purchase_date >= this date. Format: DD-MM-YYYY or YYYY-MM-DD."),
        OpenApiParameter(name="end_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter purchase_date <= this date. Format: DD-MM-YYYY or YYYY-MM-DD."),
        OpenApiParameter(
            name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Free-text search, ranked by relevance: for each of purchase_code "
                "(Purchase Order ID), vendor name, and invoice_number (checked in "
                "that field priority order), exact match ranks above starts-with, "
                "which ranks above contains. Results are ordered by this rank "
                "first, then newest first - ranking is stable and consistent "
                "across paginated pages."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Paginated purchase list."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching purchases."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        qs = Purchase.objects.filter(shop_id=self.shop_id, deleted__isnull=True).select_related("vendor")

        vendor_id = request.query_params.get("vendor_id")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)

        is_completed = request.query_params.get("is_completed")
        if is_completed is not None:
            qs = qs.filter(is_completed=is_completed.lower() == "true")

        start_date = _parse_list_date(request.query_params.get("start_date"))
        if start_date:
            qs = qs.filter(purchase_date__date__gte=start_date)
        end_date = _parse_list_date(request.query_params.get("end_date"))
        if end_date:
            qs = qs.filter(purchase_date__date__lte=end_date)

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(vendor__name__icontains=search)
                | Q(purchase_code__icontains=search)
                | Q(invoice_number__icontains=search)
            )
            # Rank by relevance: field priority (purchase_code > vendor
            # name > invoice_number) then match priority (exact > starts-
            # with > contains) - Case/When stops at the first matching
            # When, so SEARCH_RANK_CASES' order directly encodes all 9
            # tiers. -id as the final tiebreak keeps ordering fully
            # deterministic within a rank, which is required for stable
            # pagination (identical purchase_date/created_at rows would
            # otherwise have no guaranteed relative order across pages).
            qs = qs.annotate(
                search_rank=Case(
                    *[
                        When(**{f"{field}__{lookup}": search}, then=Value(rank))
                        for field, lookup, rank in SEARCH_RANK_CASES
                    ],
                    default=Value(99),
                    output_field=IntegerField(),
                )
            ).order_by("search_rank", "-purchase_date", "-created_at", "-id")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)

        results = [
            {
                "id": p.id,
                "purchase_code": p.purchase_code,
                "vendor_id": p.vendor_id,
                "vendor_name": p.vendor.name if p.vendor_id else None,
                "purchase_date": _format_datetime(p.purchase_date),
                "grand_total": p.grand_total,
                "paid_amount": p.paid_amount,
                "outstanding_balance": p.outstanding_balance,
                "is_completed": p.is_completed,
            }
            for p in page
        ]

        return paginator.get_paginated_response(results)

    except Exception as e:
        logger.error(f"Purchase list error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching purchases.",
            dev_message=str(e),
        )


__all__ = ["list"]
