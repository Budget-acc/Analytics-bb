import logging
from datetime import datetime

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.exceptions import ValidationError

from ERP.utils.custom_pagination import StandardPageNumberPagination
from finance.api.v3.serializers.withdrawal_serializer import WithdrawalReadSerializer
from finance.models import Withdrawal

logger = logging.getLogger("api_requests")

_DATE_FORMATS = ['%Y-%m-%d', '%d-%m-%Y']


def _parse_date(value, param_name):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValidationError({param_name: ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]})


@extend_schema(
    tags=["Finance Management V3"],
    summary="List withdrawal entries",
    description=(
        "Returns paginated withdrawal entries for the active shop, ordered by transaction_date "
        "descending. Optional filters: account_id, category_id, withdrawal_reason, status, "
        "from_date, to_date."
    ),
    parameters=[
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Page number (default: 1)."),
        OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Number of results per page."),
        OpenApiParameter(name="account_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Filter by account ID."),
        OpenApiParameter(name="category_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Filter by category ID."),
        OpenApiParameter(name="withdrawal_reason", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter by withdrawal reason."),
        OpenApiParameter(name="status", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter by status."),
        OpenApiParameter(name="from_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter entries from this date (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY."),
        OpenApiParameter(name="to_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter entries up to this date (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated list of withdrawal entries."),
        400: OpenApiResponse(description="Invalid date format for from_date or to_date."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching withdrawals."),
    },
)
def list(self, request):
    try:
        qs = (
            Withdrawal.objects
            .filter(shop_id=self.shop_id)
            .select_related('account', 'category')
            .order_by('-transaction_date')
        )

        account_id = request.query_params.get('account_id')
        if account_id:
            qs = qs.filter(account_id=account_id)

        category_id = request.query_params.get('category_id')
        if category_id:
            qs = qs.filter(category_id=category_id)

        withdrawal_reason = request.query_params.get('withdrawal_reason')
        if withdrawal_reason:
            qs = qs.filter(withdrawal_reason=withdrawal_reason)

        status_param = request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        from_date = request.query_params.get('from_date')
        if from_date:
            parsed = _parse_date(from_date, 'from_date')
            qs = qs.filter(transaction_date__date__gte=parsed)

        to_date = request.query_params.get('to_date')
        if to_date:
            parsed = _parse_date(to_date, 'to_date')
            qs = qs.filter(transaction_date__date__lte=parsed)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = WithdrawalReadSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    except ValidationError as e:
        return self.validation_error_response(
            errors=e.detail,
            message="Invalid filter parameters.",
        )
    except Exception as e:
        logger.error(f"Withdrawal list error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching withdrawals.",
            dev_message=str(e),
        )


__all__ = ["list"]
