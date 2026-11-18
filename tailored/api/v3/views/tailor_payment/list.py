import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from ERP.utils.custom_pagination import StandardPageNumberPagination
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
from tailored.api.v3.serializers.tailor_payment import TailorPaymentReadSerializer
from tailored.models import TailorPayment

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="List tailor payments",
    description=(
        "Returns a paginated list of payments the active shop has made to its tailors.\n\n"
        "Purpose: Lets shop staff review all recorded tailor payouts across every tailor, or "
        "narrow the list down to a single tailor.\n\n"
        "Context: Used on the tailor payments / accounting dashboard to audit outflows to "
        "tailors. Results are scoped to the active shop and ordered by transaction_date_time "
        "descending (most recent payment first).\n\n"
        "Accepted inputs:\n"
        "Query parameters — all optional:\n"
        "- page (integer): page number.\n"
        "- page_size (integer): number of results per page.\n"
        "- tailor_id (integer): restrict results to a single tailor.\n"
        "- category_id (integer): restrict results to a single expense category."
    ),
    parameters=[
        OpenApiParameter(
            name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Selects which page of results to return. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. Default: 1."
            ),
        ),
        OpenApiParameter(
            name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Controls how many payment records are returned per page. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. Default: the project's standard page size."
            ),
        ),
        OpenApiParameter(
            name="tailor_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Filters the payment list down to a single tailor. "
                "Location: query parameter. "
                "Format/Type: integer (Tailor id). "
                "Optional. If omitted, payments for all tailors in the shop are returned."
            ),
        ),
        OpenApiParameter(
            name="category_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Filters the payment list down to a single expense category. "
                "Location: query parameter. "
                "Format/Type: integer (ShopCategory id). "
                "Optional. If omitted, payments across all categories are returned."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a paginated list of tailor payments.\n\n"
                "Response shape:\n"
                "- count (integer): total number of matching records.\n"
                "- next (string | null): URL for the next page.\n"
                "- previous (string | null): URL for the previous page.\n"
                "- data (array): each item contains id, tailor_id, tailor_name, "
                "transaction_date_time, amount, payment_method, account_id, account_name, "
                "category_id, category_name, notes, created_at."
            )
        ),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching tailor payments."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        qs = (
            TailorPayment.objects
            .filter(shop_id=self.shop_id)
            .select_related('tailor', 'account', 'category')
            .order_by('-transaction_date_time')
        )

        tailor_id = request.query_params.get('tailor_id')
        if tailor_id:
            qs = qs.filter(tailor_id=tailor_id)

        category_id = request.query_params.get('category_id')
        if category_id:
            qs = qs.filter(category_id=category_id)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = TailorPaymentReadSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.error(f"TailorPayment list error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching tailor payments.",
            dev_message=str(e),
        )
