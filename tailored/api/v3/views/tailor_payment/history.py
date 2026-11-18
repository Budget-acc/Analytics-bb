import logging

from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from ERP.utils.custom_pagination import StandardPageNumberPagination
from tailored.api.v3.serializers.tailor_payment import TailorPaymentReadSerializer
from tailored.api.v3.utils.tailor_payment_summary import (
    get_tailor_total_charged,
    get_tailor_total_paid,
    get_tailor_remaining_payable,
)
from tailored.models import Tailor, TailorPayment

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Tailor payment history",
    description=(
        "Returns the full, paginated payment history for a single tailor, latest payment first.\n\n"
        "Purpose: Lets a shop review every payment ever made to a specific tailor — useful for "
        "weekly/monthly settlement review, disputes, or general bookkeeping for that tailor. "
        "Also returns the tailor's overall payable/paid/remaining totals so staff don't have "
        "to sum the (possibly multi-page) payment list themselves.\n\n"
        "Context: Used from the tailor detail screen when a shop owner or staff opens a "
        "tailor's profile/ledger to see their full payment trail (as opposed to the generic "
        "tailor-payments list, which spans all tailors). The tailor must belong to the active "
        "shop and must not be soft-deleted. The summary totals use the exact same calculation "
        "as the Create/Partial Update payment validation and the tailor monthly summary "
        "endpoint, so all three screens always agree.\n\n"
        "Accepted inputs:\n"
        "Path parameters — Required:\n"
        "- tailor_id (integer): the tailor whose payment history is requested.\n\n"
        "Query parameters — Optional:\n"
        "- page (integer): page number.\n"
        "- page_size (integer): number of results per page."
    ),
    parameters=[
        OpenApiParameter(
            "tailor_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True,
            description=(
                "Purpose: Identifies which tailor's payment history to fetch. "
                "Location: URL path parameter. "
                "Format/Type: integer (Tailor id, must belong to the active shop and not be deleted). "
                "Required."
            ),
        ),
        OpenApiParameter(
            "page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Selects which page of the tailor's payment history to return. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. Default: 1."
            ),
        ),
        OpenApiParameter(
            "page_size", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Controls how many payment records are returned per page. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. Default: the project's standard page size."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a paginated payment history for the tailor.\n\n"
                "Response shape:\n"
                "- count (integer): total number of payments for this tailor.\n"
                "- next (string | null): URL for the next page.\n"
                "- previous (string | null): URL for the previous page.\n"
                "- data (array): each item contains id, tailor_id, tailor_name, "
                "transaction_date_time, amount, payment_method, account_id, account_name, "
                "category_id, category_name, notes, created_at.\n"
                "- payment_summary (object): the tailor's overall totals, unaffected by "
                "pagination - total_payable (lifetime charge total across completed orders), "
                "total_paid (sum of active payments), remaining_payable (total_payable minus "
                "total_paid)."
            )
        ),
        404: OpenApiResponse(description="Exact message: Tailor not found."),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching tailor payment history."),
    },
    examples=[
        OpenApiExample(
            "History success",
            value={"count": 1, "next": None, "previous": None, "data": [
                {"id": 12, "tailor_id": 5, "tailor_name": "Rahim", "transaction_date_time": "2026-05-19T14:30:00+05:30",
                 "amount": "2500.00", "payment_method": "cash", "account_id": 3, "account_name": "Cash in Hand",
                 "category_id": 7, "category_name": "Stitching Labor",
                 "notes": "Weekly settlement", "created_at": "2026-05-19T14:31:02+05:30"}
            ], "payment_summary": {"total_payable": 3100.0, "total_paid": 2500.0, "remaining_payable": 600.0}},
            response_only=True,
        ),
    ],
)
@action(detail=False, methods=['get'], url_path=r'history/(?P<tailor_id>\d+)')
def history(self, request, tailor_id=None):
    try:
        if not Tailor.objects.filter(id=tailor_id, shop_id=self.shop_id, is_deleted=False).exists():
            return self.not_found_response(
                resource="Tailor",
                message="Tailor not found.",
                dev_message=f"Tailor {tailor_id} not found in shop {self.shop_id} or is deleted.",
            )

        payments = (
            TailorPayment.objects
            .filter(tailor_id=tailor_id, shop_id=self.shop_id)
            .select_related('tailor', 'account', 'category')
            .order_by('-transaction_date_time')
        )

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(payments, request)
        serializer = TailorPaymentReadSerializer(page, many=True)
        response = paginator.get_paginated_response(serializer.data)

        # Overall payable/paid/remaining totals, unaffected by pagination - uses
        # the same helpers as Create/Partial Update validation and the tailor
        # monthly summary, so all three stay consistent.
        response.data['data']['payment_summary'] = {
            'total_payable': float(get_tailor_total_charged(tailor_id, self.shop_id)),
            'total_paid': float(get_tailor_total_paid(tailor_id, self.shop_id)),
            'remaining_payable': float(get_tailor_remaining_payable(tailor_id, self.shop_id)),
        }
        return response

    except Exception as e:
        logger.error(f"TailorPayment history error for tailor {tailor_id}: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching tailor payment history.",
            dev_message=str(e),
        )
