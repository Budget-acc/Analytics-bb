from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q, Sum
from services.models import ProductTransferHistory
from shop.models import Shop
from django.utils.timezone import now
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Get transfer stats",
    description="Returns transfer aggregates for all shops or a specific shop.",
    parameters=[
        OpenApiParameter(name="shop_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional shop id filter."),
    ],
    responses={
        200: OpenApiResponse(description="Transfer statistics payload."),
        400: OpenApiResponse(description="Exact messages: Invalid shop specified. / Transfer stats error"),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 3}, request_only=True),
        OpenApiExample("Success response", value={"total_transfers": 10, "this_month_transfers": 4, "outgoing_transfers": 6, "incoming_transfers": 4, "total_quantity_transferred": 120}, response_only=True, status_codes=["200"]),
        OpenApiExample("Invalid shop response", value={"error": "Invalid shop specified."}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["get"], url_path="transfer-stats")
def transfer_stats(self, request):
    try:
        shop_id = request.query_params.get('shop_id')

        if shop_id:
            if not Shop.objects.filter(id=shop_id, is_active=True, deleted=None).exists():
                return Response({"error": "Invalid shop specified."}, status=status.HTTP_400_BAD_REQUEST)

            base_filter = Q(source_shop_id=shop_id) | Q(destination_shop_id=shop_id)
            outgoing_filter = Q(source_shop_id=shop_id)
            incoming_filter = Q(destination_shop_id=shop_id)
        else:
            base_filter = Q()
            outgoing_filter = Q()
            incoming_filter = Q()

        today = now().date()
        this_month = today.replace(day=1)

        stats = {
            "total_transfers": ProductTransferHistory.objects.filter(base_filter).count(),
            "this_month_transfers": ProductTransferHistory.objects.filter(
                base_filter, transfer_date__date__gte=this_month
            ).count(),
            "outgoing_transfers": ProductTransferHistory.objects.filter(outgoing_filter).count(),
            "incoming_transfers": ProductTransferHistory.objects.filter(incoming_filter).count(),
            "total_quantity_transferred": ProductTransferHistory.objects.filter(
                base_filter
            ).aggregate(total=Sum('quantity_transferred'))['total'] or 0
        }

        return Response(stats)

    except Exception as e:
        logger.error(f"Transfer stats error: {str(e)}", exc_info=True)
        return Response(
            {"error": "Transfer stats error"},
            status=status.HTTP_400_BAD_REQUEST
        )
