import logging
from rest_framework import status
from rest_framework.decorators import action
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product, ProductVariant
from shop.models import ShopUser
from booking_v2.models import BookingV2
from ERP.utils.custom_pagination import StandardPageNumberPagination

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="List bookings for a variant",
    description="Returns paginated booking history for one active product variant.",
    parameters=[
        OpenApiParameter("variant_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Variant id."),
        OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Booking status filter: all, upcoming, completed. Default all."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated booking list for the variant."),
        403: OpenApiResponse(
            description=(
                "Permission messages:\n"
                "- You do not have permission to access this product.\n"
                "- Variant or related entities are inactive.\n"
                "- You do not have access to this shop."
            )
        ),
        404: OpenApiResponse(description="Exact message: The requested variant does not exist or is inactive."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"booking_id": 12, "booking_status": "upcoming", "client": {"client_name": "Aman"}}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found", value={"status": "not_found", "message": "The requested variant does not exist or is inactive.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)
@action(detail=False, methods=["get"], url_path=r"variant-bookings/(?P<variant_id>\d+)")
def variant_bookings(self, request, variant_id=None):
    user = request.user
    booking_status = request.query_params.get("status", "all")
    try:
        from shop.api.v3.utils.shop import get_active_shop_id
        shop_id = get_active_shop_id(request)
    except Exception as e:
        return self.permission_denied_response(
            message="You do not have permission to access this product."
        )

    try:
        variant = ProductVariant.objects.select_related(
            "product__shop_service__shop"
        ).get(id=variant_id, is_active=True)
    except ProductVariant.DoesNotExist:
        return self.not_found_response(
            resource="ProductVariant",
            message="The requested variant does not exist or is inactive.",
            dev_message=f"Invalid or inactive variant_id: {variant_id}"
        )

    product = variant.product
    shop_service = product.shop_service
    shop = shop_service.shop

    if not (product.is_active and shop_service.is_active):
        return self.error_response(
            message="Variant or related entities are inactive.",
            dev_message=f"Inactive product ({product.id}) or shop_service ({shop_service.id}) for variant {variant_id}",
            http_status=status.HTTP_403_FORBIDDEN
        )

    is_user_linked = ShopUser.objects.filter(shop=shop, user=user).exists()
    if not is_user_linked:
        return self.permission_denied_response(
            message="You do not have access to this shop."
        )

    # Get relevant bookings
    bookings_qs = BookingV2.objects.filter(
        booked_items__product_variant_id=variant.id,
        shop=shop
    ).select_related("client_identity").distinct()

    if booking_status == "upcoming":
        bookings_qs = bookings_qs.filter(booking_status="upcoming")
    elif booking_status == "completed":
        bookings_qs = bookings_qs.filter(booking_status="completed")

    paginator = StandardPageNumberPagination()
    page = paginator.paginate_queryset(bookings_qs, request)

    result = []
    for booking in page:
        result.append({
            "booking_id": booking.id,
            "pickup_date": localtime(booking.pickup_date).strftime("%d-%m-%Y"),
            "return_date": localtime(booking.return_date).strftime("%d-%m-%Y") if booking.return_date else booking.pickup_date.strftime("%d-%m-%Y"),
            "total_amount": booking.total_amount,
            "advance_amount": booking.advance_amount,
            "security_amount": booking.security_amount,
            "payment_status": booking.payment_status,
            "delivery_status": booking.delivery_status,
            "booking_status": booking.booking_status,
            "purchase_mode": booking.purchase_mode,
            "client_address": booking.client_address,
            "client": {
                "client_name": booking.client_identity.client_name,
                "client_phone_1": booking.client_identity.client_phone_1,
                "client_phone_2": booking.client_identity.client_phone_2,

            }
        })

    return paginator.get_paginated_response(result)
