import logging
from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.exceptions import ValidationError

from ERP.utils.custom_pagination import StandardPageNumberPagination
from finance.api.v3.serializers.account_transfer_serializer import AccountTransferReadSerializer
from finance.models import AccountTransfer
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

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
    summary="List account transfers",
    description=(
        "Returns paginated account transfers for the active shop, ordered by "
        "transfer_date_time descending. Optional filters: account_id (source OR destination), "
        "from_account_id, to_account_id, from_date, to_date."
    ),
    parameters=[
        OpenApiParameter(
            name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY,
            required=False, description="Page number (default: 1).",
        ),
        OpenApiParameter(
            name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY,
            required=False, description="Number of results per page.",
        ),
        OpenApiParameter(
            name="account_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY,
            required=False,
            description="Filter by account ID. Returns all transfers where this account is the source or destination.",
        ),
        OpenApiParameter(
            name="from_account_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY,
            required=False, description="Filter by source account ID only.",
        ),
        OpenApiParameter(
            name="to_account_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY,
            required=False, description="Filter by destination account ID.",
        ),
        OpenApiParameter(
            name="from_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY,
            required=False, description="Filter transfers from this date (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY.",
        ),
        OpenApiParameter(
            name="to_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY,
            required=False, description="Filter transfers up to this date (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Paginated list of account transfers."),
        400: OpenApiResponse(description="Invalid date format for from_date or to_date."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching transfers."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        qs = (
            AccountTransfer.objects
            .filter(shop_id=self.shop_id)
            .select_related('from_account', 'to_account', 'created_by')
            .order_by('-transfer_date_time')
        )

        account_id = request.query_params.get('account_id')
        if account_id:
            from finance.models import Account
            if not Account.objects.filter(
                id=account_id, shop_id=self.shop_id, deleted__isnull=True
            ).exists():
                raise ValidationError({"account_id": ["Account not found."]})
            qs = qs.filter(Q(from_account_id=account_id) | Q(to_account_id=account_id))

        from_account_id = request.query_params.get('from_account_id')
        if from_account_id:
            qs = qs.filter(from_account_id=from_account_id)

        to_account_id = request.query_params.get('to_account_id')
        if to_account_id:
            qs = qs.filter(to_account_id=to_account_id)

        from_date = request.query_params.get('from_date')
        if from_date:
            parsed = _parse_date(from_date, 'from_date')
            qs = qs.filter(transfer_date_time__date__gte=parsed)

        to_date = request.query_params.get('to_date')
        if to_date:
            parsed = _parse_date(to_date, 'to_date')
            end_of_day = timezone.make_aware(datetime.combine(parsed, datetime.max.time()))
            qs = qs.filter(transfer_date_time__lte=end_of_day)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = AccountTransferReadSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    except ValidationError as e:
        detail = e.detail
        try:
            if isinstance(detail, dict):
                first_val = next(iter(detail.values()))
                first_msg = str(first_val[0])
            else:
                first_msg = str(detail[0])
        except (StopIteration, IndexError, TypeError):
            first_msg = "Invalid filter parameters."
        return self.validation_error_response(
            errors=detail,
            message=first_msg,
        )
    except Exception as e:
        logger.error(f"AccountTransfer list error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching transfers.",
            dev_message=str(e),
        )
