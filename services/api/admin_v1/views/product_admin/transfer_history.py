from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q
from services.models import ProductTransferHistory
from services.api.admin_v1.serializers import ProductTransferHistorySerializer
from shop.models import Shop
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Get transfer history",
    description="Returns paginated product transfer history with optional shop and transfer type filters.",
    parameters=[
        OpenApiParameter(name="shop_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Shop id filter."),
        OpenApiParameter(name="type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Transfer type filter: all, outgoing, incoming."),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated transfer history response."),
        400: OpenApiResponse(description="Exact messages: Invalid shop specified. / Transfer history error"),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 3, "type": "outgoing", "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 8, "quantity_transferred": 5, "transfer_type": "outgoing"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Invalid shop response", value={"error": "Invalid shop specified."}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["get"], url_path="transfer-history")
def transfer_history(self, request):
    try:
        shop_id = request.query_params.get('shop_id')
        transfer_type = request.query_params.get('type', 'all')

        queryset = ProductTransferHistory.objects.select_related(
            'source_shop', 'destination_shop', 'source_product',
            'destination_product', 'source_variant', 'destination_variant',
            'initiated_by'
        )

        if shop_id:
            if not Shop.objects.filter(id=shop_id, is_active=True, deleted=None).exists():
                return Response({"error": "Invalid shop specified."}, status=status.HTTP_400_BAD_REQUEST)

            if transfer_type == 'outgoing':
                queryset = queryset.filter(source_shop_id=shop_id)
            elif transfer_type == 'incoming':
                queryset = queryset.filter(destination_shop_id=shop_id)
            else:
                queryset = queryset.filter(
                    Q(source_shop_id=shop_id) | Q(destination_shop_id=shop_id)
                )
        else:
            if transfer_type == 'outgoing':
                queryset = queryset.exclude(source_shop=None)
            elif transfer_type == 'incoming':
                queryset = queryset.exclude(destination_shop=None)

        paginator = PageNumberPagination()
        paginated_history = paginator.paginate_queryset(queryset, request)
        serializer = ProductTransferHistorySerializer(paginated_history, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.error(f"Transfer history error: {str(e)}", exc_info=True)
        return Response(
            {"error": "Transfer history error"},
            status=status.HTTP_400_BAD_REQUEST
        )
