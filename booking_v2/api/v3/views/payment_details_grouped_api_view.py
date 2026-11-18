from datetime import datetime
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from booking_v2.models import PaymentDetailsV2, Client
from shop.api.admin_v1.utils.shop import get_active_shop_id
from django.utils.timezone import localtime
from collections import defaultdict, OrderedDict
from booking_v2.api.v3.views.pagination import DayGroupedPagination
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
import logging

logger = logging.getLogger("api_requests")


class PaymentDetailsGroupedAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Booking Management V3"],
        summary="Get grouped payment details",
        description=(
            "Returns booking payment entries grouped by Month -> Day with pagination.\n\n"
            "Purpose: Supports wallet/payment timeline screens with grouped history.\n\n"
            "Context: Works on active shop and can optionally filter by a specific client."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Limits payment history to one client. "
                    "Location: query parameter. Format/Type: integer. Optional."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(description="Paginated grouped payment structure with status/message/data envelope."),
            400: OpenApiResponse(
                description=(
                    "Validation errors exact messages: Client not found.; Client not found for this shop."
                )
            ),
            403: OpenApiResponse(
                description="Permission error exact message: Your access to this shop has been deactivated."
            ),
        },
    )
    def get(self, request):
        try:
            shop_id = get_active_shop_id(request)
        except Exception:
            return self.permission_denied_response(
                message="Your access to this shop has been deactivated."
            )
        client_id = request.query_params.get("client_id")

        payments_qs = PaymentDetailsV2.objects.select_related(
            "booking", "booking__client_identity"
        ).filter(
            booking__shop_id=shop_id,
            booking__deleted__isnull=True
        ).order_by("-created_at")

        if client_id:
            if not client_id.isdigit():
                return self.validation_error_response(
                    errors={"client_id": ["Client not found."]},
                    message="Client not found."
                )

            if not Client.objects.filter(id=client_id, shop_id=shop_id).exists():
                return self.validation_error_response(
                    errors={"client_id": ["Client not found for this shop."]},
                    message="Client not found for this shop."
                )

            payments_qs = payments_qs.filter(booking__client_identity_id=client_id)

        # Group payments
        grouped_by_date = defaultdict(list)
        for payment in payments_qs:
            local_created = localtime(payment.created_at)
            date_key = local_created.strftime("%d-%m-%Y")
            month_key = local_created.strftime("%B %Y")
            grouped_by_date[(month_key, date_key)].append({
                "booking_id": payment.booking_id,
                "payment_amount": payment.payment_amount,
                "payment_method": payment.payment_method,
                "client_name": payment.booking.client_identity.client_name if payment.booking.client_identity else "",
                "created_at": local_created.strftime("%d-%m-%Y %H:%M:%S")
            })

        # Convert dates to datetime objects for proper sorting
        def get_date_tuple(item):
            month, day = item
            return datetime.strptime(day, "%d-%m-%Y")

        # Sort by date in descending order (newest first)
        sorted_dates = sorted(
            grouped_by_date.keys(),
            key=get_date_tuple,
            reverse=True
        )

        # Organize data into months with days in descending order
        sorted_grouped = OrderedDict()
        for month, day in sorted_dates:
            if month not in sorted_grouped:
                sorted_grouped[month] = OrderedDict()
            # Sort items within each day by created_at descending
            day_items = sorted(
                grouped_by_date[(month, day)],
                key=lambda x: datetime.strptime(x['created_at'], "%d-%m-%Y %H:%M:%S"),
                reverse=True
            )
            sorted_grouped[month][day] = day_items

        # Flatten by day for pagination while maintaining order
        flat_group_by_day = OrderedDict()
        for month, days in sorted_grouped.items():
            for day, items in days.items():
                flat_group_by_day[f"{month}|{day}"] = items

        paginator = DayGroupedPagination()
        paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

        # Rebuild month → day → list structure in correct order
        final_group = OrderedDict()
        if isinstance(paginated_group, OrderedDict):  # If paginated
            for key, items in paginated_group.items():
                month, day = key.split("|")
                if month not in final_group:
                    final_group[month] = OrderedDict()
                final_group[month][day] = items
        else:  # If not paginated
            final_group = sorted_grouped

        return paginator.get_paginated_response(final_group)


__all__ = ["PaymentDetailsGroupedAPIView"]
