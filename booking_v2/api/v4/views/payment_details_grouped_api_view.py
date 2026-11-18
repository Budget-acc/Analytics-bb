from collections import defaultdict, OrderedDict
from datetime import datetime
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import Client, PaymentDetailsV2
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from booking_v2.api.v4.views.pagination import DayGroupedPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
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
        tags=["Booking Management V4"],
        summary="Get grouped payment details",
        description=(
            "Returns booking payment entries grouped by Month → Day with day-level pagination.\n\n"
            "Purpose: Power wallet and payment timeline screens with a grouped payment history.\n\n"
            "Context: Scoped to the active shop; supports optional filtering by a single client."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Limits the payment history to a specific client. "
                    "Location: query parameter. "
                    "Format/Type: integer. "
                    "Optional."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Paginated grouped payment structure. "
                    "Data is nested as Month → Day → { payments: {gpay, cash, total}, items: [...] }."
                )
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages (one per case):\n"
                    "- Client not found.\n"
                    "- Client not found for this shop."
                )
            ),
            403: OpenApiResponse(
                description="Permission error.\n\nExact message:\n- Your access to this shop has been deactivated."
            ),
        },
    )
    def get(self, request):
        try:
            # -------- Validate shop --------
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )

            # -------- Validate client --------
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

            # -------- Group payments by (month, day) --------
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

            # -------- Sort keys (month/day) --------
            def get_date_tuple(item):
                _, day = item
                return datetime.strptime(day, "%d-%m-%Y")

            sorted_dates = sorted(
                grouped_by_date.keys(),
                key=get_date_tuple,
                reverse=True
            )

            # -------- Build month → day → {payments, items} --------
            sorted_grouped = OrderedDict()
            for month, day in sorted_dates:
                if month not in sorted_grouped:
                    sorted_grouped[month] = OrderedDict()

                day_items = sorted(
                    grouped_by_date[(month, day)],
                    key=lambda x: datetime.strptime(x['created_at'], "%d-%m-%Y %H:%M:%S"),
                    reverse=True
                )

                # --- Daily payment summary ---
                gpay_total = sum(item["payment_amount"] for item in day_items if item["payment_method"] == "gpay")
                cash_total = sum(item["payment_amount"] for item in day_items if item["payment_method"] == "cash")
                total = sum(item["payment_amount"] for item in day_items)

                sorted_grouped[month][day] = {
                    "payments": {
                        "gpay": gpay_total,
                        "cash": cash_total,
                        "total": total
                    },
                    "items": day_items
                }

            # -------- Pagination (flatten for slicing) --------
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, content in days.items():
                    flat_group_by_day[f"{month}|{day}"] = content

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # -------- Rebuild paginated structure --------
            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):  # Paginated case
                for key, content in paginated_group.items():
                    month, day = key.split("|")
                    if month not in final_group:
                        final_group[month] = OrderedDict()
                    final_group[month][day] = content
            else:
                final_group = sorted_grouped

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(f"PaymentDetailsGroupedAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching grouped payment details.",
                dev_message=str(e)
            )
