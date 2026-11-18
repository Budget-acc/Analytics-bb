import logging
from collections import defaultdict, OrderedDict
from datetime import datetime
from django.db.models import F, Q, Case, When, Value, DecimalField, Subquery, OuterRef, Sum
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import BookingV2, Client, BookedProductVariant
from tailored.models import TailorOrder, TailorOrderItem
from booking_v2.api.v5.views.pagination import DayGroupedPagination
from django.db import models
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class PendingBookingAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Booking Management V5"],
        summary="Get pending booking amounts",
        description=(
            "Returns pending receivable amounts from bookings and tailor orders grouped by Month -> Day."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional client filter.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Grouped pending amount response."),
            400: OpenApiResponse(
                description="Validation errors.\n\nExact messages:\n- Client ID must be a valid integer.\n- Client not found for this shop."
            ),
            403: OpenApiResponse(
                description="Permission error.\n\nExact message:\n- Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Server error.\n\nExact message:\n- Something went wrong while fetching pending amounts."
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

            # Validate client if provided
            client = None
            if client_id:
                if not client_id.isdigit():
                    return self.validation_error_response(
                        errors={"client_id": ["Client ID must be a valid integer."]},
                        message="Client ID must be a valid integer."
                    )
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id)
                except Client.DoesNotExist:
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found for this shop."]},
                        message="Client not found for this shop."
                    )

            # -------- Bookings Query --------
            bookings_query = BookingV2.objects.filter(
                shop_id=shop_id,
                deleted__isnull=True,
                total_amount__gt=0
            ).exclude(
                delivery_status="cancelled"
            ).select_related("client_identity").annotate(
                remaining_amount=Coalesce(F('total_amount'), Value(0)) - Coalesce(F('advance_amount'), Value(0)) - Coalesce(F('discount_amount'), Value(0)),
                item_id=F('id'),
                invoice_id_field=F('shop_booking_id'),
                client_name_field=F('client_identity__client_name'),
                date_field=F('return_date')
            ).values(
                'item_id',
                'invoice_id_field',
                'client_name_field',
                'remaining_amount',
                'date_field'
            ).filter(
                remaining_amount__gt=0
            ).order_by('return_date')

            if client:
                bookings_query = bookings_query.filter(client_identity=client)

            # -------- Tailor Orders Query --------
            item_charge_subquery = Subquery(
                TailorOrderItem.objects.filter(
                    order_id=OuterRef('pk')
                ).values('order_id').annotate(
                    total=Sum('customer_item_charge')
                ).values('total')[:1],
                output_field=DecimalField()
            )

            tailor_query = TailorOrder.objects.filter(
                shop_id=shop_id,
                deleted__isnull=True,
                customer_order_type='rental'
            ).exclude(
                status__in=['cancelled', 'completed']
            ).select_related("client").annotate(
                total_item_charge=Coalesce(item_charge_subquery, Value(0, output_field=DecimalField())),
            ).annotate(
                remaining_amount=(
                    Coalesce(F('total_item_charge'), Value(0, output_field=DecimalField())) +
                    Coalesce(F('total_additional_charges'), Value(0, output_field=DecimalField())) -
                    Coalesce(F('paid_amount'), Value(0, output_field=DecimalField())) -
                    Coalesce(F('discount_amount'), Value(0, output_field=DecimalField()))
                ),
                item_id=F('id'),
                invoice_id_field=F('shop_tailor_id'),
                client_name_field=F('client__client_name'),
                date_field=F('return_date')
            ).values(
                'item_id',
                'invoice_id_field',
                'client_name_field',
                'remaining_amount',
                'date_field'
            ).filter(
                remaining_amount__gt=0,
                date_field__isnull=False
            ).order_by('date_field')

            if client:
                tailor_query = tailor_query.filter(client=client)

            # -------- Combine Data --------
            combined_data = []
            # Preload all booked items for all bookings in one query
            booking_ids = [item['item_id'] for item in bookings_query]
            booked_items_map = {}
            if booking_ids:
                booked_items = BookedProductVariant.objects.filter(booking_id__in=booking_ids)
                for b in booked_items:
                    booked_items_map.setdefault(b.booking_id, []).append(b.product_name)

            # Preload all tailor order items for all tailor orders in one query
            tailor_ids = [item['item_id'] for item in tailor_query]
            tailor_items_map = {}
            if tailor_ids:
                tailor_items = TailorOrderItem.objects.filter(order_id__in=tailor_ids)
                for t in tailor_items:
                    tailor_items_map.setdefault(t.order_id, []).append(t.product_name)

            # Process bookings
            for item in bookings_query:
                date_value = item['date_field']
                if date_value:
                    local_date = localtime(date_value) if date_value else None
                    normalized_date = local_date.date() if local_date else None
                    items_list = booked_items_map.get(item['item_id'], [])
                    items_str = ", ".join(items_list)
                    combined_data.append({
                        "id": item['item_id'],
                        "invoice_id": item['invoice_id_field'] or f"B{item['item_id']}",
                        "client_name": item['client_name_field'],
                        "remaining_amount": int(float(item['remaining_amount'])) if item['remaining_amount'] else 0,
                        "date": normalized_date.strftime("%d-%m-%Y") if normalized_date else None,
                        "type": "booking",
                        "items": items_str,
                        "sort_date": normalized_date
                    })

            # Process tailor orders
            for item in tailor_query:
                date_value = item['date_field']
                if date_value:
                    local_date = localtime(date_value) if date_value else None
                    normalized_date = local_date.date() if local_date else None
                    client_name = item['client_name_field'] if item['client_name_field'] else None
                    items_list = tailor_items_map.get(item['item_id'], [])
                    items_str = ", ".join(items_list)
                    combined_data.append({
                        "id": item['item_id'],
                        "invoice_id": item['invoice_id_field'] or f"TO{item['item_id']}",
                        "client_name": client_name,
                        "remaining_amount": int(float(item['remaining_amount'])) if item['remaining_amount'] else 0,
                        "date": normalized_date.strftime("%d-%m-%Y") if normalized_date else None,
                        "type": "tailor_order",
                        "items": items_str,
                        "sort_date": normalized_date
                    })

            # -------- Grouping by Date --------
            grouped_by_date = defaultdict(list)

            for item in combined_data:
                if item["sort_date"]:
                    month_key = item["sort_date"].strftime("%B %Y")
                    day_key = item["sort_date"].strftime("%d-%m-%Y")

                    # Remove helper field for final output
                    final_item = {k: v for k, v in item.items() if k != 'sort_date'}
                    grouped_by_date[(month_key, day_key, item["sort_date"])].append(final_item)

            # -------- Efficient Sorting --------
            def get_sort_tuple(item):
                month, day, dt = item
                if dt:
                    return (dt.year, dt.month, dt.day)
                return (0, 0, 0)

            sorted_dates = sorted(grouped_by_date.keys(), key=get_sort_tuple)

            # -------- Structure with Totals --------
            sorted_grouped = OrderedDict()
            for month, day, dt in sorted_dates:
                if month not in sorted_grouped:
                    sorted_grouped[month] = OrderedDict()

                day_items = grouped_by_date[(month, day, dt)]

                # Sort items within each day by remaining_amount (highest first)
                sorted_items = sorted(day_items, key=lambda x: x["remaining_amount"], reverse=True)

                total_pending = sum(item["remaining_amount"] for item in day_items)

                sorted_grouped[month][day] = {
                    "total_pending": total_pending,
                    "items": sorted_items
                }

            # -------- Pagination --------
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, items in days.items():
                    flat_group_by_day[f"{month}|{day}"] = items

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # Rebuild final grouped structure
            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):
                for key, items in paginated_group.items():
                    month, day = key.split("|")
                    if month not in final_group:
                        final_group[month] = OrderedDict()
                    final_group[month][day] = items
            else:
                final_group = sorted_grouped

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(f"Pending booking amount error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching pending amounts.",
                dev_message=str(e)
            )


__all__ = ["PendingBookingAmountAPIView"]
