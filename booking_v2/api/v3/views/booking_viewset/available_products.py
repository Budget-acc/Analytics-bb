from datetime import datetime, timedelta, time
from booking_v2.models import (
    BookingV2,
    BookedProductVariant,
    ProductVariantAvailability,
)
from services.models import Product, ProductVariant, ShopOwnerService, ServiceAttributeTemplate, ServiceAttributeValue
from django.db.models import Q, Sum, Prefetch, F, Value, Exists, OuterRef, Case, When
from django.db import models
from rest_framework.decorators import action
from rest_framework import status
from django.utils.dateparse import parse_date
from django.utils import timezone
from shop.api.admin_v1.utils.shop import get_active_shop_id
from booking_v2.api.v3.views.pagination import ProductCursorPagination
from booking_v2.api.v3.serializers.product import ProductVariantSerializer
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="Get available products",
    description=(
        "Returns service products with variants currently available in the requested date/time window.\n\n"
        "Purpose: Product selection API for booking creation/update with stock-safe filtering.\n\n"
        "Context: Supports optional search, price filter, and booking-aware exclusion during edit."
    ),
    parameters=[
        OpenApiParameter(name="service_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=True, description="Purpose: Selects shop service scope. Location: query parameter. Format/Type: integer. Required."),
        OpenApiParameter(name="event_date", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=True, description="Purpose: Booking start date for availability check. Location: query parameter. Format/Type: YYYY-MM-DD. Required."),
        OpenApiParameter(name="return_date", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=True, description="Purpose: Booking end date for availability check. Location: query parameter. Format/Type: YYYY-MM-DD. Required."),
        OpenApiParameter(name="event_time", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Optional event start time. Location: query parameter. Format/Type: HH:MM. Optional."),
        OpenApiParameter(name="return_time", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Optional event return time. Location: query parameter. Format/Type: HH:MM. Optional."),
        OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Excludes current booking quantities during edit. Location: query parameter. Format/Type: integer. Optional."),
        OpenApiParameter(name="search_field", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Field key for search filtering. Location: query parameter. Format/Type: string. Optional."),
        OpenApiParameter(name="search_value", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Search term for selected search_field. Location: query parameter. Format/Type: string. Optional."),
        OpenApiParameter(name="min_price", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Minimum product price filter. Location: query parameter. Format/Type: integer. Optional. Default: 0."),
        OpenApiParameter(name="max_price", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Maximum product price filter. Location: query parameter. Format/Type: integer. Optional."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated success response with main_service_name and available products list."),
        400: OpenApiResponse(
            description=(
                "Validation errors exact messages include: Missing required parameters.; Invalid time format.; Invalid date format.; "
                "Return date must be greater than or equal to pickup date.; Invalid search field.; "
                "Invalid search value for numeric attribute.; Invalid min_price value.; Invalid max_price value.; "
                "An error occurred during attribute search.; An error occurred during search."
            )
        ),
        403: OpenApiResponse(description="Permission/business error exact message: You do not have access to this service."),
    },
)
@action(detail=False, methods=["get"], url_path="available-products")
def available_products(self, request):
    service_id = request.GET.get("service_id")
    event_date_str = request.GET.get("event_date")
    return_date_str = request.GET.get("return_date")

    # Optional booking_id parameter for editing existing bookings
    booking_id = request.GET.get("booking_id")  # Optional

    search_field = request.GET.get("search_field", "").strip().lower()
    search_value = request.GET.get("search_value", "").strip().lower()

    if not all([service_id, event_date_str, return_date_str]):
        return self.validation_error_response(
            errors={"params": ["Missing required parameters: service_id, event_date, return_date"]},
            message="Missing required parameters."
        )

    event_date = parse_date(event_date_str)
    return_date = parse_date(return_date_str)

    # Includes Timestamp ----------
    event_time_str = request.GET.get("event_time")
    return_time_str = request.GET.get("return_time")

    if event_time_str:
        try:
            event_time = datetime.strptime(event_time_str, "%H:%M").time()

            # Default return_time to 23:59 if not provided
            if return_time_str:
                return_time = datetime.strptime(return_time_str, "%H:%M").time()
            else:
                return_time = time(23, 59)

            event_datetime = datetime.combine(event_date, event_time)
            return_datetime = datetime.combine(return_date, return_time)

            # Keep queryset comparisons timezone-safe when USE_TZ=True.
            if timezone.is_naive(event_datetime):
                event_datetime = timezone.make_aware(
                    event_datetime,
                    timezone.get_current_timezone(),
                )
            if timezone.is_naive(return_datetime):
                return_datetime = timezone.make_aware(
                    return_datetime,
                    timezone.get_current_timezone(),
                )

        except ValueError:
            return self.validation_error_response(
                errors={"time": ["Invalid time format. Use HH:MM."]},
                message="Invalid time format."
            )

    elif return_time_str:
        try:
            return_time = datetime.strptime(return_time_str, "%H:%M").time()
            event_time = time(0, 1)  # Default event_time to 00:01

            event_datetime = datetime.combine(event_date, event_time)
            return_datetime = datetime.combine(return_date, return_time)

            if timezone.is_naive(event_datetime):
                event_datetime = timezone.make_aware(
                    event_datetime,
                    timezone.get_current_timezone(),
                )
            if timezone.is_naive(return_datetime):
                return_datetime = timezone.make_aware(
                    return_datetime,
                    timezone.get_current_timezone(),
                )

        except ValueError:
            return self.validation_error_response(
                errors={"time": ["Invalid time format. Use HH:MM."]},
                message="Invalid time format."
            )
    else:
        event_datetime = None
        return_datetime = None
    # ----------------------

    if not event_date or not return_date:
        return self.validation_error_response(
            errors={"date": ["Invalid date format. Use YYYY-MM-DD."]},
            message="Invalid date format."
        )

    if return_date < event_date:
        return self.validation_error_response(
            errors={"date": ["Return date must be greater than or equal to event date."]},
            message="Return date must be greater than or equal to pickup date."
        )

    # Step 1: Get user's shops
    try:
        shop_id = get_active_shop_id(request)

        # Verify the service belongs to the shop
        service_obj = ShopOwnerService.objects.select_related(
            "general_service__main_category"
        ).get(id=service_id, shop_id=shop_id)

        main_service_name = (
            service_obj.general_service.main_category.name
            if service_obj.general_service and service_obj.general_service.main_category
            else None
        )

    except ShopOwnerService.DoesNotExist:
        return self.error_response(
            message="You do not have access to this service.",
            http_status=status.HTTP_403_FORBIDDEN
        )

    # Step 3: Prepare product queryset
    variant_qs = ProductVariant.objects.filter(
        is_active=True,
        stock__gt=0
    ).select_related("product")

    product_qs = Product.objects.filter(
        shop_service_id=service_id,
        shop_service__shop_id=shop_id,
        is_active=True,
    ).prefetch_related(
        Prefetch("variants", queryset=variant_qs),
        Prefetch(
            "attribute_values",
            queryset=ServiceAttributeValue.objects.select_related("attribute"),
            to_attr="prefetched_attributes"
        )
    ).select_related("shop_service", "shop_service__shop")

    if search_field and search_value:
        try:
            # Validate and sanitize search_field
            standard_fields = ["name", "color", "category", "model"]

            if search_field in standard_fields:
                product_qs, search_error = self._apply_product_variant_search(
                    product_qs=product_qs,
                    search_field=search_field,
                    search_value=search_value,
                    service_obj=service_obj,
                )
                if search_error:
                    return search_error

            else:
                # Step 1: Validate attribute key exists and belongs to this service
                attr_template = ServiceAttributeTemplate.objects.filter(
                    general_service_id=service_obj.general_service_id,
                    attribute_key=search_field
                ).first()

                if not attr_template:
                    # Invalid or non-existent attribute key
                    return self.validation_error_response(
                        errors={"search_field": [
                            f"Invalid search_field '{search_field}'. Must be one of: {', '.join(standard_fields)} "
                            "or a valid attribute key for this service."
                        ]},
                        message="Invalid search field."
                    )

                # Step 2: Check attribute type for proper query construction
                attribute_type = attr_template.attribute_type

                # Step 3: Build attribute search query
                try:
                    if attribute_type in ['text', 'choice']:
                        attr_subquery = ServiceAttributeValue.objects.filter(
                            product_id=OuterRef('pk'),
                            attribute_id=attr_template.id,
                            value__icontains=search_value
                        )

                    elif attribute_type in ['number', 'decimal']:
                        try:
                            search_num = float(search_value)
                            attr_subquery = ServiceAttributeValue.objects.filter(
                                product_id=OuterRef('pk'),
                                attribute_id=attr_template.id,
                                value=search_num
                            )
                        except (ValueError, TypeError):
                            logger.warning(
                                f"Non-numeric search value '{search_value}' for numeric attribute '{search_field}'"
                            )
                            return self.validation_error_response(
                                errors={"search_value": [f"Value must be numeric for attribute '{search_field}'"]},
                                message="Invalid search value for numeric attribute."
                            )

                    elif attribute_type == 'date':
                        # Date search
                        try:
                            search_date = parse_date(search_value)
                            if not search_date:
                                raise ValueError("Invalid date format")
                            attr_subquery = ServiceAttributeValue.objects.filter(
                                product_id=OuterRef('pk'),
                                attribute_id=attr_template.id,
                                value=search_date.isoformat()
                            )
                        except (ValueError, TypeError):
                            return self.validation_error_response(
                                errors={"search_value": ["Date format must be YYYY-MM-DD"]},
                                message="Invalid date format."
                            )

                    elif attribute_type == 'boolean':
                        # Boolean search
                        bool_val = search_value.lower() in ['true', '1', 'yes', 'on']
                        attr_subquery = ServiceAttributeValue.objects.filter(
                            product_id=OuterRef('pk'),
                            attribute_id=attr_template.id,
                            value=bool_val
                        )

                    else:
                        # Unknown type - treat as text
                        attr_subquery = ServiceAttributeValue.objects.filter(
                            product_id=OuterRef('pk'),
                            attribute_id=attr_template.id,
                            value__icontains=search_value
                        )

                    # Apply the attribute filter using Exists (single query, optimized)
                    product_qs = product_qs.annotate(
                        has_matching_attr=Exists(attr_subquery)
                    ).filter(has_matching_attr=True)

                except Exception as e:
                    logger.error(
                        f"Attribute search error for field='{search_field}', value='{search_value}': {str(e)}",
                        exc_info=True
                    )
                    return self.error_response(
                        message="An error occurred during attribute search.",
                        http_status=status.HTTP_400_BAD_REQUEST
                    )

        except Exception as e:
            logger.error(
                f"Search logic error: {str(e)}",
                exc_info=True
            )
            return self.error_response(
                message="An error occurred during search.",
                http_status=status.HTTP_400_BAD_REQUEST
            )

    try:
        min_price = int(request.GET.get("min_price", 0))
    except ValueError:
        return self.validation_error_response(
            errors={"min_price": ["Invalid min_price value."]},
            message="Invalid min_price value."
        )

    max_price_raw = request.GET.get("max_price")
    if max_price_raw:
        try:
            max_price = int(max_price_raw)
        except ValueError:
            return self.validation_error_response(
                errors={"max_price": ["Invalid max_price value."]},
                message="Invalid max_price value."
            )

        product_qs = product_qs.filter(
            Q(price__isnull=True) | Q(price__gte=min_price, price__lte=max_price)
        )
    else:
        product_qs = product_qs.filter(
            Q(price__isnull=True) | Q(price__gte=min_price)
        )

    # Step 4: Apply cursor pagination
    paginator = ProductCursorPagination()
    paginated_products = paginator.paginate_queryset(product_qs.order_by("id"), request)

    date_range = [
        event_date + timedelta(days=i)
        for i in range((return_date - event_date).days + 1)
    ]

    # Get the booking being edited if booking_id is provided
    editing_booking = None
    editing_booked_variants = {}
    if booking_id:
        try:
            editing_booking = BookingV2.objects.get(
                id=booking_id,
                shop_id=shop_id,
                deleted__isnull=True
            )
            # Get all booked variants for this booking with their quantities
            booked_items = BookedProductVariant.objects.filter(
                booking_id=booking_id
            ).select_related('product_variant')

            for item in booked_items:
                if item.product_variant_id:
                    editing_booked_variants[item.product_variant_id] = {
                        'quantity': item.quantity or 1,
                        'booking': editing_booking
                    }
        except BookingV2.DoesNotExist:
            # Invalid booking_id - just log and continue (treat as new booking)
            logger.warning(f"Invalid booking_id provided: {booking_id} for shop {shop_id}")

    final_products = []
    for product in paginated_products:
        available_variants = []
        product_attributes = {}

        for attribute_value in product.attribute_values.all():
            if attribute_value.attribute and attribute_value.attribute.attribute_key:
                product_attributes[attribute_value.attribute.attribute_key] = attribute_value.value

        for variant in product.variants.all():
            # Check if this variant is in the booking being edited
            is_editing_variant = variant.id in editing_booked_variants
            editing_info = editing_booked_variants.get(variant.id)

            # Timestamp ------------------
            if event_datetime and return_datetime:
                # Timestamp-based check using BookedProductVariant

                # Base query for overlapping bookings
                overlapping_query = BookedProductVariant.objects.filter(
                    product_variant=variant,
                    booking__deleted__isnull=True,
                    booking__booking_status="upcoming",
                # Old: every returned or cancelled booking was skipped.
                # ).exclude(
                #     booking__delivery_status__in=["returned", "cancelled"]
                # )
                ).exclude(
                    booking__delivery_status="returned",
                ).exclude(
                    # Cancelled bookings still hold stock until the booking is completed.
                    booking__delivery_status="cancelled",
                    booking__booking_status="completed",
                )

                # EXCLUDE the current booking if we're editing it
                if editing_booking:
                    overlapping_query = overlapping_query.exclude(
                        booking_id=editing_booking.id
                    )

                overlapping_bookings = overlapping_query.annotate(
                    effective_start=Case(
                        When(
                            booking__cooling_period_end__isnull=False,
                            booking__cooling_period_end__lt=F("booking__pickup_date"),
                            then=F("booking__cooling_period_end"),
                        ),
                        default=F("booking__pickup_date"),
                        output_field=models.DateTimeField(),
                    ),
                    effective_end=Case(
                        When(
                            booking__cooling_period_end__isnull=False,
                            booking__cooling_period_end__gt=F("booking__return_date"),
                            then=F("booking__cooling_period_end"),
                        ),
                        default=F("booking__return_date"),
                        output_field=models.DateTimeField(),
                    ),
                ).filter(
                    effective_start__lt=return_datetime,
                    effective_end__gt=event_datetime,
                )

                total_booked = overlapping_bookings.aggregate(total=Sum("quantity"))["total"] or 0

                # Calculate remaining stock
                # If this is the editing variant, we can use its full stock since we're excluding it
                remaining_stock = variant.stock - total_booked
                is_available = remaining_stock > 0

                if is_available:
                    variant_data = ProductVariantSerializer(variant).data
                    variant_data["remaining_stock"] = remaining_stock
                    available_variants.append(variant_data)

            else:
                # Date-based availability (existing logic with booking exclusion)

                # Get all availability records for this variant in the date range
                availabilities = ProductVariantAvailability.objects.filter(
                    variant=variant,
                    date__in=date_range
                )

                # Build booked map
                booked_map = {a.date: a.booked_quantity for a in availabilities}

                # If we're editing a booking, we need to adjust the booked quantities
                # to exclude this booking's quantities from the availability check
                if is_editing_variant and editing_info:
                    # Get the original dates of this booking to know where to subtract
                    original_booking = editing_info['booking']
                    original_quantity = editing_info['quantity']

                    # Calculate original date range including cooling period
                    original_start = original_booking.pickup_date.date()
                    original_end = original_booking.return_date.date() if original_booking.return_date else original_start

                    # Adjust for cooling period if present
                    if original_booking.cooling_period_end:
                        cooling_date = original_booking.cooling_period_end.date()
                        if cooling_date < original_start:
                            original_start = cooling_date
                        elif cooling_date > original_end:
                            original_end = cooling_date

                    # Generate all dates in the original range
                    original_dates = [
                        original_start + timedelta(days=i)
                        for i in range((original_end - original_start).days + 1)
                    ]

                    # For each date in the requested range, if it falls within the original booking's dates,
                    # subtract the original quantity from the booked map to "release" that stock
                    for date in date_range:
                        if date in original_dates:
                            current_booked = booked_map.get(date, 0)
                            # Ensure we don't go below zero
                            booked_map[date] = max(0, current_booked - original_quantity)

                # Calculate remaining stock per day
                remaining_per_day = [
                    variant.stock - booked_map.get(d, 0)
                    for d in date_range
                ]

                # Minimum available across the period
                remaining_stock = min(remaining_per_day) if remaining_per_day else variant.stock

                # Check if fully available across all days
                is_fully_available = all(
                    booked_map.get(d, 0) < variant.stock for d in date_range
                )

                if is_fully_available:
                    variant_data = ProductVariantSerializer(variant).data
                    variant_data["remaining_stock"] = remaining_stock
                    available_variants.append(variant_data)

        if available_variants:
            product_data = {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "hourly_rental_price": product.hourly_rental_price,
                "sale_price": product.sale_price,
                "color": product.color,
                "category": product.category,
                "model": product.model,
                "image": product.image,
                "thumbnail_image": product.thumbnail_image,
                "attributes": product_attributes,
                "variants": available_variants
            }
            final_products.append(product_data)

    return paginator.get_paginated_response({
        "main_service_name": main_service_name,
        "products": final_products
    })
