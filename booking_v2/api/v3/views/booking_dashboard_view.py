from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.response_utils import ResponseStatus
from booking_v2.models import BookingV2
from shop.models import Shop
from shop.api.admin_v1.utils.shop import get_active_shop_id
from django.utils.timezone import localdate
from booking_v2.api.v3.serializers.booking import BookingListSerializer
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


class BookingDashboardView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Booking Management V3"],
        summary="Get booking dashboard",
        description=(
            "Fetches booking dashboard counters and paginated booking cards for the active shop.\n\n"
            "Purpose: Power dashboard screens with completed, upcoming, and expired counts and a filtered list.\n\n"
            "Context: Use after user authentication and active shop selection."
        ),
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="upcoming",
                description=(
                    "Purpose: Selects list section returned in data. "
                    "Location: query parameter. Format/Type: string. "
                    "Allowed values: upcoming, ongoing. Optional. Default: upcoming."
                ),
            ),
        ],
        responses={
            200: inline_serializer(
                name="BookingDashboardSuccessV3",
                fields={
                    "count": serializers.IntegerField(),
                    "next": serializers.CharField(allow_null=True),
                    "previous": serializers.CharField(allow_null=True),
                    "status": serializers.CharField(help_text="success"),
                    "message": serializers.CharField(help_text="Dashboard data fetched successfully."),
                    "data": serializers.DictField(),
                },
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors include exact message: Invalid parameter."
                )
            ),
            403: OpenApiResponse(
                description="Permission error exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Server error exact message: Something went wrong while fetching dashboard data."
            ),
        },
    )
    def get(self, request):
        try:
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )
            shop = Shop.objects.get(id=shop_id)

            queryset = (
                BookingV2.objects.filter(shop=shop)
                .select_related("client_identity", "staff")
                .prefetch_related("booked_items")
            )

            today = localdate()

            total_completed = queryset.filter(booking_status='completed').count()

            expired_qs = queryset.filter(
                booking_status='upcoming',
                return_date__date__lt=today
            ).count()

            total_upcoming = queryset.filter(
                    booking_status='upcoming',
                    pickup_date__date__gte=today
                ).count()

            # ===== QUERY PARAM =====
            allowed_types = ["upcoming", "ongoing"]
            view_type = request.query_params.get("type", "upcoming").lower()

            if view_type not in allowed_types:
                return self.validation_error_response(
                    errors={"type": [f"Invalid type '{view_type}'. Allowed values: {allowed_types}."]},
                    message="Invalid parameter."
                )

            # ===== SELECT TYPE =====
            if view_type == "ongoing":
                # filter ongoing bookings
                source_qs = queryset.filter(
                    booking_status='upcoming',
                    pickup_date__date__lte=today,
                    return_date__date__gte=today
                ).order_by('return_date', 'id')
            else:
                # filter upcoming bookings
                source_qs = queryset.filter(
                    booking_status='upcoming',
                    pickup_date__date__gte=today
                ).order_by('pickup_date', 'id')

            # Apply pagination
            paginator = StandardPageNumberPagination()
            paginated_qs = paginator.paginate_queryset(source_qs, request)
            serializer = BookingListSerializer(paginated_qs, many=True)

            data = {
                "completed_count": total_completed,
                "upcoming_count": total_upcoming,
                "expired_count": expired_qs,
            }

            if view_type == "ongoing":
                data["ongoing_bookings"] = serializer.data
            else:
                data["upcoming_bookings"] = serializer.data

            paginated_response = paginator.get_paginated_response(data)
            paginated_response.data["status"] = ResponseStatus.SUCCESS.value
            paginated_response.data["message"] = "Dashboard data fetched successfully."
            return paginated_response

        except Exception as e:
            logger.error(f"Booking dashboard error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching dashboard data.",
                dev_message=str(e)
            )


__all__ = ["BookingDashboardView"]
