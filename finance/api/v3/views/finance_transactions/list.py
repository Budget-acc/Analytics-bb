import logging

from django.db.models import CharField, Sum, Value
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.exceptions import ValidationError

from ERP.utils.custom_pagination import StandardPageNumberPagination
from finance.models import Investment, Withdrawal

from .helpers import apply_common_filters, serialize_investment, serialize_withdrawal

logger = logging.getLogger("api_requests")

# Per-type registry driving the combined feed. Adding a future transaction
# type (e.g. "manual_income") only requires one more entry here - no other
# changes to list() below. Every registered model must share the field names
# apply_common_filters() relies on (shop, deleted, transaction_date,
# created_at, account, payment_method, amount, notes, reference_number).
TRANSACTION_TYPE_REGISTRY = {
    "investment": {
        "model": Investment,
        "serializer": serialize_investment,
    },
    "withdrawal": {
        "model": Withdrawal,
        "serializer": serialize_withdrawal,
    },
}


@extend_schema(
    tags=["Finance Management V3"],
    summary="List combined Investment and Withdrawal transactions",
    description=(
        "Returns a single paginated, chronologically-sorted feed combining Investment and "
        "Withdrawal records, along with a summary of total investment, total withdrawal, and "
        "net capital across all records matching the applied filters (not just the current page). "
        "Each row carries a `type` field ('investment' or 'withdrawal') so the frontend can route "
        "to the correct detail/edit screen."
    ),
    parameters=[
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Page number (default: 1)."),
        OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Number of results per page."),
        OpenApiParameter(name="ordering", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="'transaction_date' (ascending) or '-transaction_date' (descending, default)."),
        OpenApiParameter(
            name="transaction_type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description="Comma-separated list of transaction types, e.g. 'investment,withdrawal'. Use 'all' or omit for every type. Currently supported: investment, withdrawal.",
        ),
        OpenApiParameter(name="account_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Filter by account ID."),
        OpenApiParameter(name="payment_method", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="'cash', 'upi', or 'bank_transfer'."),
        OpenApiParameter(name="from_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter from this date (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY."),
        OpenApiParameter(name="to_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter up to this date (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY."),
        OpenApiParameter(name="start_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter transaction_date from this date onwards (inclusive). Format: YYYY-MM-DD or DD-MM-YYYY."),
        OpenApiParameter(name="end_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filter transaction_date up to and including this date. Format: YYYY-MM-DD or DD-MM-YYYY."),
        OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Searches notes, account name, reference number, and (if numeric) transaction ID."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated combined list with a summary object."),
        400: OpenApiResponse(description="Invalid filter parameters (e.g. bad date format or an unrecognized transaction_type value)."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching finance transactions."),
    },
)
def list(self, request):
    try:
        transaction_type_param = request.query_params.get('transaction_type')
        if not transaction_type_param or transaction_type_param.strip().lower() == 'all':
            requested_types = [t for t in TRANSACTION_TYPE_REGISTRY]
        else:
            requested_types = [t.strip().lower() for t in transaction_type_param.split(',') if t.strip()]
            if not requested_types:
                return self.validation_error_response(
                    errors={'transaction_type': ["Must contain at least one valid transaction type."]},
                    message="Please select a valid transaction type.",
                )
            invalid_types = [t for t in requested_types if t not in TRANSACTION_TYPE_REGISTRY]
            if invalid_types:
                valid_list = ", ".join(sorted(TRANSACTION_TYPE_REGISTRY) + ['all'])
                return self.validation_error_response(
                    errors={'transaction_type': [f"Invalid transaction type(s): {', '.join(invalid_types)}. Must be one of: {valid_list}."]},
                    message="Please select a valid transaction type.",
                )

        ordering = request.query_params.get('ordering')
        date_order = 'transaction_date' if ordering == 'transaction_date' else '-transaction_date'
        tie_break = 'created_at' if date_order == 'transaction_date' else '-created_at'

        value_parts = []
        active_querysets = {}

        for type_name, config in TRANSACTION_TYPE_REGISTRY.items():
            if type_name not in requested_types:
                continue
            model = config["model"]
            qs = model.objects.filter(shop_id=self.shop_id, deleted__isnull=True)
            qs = apply_common_filters(qs, request)
            active_querysets[type_name] = qs
            value_parts.append(
                qs.annotate(type=Value(type_name, output_field=CharField()))
                .values('id', 'type', 'transaction_date', 'created_at')
            )

        combined = value_parts[0]
        for extra in value_parts[1:]:
            combined = combined.union(extra, all=True)
        combined = combined.order_by(date_order, tie_break)

        paginator = StandardPageNumberPagination()
        page_rows = paginator.paginate_queryset(combined, request)

        ids_by_type = {type_name: [] for type_name in active_querysets}
        for row in page_rows:
            ids_by_type[row['type']].append(row['id'])

        objects_by_type = {}
        for type_name, ids in ids_by_type.items():
            if not ids:
                objects_by_type[type_name] = {}
                continue
            model = TRANSACTION_TYPE_REGISTRY[type_name]["model"]
            objects_by_type[type_name] = {
                o.id: o for o in model.objects.filter(id__in=ids).select_related('account', 'created_by')
            }

        results = []
        for row in page_rows:
            obj = objects_by_type[row['type']].get(row['id'])
            if obj:
                results.append(TRANSACTION_TYPE_REGISTRY[row['type']]["serializer"](obj))

        totals_by_type = {
            type_name: (qs.aggregate(total=Sum('amount'))['total'] or 0)
            for type_name, qs in active_querysets.items()
        }
        total_investment = totals_by_type.get('investment', 0)
        total_withdrawal = totals_by_type.get('withdrawal', 0)

        return self.success_response(
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "data": {
                    "summary": {
                        "total_investment": total_investment,
                        "total_withdrawal": total_withdrawal,
                        "net_capital": total_investment - total_withdrawal,
                    },
                    "results": results,
                },
            },
            message="Finance transactions retrieved successfully.",
        )

    except ValidationError as e:
        return self.validation_error_response(
            errors=e.detail,
            message="Invalid filter parameters.",
        )
    except Exception as e:
        logger.error(f"Finance transactions list error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching finance transactions.",
            dev_message=str(e),
        )


__all__ = ["list"]
