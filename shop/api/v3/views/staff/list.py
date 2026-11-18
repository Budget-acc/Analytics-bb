from shop.models import Staff
from shop.api.v3.serializers import StaffSerializer
from ERP.utils.custom_pagination import StandardPageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="List staff",
    description="Returns paginated staff list for active shop with optional name/phone and deleted-state filters.",
    parameters=[
        OpenApiParameter("name", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Filter by name contains."),
        OpenApiParameter("phone", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Filter by phone contains."),
        OpenApiParameter("is_deleted", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Default false."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated staff list response."),
        500: OpenApiResponse(description="Exact message: Unable to retrieve staff list. Please try again later."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 10, "name": "Raju", "phone": "9999999999", "is_deleted": False}]}, response_only=True, status_codes=["200"]),
    ],
)
@skip_subscription_validation
def list(self, request):
    """List all staff for the current shop with optional search and pagination"""
    try:
        search_name = request.query_params.get("name")
        search_phone = request.query_params.get("phone")
        is_deleted_param = request.query_params.get("is_deleted", "false").lower()

        show_deleted = is_deleted_param == "true"

        staff_qs = Staff.objects.filter(shop_id=self.shop_id, is_deleted=show_deleted)

        if search_name:
            staff_qs = staff_qs.filter(name__icontains=search_name.strip())
        if search_phone:
            staff_qs = staff_qs.filter(phone__icontains=search_phone.strip())

        staff_qs = staff_qs.order_by("name")

        paginator = StandardPageNumberPagination()
        paginated_qs = paginator.paginate_queryset(staff_qs, request)
        serializer = StaffSerializer(paginated_qs, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logs.error(f"Error retrieving staff list for shop {self.shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve staff list. Please try again later.",
            dev_message=str(e)
        )
