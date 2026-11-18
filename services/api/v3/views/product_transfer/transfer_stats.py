import logging
from django.db.models import Q, Sum
from django.utils.timezone import now
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop
from services.models import ProductTransferHistory

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Get transfer statistics",
    description="Returns aggregate transfer counts and quantities for a specific shop.",
    parameters=[
        OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Shop id."),
    ],
    responses={
        200: OpenApiResponse(description="Standard success response with transfer stats object."),
        400: OpenApiResponse(
            description=(
                "Validation/error messages:\n"
                "- You must provide a valid shop.\n"
                "- shop_id must be a valid integer.\n"
                "- Transfer stats error"
            )
        ),
        403: OpenApiResponse(description="Exact message: You do not have permission to access this shop."),
        404: OpenApiResponse(description="Exact message: Invalid shop specified."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Transfer stats retrieved successfully.", "data": {"total_transfers": 20, "this_month_transfers": 6, "outgoing_transfers": 9, "incoming_transfers": 11, "total_quantity_transferred": 84}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="transfer-stats")
def transfer_stats(self, request):
    try:
        shop_id = request.query_params.get('shop_id')

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

            base_filter = Q(source_shop_id=shop_id) | Q(destination_shop_id=shop_id)
            outgoing_filter = Q(source_shop_id=shop_id)
            incoming_filter = Q(destination_shop_id=shop_id)

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

        return self.success_response(
            data=stats,
            message="Transfer stats retrieved successfully."
        )

    except Exception as e:
        logger.error(f"Transfer stats error: {str(e)}", exc_info=True)
        return Response(
            {"error": "Transfer stats error"},
            status=status.HTTP_400_BAD_REQUEST
        )
