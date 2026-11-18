import logging
from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop
from services.models import ProductTransferHistory
from services.api.v3.serializers import ProductTransferHistorySerializer
from ERP.utils.custom_pagination import StandardPageNumberPagination

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Get transfer history",
    description="Returns paginated transfer history for a shop with optional incoming/outgoing type filter.",
    parameters=[
        OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Shop id."),
        OpenApiParameter("type", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="all, outgoing, incoming. Default all."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated transfer history response."),
        400: OpenApiResponse(
            description=(
                "Validation/error messages:\n"
                "- You must provide a valid shop.\n"
                "- shop_id must be a valid integer.\n"
                "- Transfer history error"
            )
        ),
        403: OpenApiResponse(description="Exact message: You do not have permission to access this shop."),
        404: OpenApiResponse(description="Exact message: Invalid shop specified."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 4, "quantity_transferred": 2, "transfer_type": "TO_EXISTING"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Missing shop response", value={"status": "validation_error", "message": "You must provide a valid shop.", "errors": {"shop_id": ["You must provide a valid shop."]}}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["get"], url_path="transfer-history")
def transfer_history(self, request):
    try:
        shop_id = request.query_params.get('shop_id')
        transfer_type = request.query_params.get('type', 'all')

        if not shop_id:
            return self.validation_error_response(
                errors={"shop_id": ["You must provide a valid shop."]},
                message="You must provide a valid shop."
            )

        if not str(shop_id).isdigit():
            return self.validation_error_response(
                errors={"shop_id": ["shop_id must be a valid integer."]},
                message="shop_id must be a valid integer."
            )

        queryset = ProductTransferHistory.objects.select_related(
            'source_shop', 'destination_shop', 'source_product',
            'destination_product', 'source_variant', 'destination_variant',
            'initiated_by'
        )

        if shop_id:
            if not Shop.objects.filter(id=shop_id, is_active=True, deleted=None).exists():
                return self.not_found_response(
                    resource="Shop",
                    message="Invalid shop specified."
                )

            if not self._check_user_permission(request.user, shop_id):
                return self.permission_denied_response(
                    message="You do not have permission to access this shop."
                )

            if transfer_type == 'outgoing':
                queryset = queryset.filter(source_shop_id=shop_id)
            elif transfer_type == 'incoming':
                queryset = queryset.filter(destination_shop_id=shop_id)
            else:
                queryset = queryset.filter(
                    Q(source_shop_id=shop_id) | Q(destination_shop_id=shop_id)
                )

        paginator = StandardPageNumberPagination()
        paginated_history = paginator.paginate_queryset(queryset, request)
        serializer = ProductTransferHistorySerializer(paginated_history, many=True, context={'request': request})

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.error(f"Transfer history error: {str(e)}", exc_info=True)
        return Response(
            {"error": "Transfer history error"},
            status=status.HTTP_400_BAD_REQUEST
        )
