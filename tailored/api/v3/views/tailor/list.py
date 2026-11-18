from ERP.utils.custom_pagination import StandardPageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from tailored.models import Tailor
from tailored.api.v3.serializers import TailorSerializer
from django.db.models import When, Case, Value, IntegerField, Q
import logging
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="List tailor members",
    description="Returns paginated tailor members for the active shop with optional name, phone, and soft-delete filters.",
    parameters=[
        OpenApiParameter("name", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search by tailor name."),
        OpenApiParameter("phone", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search by tailor phone number."),
        OpenApiParameter("is_deleted", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Defaults to false. Set true to list deleted tailor members."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page number."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated tailor list response."),
        500: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- Unable to retrieve tailor list. Please try again later.\n"
                "- Access to this shop has been deactivated."
            )
        ),
    },
    examples=[
        OpenApiExample(
            "List success",
            value={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 5,
                        "name": "Rahul Tailor",
                        "phone": "+919876543210",
                        "is_deleted": False,
                        "created_at": "04-05-2026 11:30:00"
                    }
                ]
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@skip_subscription_validation
def list(self, request):
    """List all tailor for the current shop with optional search and pagination"""
    try:
        search_name = request.query_params.get("name")
        search_phone = request.query_params.get("phone")
        is_deleted_param = request.query_params.get("is_deleted", "false").lower()

        show_deleted = is_deleted_param == "true"

        tailor_qs = Tailor.objects.filter(shop_id=self.shop_id, is_deleted=show_deleted)

        if search_name or search_phone:

            when_conditions = []

            # ----- NAME CONDITIONS -----
            if search_name:
                search_name = search_name.strip()

                when_conditions.extend([
                    When(name__iexact=search_name, then=Value(0)),
                    When(name__istartswith=search_name, then=Value(1)),
                    When(name__icontains=search_name, then=Value(2)),
                ])

            # ----- PHONE CONDITIONS -----
            if search_phone:
                search_phone = search_phone.strip()

                when_conditions.extend([
                    When(phone__iexact=search_phone, then=Value(0)),
                    When(phone__istartswith=search_phone, then=Value(1)),
                    When(phone__icontains=search_phone, then=Value(2)),
                ])

            tailor_qs = tailor_qs.annotate(
                search_rank=Case(
                    *when_conditions,
                    default=Value(3),
                    output_field=IntegerField(),
                )
            )

            filters = Q()

            if search_name:
                filters &= Q(name__icontains=search_name)

            if search_phone:
                filters &= Q(phone__icontains=search_phone)

            tailor_qs = tailor_qs.filter(filters).order_by("search_rank", "name")

        else:
            tailor_qs = tailor_qs.order_by("name")


        paginator = StandardPageNumberPagination()
        paginated_qs = paginator.paginate_queryset(tailor_qs, request)
        serializer = TailorSerializer(paginated_qs, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.error(f"Error retrieving tailor list for shop {self.shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve tailor list. Please try again later.",
            dev_message=str(e)
        )
