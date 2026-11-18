import logging
from rest_framework import status
from rest_framework.decorators import action
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product
from shop.models import ShopUser
from booking_v2.models import BookingV2
from ERP.utils.custom_pagination import StandardPageNumberPagination

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="List bookings for a product",
    description="Returns paginated bookings across all active variants of a product.",
    parameters=[
        OpenApiParameter("product_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Product id."),
        OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Booking status filter: all, upcoming, completed. Default all."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated booking list for the product."),
        403: OpenApiResponse(
            description=(
                "Permission messages:\n"
                "- You do not have permission to access this product.\n"
                "- Product or related entities are inactive.\n"
                "- You do not have access to this shop."
            )
        ),
        404: OpenApiResponse(description="Exact message: The product does not exist or is inactive."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching bookings. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"booking_id": 21, "booking_status": "completed", "booked_items": ["Blue Shirt"]}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found", value={"status": "not_found", "message": "The product does not exist or is inactive.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)
@action(detail=False, methods=["get"], url_path=r"product-bookings/(?P<product_id>\d+)")
def product_bookings(self, request, product_id=None):
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
        product = Product.objects.select_related("shop_service__shop").get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return self.not_found_response(
            resource="Product",
            message="The product does not exist or is inactive.",
            dev_message=f"Invalid or inactive product_id: {product_id}"
        )

    shop_service = product.shop_service
    shop = shop_service.shop

    if not shop_service.is_active:
        return self.error_response(
            message="Product or related entities are inactive.",
            dev_message=f"Inactive shop_service ({shop_service.id}) for product {product_id}",
            http_status=status.HTTP_403_FORBIDDEN
        )

    is_user_linked = ShopUser.objects.filter(shop=shop, user=user).exists()
    if not is_user_linked:
        return self.permission_denied_response(
            message="You do not have access to this shop."
        )

    try:
        # Find bookings from all product variants
        variant_ids = product.variants.filter(is_active=True).values_list("id", flat=True)
        bookings_qs = BookingV2.objects.filter(
            booked_items__product_variant_id__in=variant_ids,
            booked_items__deleted__isnull=True,
            shop=shop
        ).select_related("client_identity").prefetch_related("booked_items").distinct()

        if booking_status == "upcoming":
            bookings_qs = bookings_qs.filter(booking_status="upcoming").order_by("pickup_date")
        elif booking_status == "completed":
            bookings_qs = bookings_qs.filter(booking_status="completed").order_by("-pickup_date")

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(bookings_qs, request)

        result = []
        for booking in page:
            result.append({
                "booking_id": booking.id,
                "pickup_date": localtime(booking.pickup_date).strftime("%d-%m-%Y"),
                "return_date": localtime(booking.return_date).strftime("%d-%m-%Y") if booking.return_date else booking.pickup_date.strftime("%d-%m-%Y"),
                "payment_status": booking.payment_status,
                "delivery_status": booking.delivery_status,
                "booking_status": booking.booking_status,
                "client_name": booking.client_identity.client_name if booking.client_identity else booking.client.client_name,
                "created_at": localtime(booking.created_at).strftime("%d-%m-%Y %H:%M:%S"),
                "booked_items":[item.product_name for item in booking.booked_items.all()],
            })

        return paginator.get_paginated_response(result)

    except Exception as e:
        logger.error(f"Error fetching bookings for product {product_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching bookings. Please try again.",
            dev_message=str(e)
        )
