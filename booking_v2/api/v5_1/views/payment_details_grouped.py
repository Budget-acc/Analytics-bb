import logging
from collections import defaultdict, OrderedDict
from datetime import datetime

from django.db.models import F
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import Client, PaymentDetailsV2, SecurityDepositPayment
from sales.models import SalesPaymentDetails
from tailored.models import CustomerPayment, TailorOrderSecurityPayment
from finance.models import ManualIncome
from booking_v2.api.v5.views.pagination import DayGroupedPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class PaymentDetailsGroupedAPIViewV51(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Booking Management V5.1"],
        summary="Get grouped payment details (V5.1)",
        description=(
            "Returns payments grouped by Month → Day from six sources: "
            "booking payments, booking security payments, sales payments, "
            "tailor order customer payments, tailor order security payments, and manual income. "
            "Original payment amounts are returned directly — no refund-adjusted deductions. "
            "Manual income entries use transaction_datetime as the date. "
            "Each item includes 'category' and 'description' fields (populated for manual income, null otherwise). "
            "Results are paginated in groups of 6 days per page."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional client filter. Applied to bookings, sales, and tailor orders. "
                    "Manual income entries are excluded when this filter is active."
                ),
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Day-group page number. Default: 1.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Grouped day-wise payment response.\n\n"
                    "Shape: { '<Month YYYY>': { '<DD-MM-YYYY>': { "
                    "'payments': { 'upi': Decimal, 'cash': Decimal, 'total': Decimal } "
                    "(security payments excluded), "
                    "'total_security_amount': Decimal, "
                    "'security_amount': { 'upi': Decimal, 'cash': Decimal, 'total': Decimal }, "
                    "'items': [ { 'id', 'invoice_id', 'client_name', 'payment_amount', "
                    "'payment_method', 'created_at', 'type', 'category', 'description' } ] } } }\n\n"
                    "type values: booking, booking_security, sale, tailor_order, tailor_order_security, manual_income.\n\n"
                    "Pagination metadata: count, next, previous."
                )
            ),
            400: OpenApiResponse(
                description="Validation errors.\n\nExact messages:\n- Client not found.\n- Client not found for this shop."
            ),
            403: OpenApiResponse(
                description="Exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Something went wrong while fetching grouped payment details."
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

            client_id = request.query_params.get("client_id")
            client = None
            if client_id:
                if not client_id.isdigit():
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found."]},
                        message="Client not found."
                    )
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id)
                except Client.DoesNotExist:
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found for this shop."]},
                        message="Client not found for this shop."
                    )

            combined = []

            # --- Booking Payments ---
            # BookingV2 is SafeDeleteModel → filter booking__deleted__isnull=True
            booking_qs = (
                PaymentDetailsV2.objects
                .filter(booking__shop_id=shop_id, booking__deleted__isnull=True)
                .select_related("booking", "booking__client_identity", "account")
                .annotate(
                    invoice_id=F("booking__shop_booking_id"),
                    client_name=F("booking__client_identity__client_name"),
                )
                .values(
                    "id",
                    "invoice_id",
                    "client_name",
                    "payment_amount",
                    "payment_method",
                    "created_at",
                    "account_id",
                    "account__name",
                    "booking_id",
                )
            )
            if client:
                booking_qs = booking_qs.filter(booking__client_identity=client)

            for row in booking_qs.iterator(chunk_size=2000):
                local_dt = localtime(row["created_at"])
                combined.append({
                    "id": row["id"],
                    "invoice_id": row["invoice_id"],
                    "client_name": row["client_name"],
                    "payment_amount": row["payment_amount"],
                    "payment_method": row["payment_method"],
                    "account_id": row["account_id"],
                    "account_name": row.get("account__name"),
                    "created_at": local_dt.strftime("%d-%m-%Y %H:%M:%S"),
                    "type": "booking",
                    "category": None,
                    "description": None,
                    "reference_id": row["booking_id"],
                    "sort_datetime": local_dt,
                })

            # --- Sales Payments ---
            # Sale is models.Model (no soft delete) → no deleted filter needed
            sales_qs = (
                SalesPaymentDetails.objects
                .filter(sale__shop_id=shop_id)
                .select_related("sale", "sale__client", "account")
                .annotate(
                    invoice_id=F("sale__shop_sale_id"),
                    client_name=F("sale__client__client_name"),
                )
                .values(
                    "id",
                    "invoice_id",
                    "client_name",
                    "payment_amount",
                    "payment_method",
                    "created_at",
                    "account_id",
                    "account__name",
                )
            )
            if client:
                sales_qs = sales_qs.filter(sale__client=client)

            for row in sales_qs.iterator(chunk_size=2000):
                local_dt = localtime(row["created_at"])
                combined.append({
                    "id": row["id"],
                    "invoice_id": row["invoice_id"],
                    "client_name": row["client_name"],
                    "payment_amount": row["payment_amount"],
                    "payment_method": row["payment_method"],
                    "account_id": row["account_id"],
                    "account_name": row.get("account__name"),
                    "created_at": local_dt.strftime("%d-%m-%Y %H:%M:%S"),
                    "type": "sale",
                    "category": None,
                    "description": None,
                    "reference_id": None,
                    "sort_datetime": local_dt,
                })

            # --- Booking Security Payments ---
            # SecurityDepositPayment is plain Model — filter via booking__deleted__isnull=True
            # Only include paid security payments and use paid_at as the payment date.
            booking_security_qs = (
                SecurityDepositPayment.objects
                .filter(
                    booking__shop_id=shop_id,
                    booking__deleted__isnull=True,
                    is_paid=True,
                    paid_at__isnull=False,
                )
                .select_related("booking", "booking__client_identity", "account")
                .annotate(
                    invoice_id=F("booking__shop_booking_id"),
                    client_name=F("booking__client_identity__client_name"),
                )
                .values(
                    "id", "invoice_id", "client_name",
                    "amount", "payment_method", "paid_at",
                    "account_id", "account__name",
                    "booking_id",
                )
            )
            if client:
                booking_security_qs = booking_security_qs.filter(booking__client_identity=client)

            for row in booking_security_qs.iterator(chunk_size=2000):
                payment_dt = row.get("paid_at")
                local_dt = localtime(payment_dt) if payment_dt else None
                combined.append({
                    "id": row["id"],
                    "invoice_id": row["invoice_id"],
                    "client_name": row["client_name"],
                    "payment_amount": row["amount"],
                    "payment_method": row["payment_method"],
                    "account_id": row["account_id"],
                    "account_name": row.get("account__name"),
                    "created_at": local_dt.strftime("%d-%m-%Y %H:%M:%S") if local_dt else None,
                    "type": "booking_security",
                    "category": None,
                    "description": None,
                    "reference_id": row["booking_id"],
                    "sort_datetime": local_dt,
                })

            # --- Tailor Order Payments ---
            # TailorOrder is SafeDeleteModel → filter order__deleted__isnull=True
            tailor_qs = (
                CustomerPayment.objects
                .filter(order__shop_id=shop_id, order__deleted__isnull=True, order__created_booking__isnull=True,)
                .select_related("order", "order__client", "account")
                .annotate(
                    invoice_id=F("order__shop_tailor_id"),
                    client_name=F("order__client__client_name"),
                )
                .values(
                    "id",
                    "invoice_id",
                    "client_name",
                    "payment_amount",
                    "payment_method",
                    "created_at",
                    "account_id",
                    "account__name",
                    "order_id",
                )
            )
            if client:
                tailor_qs = tailor_qs.filter(order__client=client)

            for row in tailor_qs.iterator(chunk_size=2000):
                local_dt = localtime(row["created_at"])
                combined.append({
                    "id": row["id"],
                    "invoice_id": row["invoice_id"],
                    "client_name": row["client_name"],
                    "payment_amount": row["payment_amount"],
                    "payment_method": row["payment_method"],
                    "account_id": row["account_id"],
                    "account_name": row.get("account__name"),
                    "created_at": local_dt.strftime("%d-%m-%Y %H:%M:%S"),
                    "type": "tailor_order",
                    "category": None,
                    "description": None,
                    "reference_id": row["order_id"],
                    "sort_datetime": local_dt,
                })

            # --- Tailor Order Security Payments ---
            # Only include paid security payments and use paid_at as payment date.
            # TailorOrder is SafeDeleteModel -> filter order__deleted__isnull=True

            tailor_security_qs = (
                TailorOrderSecurityPayment.objects
                .filter(
                    order__shop_id=shop_id,
                    order__deleted__isnull=True,
                    order__created_booking__isnull=True,
                    is_paid=True,
                    paid_at__isnull=False,
                )
                .select_related(
                    "order",
                    "order__client",
                    "account",
                )
                .annotate(
                    invoice_id=F("order__shop_tailor_id"),
                    client_name=F("order__client__client_name"),
                )
                .values(
                    "id",
                    "invoice_id",
                    "client_name",
                    "amount",
                    "payment_method",
                    "paid_at",
                    "account_id",
                    "account__name",
                    "order_id",
                )
            )

            if client:
                tailor_security_qs = tailor_security_qs.filter(
                    order__client=client
                )

            for row in tailor_security_qs.iterator(chunk_size=2000):
                payment_dt = row.get("paid_at")
                local_dt = localtime(payment_dt) if payment_dt else None

                combined.append({
                    "id": row["id"],
                    "invoice_id": row["invoice_id"],
                    "client_name": row["client_name"],
                    "payment_amount": row["amount"],
                    "payment_method": row["payment_method"],
                    "account_id": row["account_id"],
                    "account_name": row.get("account__name"),
                    "created_at": (
                        local_dt.strftime("%d-%m-%Y %H:%M:%S")
                        if local_dt else None
                    ),
                    "type": "tailor_order_security",
                    "category": None,
                    "description": None,
                    "reference_id": row["order_id"],
                    "sort_datetime": local_dt,
                })

            # --- Manual Income ---
            # ManualIncome is SafeDeleteModel — default manager auto-excludes soft-deleted rows.
            # Excluded entirely when client_id filter is active.
            if not client:
                income_qs = (
                    ManualIncome.objects
                    .filter(shop_id=shop_id)
                    .select_related("category", "account")
                    .values(
                        "id",
                        "amount",
                        "payment_method",
                        "transaction_date_time",
                        "description",
                        "category__name",
                        "account_id",
                        "account__name",
                    )
                )
                for row in income_qs.iterator(chunk_size=2000):
                    local_dt = localtime(row["transaction_date_time"])
                    combined.append({
                        "id": row["id"],
                        "invoice_id": None,
                        "client_name": None,
                        "payment_amount": row["amount"],
                        "payment_method": row["payment_method"],
                        "account_id": row["account_id"],
                        "account_name": row.get("account__name"),
                        "created_at": local_dt.strftime("%d-%m-%Y %H:%M:%S"),
                        "type": "manual_income",
                        "category": row["category__name"],
                        "description": row["description"],
                        "reference_id": None,
                        "sort_datetime": local_dt,
                    })

            combined.sort(key=lambda x: x["sort_datetime"], reverse=True)

            grouped_by_date = defaultdict(list)
            for payment in combined:
                date_key = payment["sort_datetime"].strftime("%d-%m-%Y")
                month_key = payment["sort_datetime"].strftime("%B %Y")
                entry = {k: v for k, v in payment.items() if k != "sort_datetime"}
                grouped_by_date[(month_key, date_key)].append(entry)

            def get_date_tuple(item):
                _, day = item
                return datetime.strptime(day, "%d-%m-%Y")

            sorted_dates = sorted(grouped_by_date.keys(), key=get_date_tuple, reverse=True)

            method_mapping = {"gpay": "upi", "upi": "upi", "cash": "cash"}

            sorted_grouped = OrderedDict()
            for month, day in sorted_dates:
                if month not in sorted_grouped:
                    sorted_grouped[month] = OrderedDict()

                day_items = sorted(
                    grouped_by_date[(month, day)],
                    key=lambda x: datetime.strptime(x["created_at"], "%d-%m-%Y %H:%M:%S"),
                    reverse=True,
                )

                _security_types = {"booking_security", "tailor_order_security"}

                # Single-pass split: security vs non-security items
                security_items = []
                non_security_items = []
                for item in day_items:
                    if item.get("type") in _security_types:
                        security_items.append(item)
                    else:
                        non_security_items.append(item)

                # payments: non-security items only (security is shown separately)
                upi_total = sum(
                    item["payment_amount"] for item in non_security_items
                    if method_mapping.get(item["payment_method"].lower(), item["payment_method"].lower()) == "upi"
                )
                cash_total = sum(
                    item["payment_amount"] for item in non_security_items
                    if method_mapping.get(item["payment_method"].lower(), item["payment_method"].lower()) == "cash"
                )
                total = sum(item["payment_amount"] for item in non_security_items)

                # security breakdown
                total_security_amount = sum(item["payment_amount"] for item in security_items)
                security_cash = sum(
                    item["payment_amount"] for item in security_items
                    if method_mapping.get(item["payment_method"].lower(), item["payment_method"].lower()) == "cash"
                )
                security_upi = total_security_amount - security_cash

                sorted_grouped[month][day] = {
                    "payments": {
                        "upi": upi_total,
                        "cash": cash_total,
                        "total": total,
                    },
                    "total_security_amount": total_security_amount,
                    "security_amount": {
                        "upi": security_upi,
                        "cash": security_cash,
                        "total": total_security_amount,
                    },
                    "items": day_items,
                }

            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, content in days.items():
                    flat_group_by_day[f"{month}|{day}"] = content

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):
                for key, content in paginated_group.items():
                    month, day = key.split("|")
                    if month not in final_group:
                        final_group[month] = OrderedDict()
                    final_group[month][day] = content
            else:
                final_group = sorted_grouped

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(f"PaymentDetailsGroupedAPIViewV51 error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching grouped payment details.",
                dev_message=str(e)
            )
