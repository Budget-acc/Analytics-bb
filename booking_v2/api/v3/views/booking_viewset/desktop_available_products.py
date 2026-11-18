from datetime import datetime, timedelta, time
import json
from booking_v2.models import (
    BookingV2,
    BookedProductVariant,
    ProductVariantAvailability,
)
from services.models import Product, ProductVariant, ShopOwnerService
from django.db.models import Q, Sum, Prefetch, F, Case, When, Value, IntegerField
from django.db import models
from rest_framework.decorators import action
from rest_framework import status
from django.utils.dateparse import parse_date
from django.utils import timezone
from shop.api.admin_v1.utils.shop import get_active_shop_id
from booking_v2.api.v3.views.pagination import ProductCursorPagination
from booking_v2.api.v3.serializers.product import ProductVariantSerializer
from ERP.utils.custom_pagination import StandardPageNumberPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="Get desktop available products",
    description=(
        "Returns desktop-optimized product/variant availability across one or multiple services.\n\n"
        "Purpose: High-volume product picking for desktop booking workflows.\n\n"
        "Context: Supports variant-specific fetch, search, price filters, and booking-aware stock adjustment."
    ),
    parameters=[
        OpenApiParameter(name="service_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Restricts to one service. If omitted, all active shop services are used. Location: query parameter. Format/Type: integer. Optional."),
        OpenApiParameter(name="event_date", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=True, description="Purpose: Availability window start date. Location: query parameter. Format/Type: YYYY-MM-DD. Required."),
        OpenApiParameter(name="return_date", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=True, description="Purpose: Availability window end date. Location: query parameter. Format/Type: YYYY-MM-DD. Required."),
        OpenApiParameter(name="event_time", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Optional start time. Location: query parameter. Format/Type: HH:MM. Optional."),
        OpenApiParameter(name="return_time", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Optional return time. Location: query parameter. Format/Type: HH:MM. Optional."),
        OpenApiParameter(name="variant_ids", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Fetches specific variants only. Location: query parameter. Format/Type: JSON array or comma-separated integer IDs. Optional."),
        OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Excludes this booking's own holds when editing. Location: query parameter. Format/Type: integer. Optional."),
        OpenApiParameter(name="search_field", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Search field key. Location: query parameter. Format/Type: string. Optional."),
        OpenApiParameter(name="search_value", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Purpose: Search term for chosen field. Location: query parameter. Format/Type: string. Optional."),
        OpenApiParameter(name="min_price", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Lower price bound. Location: query parameter. Format/Type: non-negative integer. Optional. Default: 0."),
        OpenApiParameter(name="max_price", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Upper price bound. Location: query parameter. Format/Type: non-negative integer >= min_price. Optional."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated success response with products payload or no-service success message."),
        400: OpenApiResponse(
            description=(
                "Validation/business errors exact messages include: Invalid variant_ids format. All IDs must be integers.; "
                "variant_ids parameter cannot be empty.; Invalid variant_ids format. Expected array of integers.; Missing required parameters.; "
                "Invalid date format.; Return date must be greater than or equal to event date.; Invalid time format.; Invalid service_id.; "
                "Invalid search field.; Invalid min_price value.; Invalid max_price value.; Invalid booking provided."
            )
        ),
        403: OpenApiResponse(
            description=(
                "Permission/business errors exact messages: Unable to identify shop.; "
                "Service not found or you do not have access to this service."
            )
        ),
    },
)
@action(detail=False, methods=["get"], url_path="desktop-available-products")
def desktop_available_products(self, request):
    """
    Get available products for booking.
    """

    # === STEP 1: Extract and validate parameters ===
    service_id = request.GET.get("service_id")
    event_date_str = request.GET.get("event_date")
    return_date_str = request.GET.get("return_date")

    # Optional booking_id parameter for editing existing bookings
    booking_id = request.GET.get("booking_id")

    # Sanitize search inputs
    search_field = request.GET.get("search_field", "").strip().lower()
    search_value = request.GET.get("search_value", "").strip()

    variant_ids_param = request.GET.get("variant_ids")
    variant_ids = []

    if variant_ids_param:
        try:
            # First try to parse as JSON
            variant_ids = json.loads(variant_ids_param)

            # Ensure it's a list
            if not isinstance(variant_ids, list):
                variant_ids = [variant_ids]

            # Validate all items are integers
            validated_ids = []
            for v in variant_ids:
                try:
                    validated_ids.append(int(v))
                except (ValueError, TypeError):
                    return self.validation_error_response(
                        errors={"variant_ids": [f"Invalid variant ID value: {v}. All IDs must be integers."]},
                        message="Invalid variant_ids format. All IDs must be integers."
                    )

            variant_ids = validated_ids

            # Optional: Check if list is not empty
            if not variant_ids:
                return self.validation_error_response(
                    errors={"variant_ids": ["variant_ids array cannot be empty."]},
                    message="variant_ids parameter cannot be empty."
                )

        except json.JSONDecodeError:
            # If JSON parsing fails, try comma-separated format
            try:
                variant_ids = [int(v.strip()) for v in variant_ids_param.split(',') if v.strip()]

                if not variant_ids:
                    return self.validation_error_response(
                        errors={"variant_ids": ["variant_ids cannot be empty."]},
                        message="variant_ids parameter cannot be empty."
                    )

            except (ValueError, TypeError):
                return self.validation_error_response(
                    errors={"variant_ids": ["Invalid variant_ids format. Use JSON array or comma-separated integers."]},
                    message="Invalid variant_ids format. Expected array of integers."
                )

    # Validate required date parameters
    if not all([event_date_str, return_date_str]):
        return self.validation_error_response(
            errors={"params": ["Missing required parameters: event_date, return_date"]},
            message="Missing required parameters."
        )

    # Parse and validate dates
    try:
        event_date = parse_date(event_date_str)
        return_date = parse_date(return_date_str)

        if not event_date or not return_date:
            raise ValueError("Invalid date format")

    except (ValueError, TypeError):
        return self.validation_error_response(
            errors={"date": ["Invalid date format. Use YYYY-MM-DD."]},
            message="Invalid date format."
        )

    if return_date < event_date:
        return self.validation_error_response(
            errors={"date": ["Return date must be greater than or equal to event date."]},
            message="Return date must be greater than or equal to event date."
        )

    # === STEP 2: Handle timestamp parameters ===
    event_time_str = request.GET.get("event_time")
    return_time_str = request.GET.get("return_time")
    event_datetime = None
    return_datetime = None

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

        except (ValueError, TypeError):
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

        except (ValueError, TypeError):
            return self.validation_error_response(
                errors={"time": ["Invalid time format. Use HH:MM."]},
                message="Invalid time format."
            )

    # === STEP 3: Get shop and validate service access ===
    try:
        shop_id = get_active_shop_id(request)
    except (AttributeError, ValueError, KeyError) as e:
        logger.error(f"Failed to get shop_id: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to identify shop.",
            http_status=status.HTTP_403_FORBIDDEN
        )

    # Initialize service IDs list
    service_ids = []

    service_obj = None

    if service_id:
        # Single service mode - validate service belongs to shop
        try:
            service_obj = ShopOwnerService.objects.select_related(
                "general_service__main_category"
            ).get(
                id=service_id,
                shop_id=shop_id,
                is_active=True,
                deleted__isnull=True
            )

            service_ids = [service_id]

        except ShopOwnerService.DoesNotExist:
            return self.error_response(
                message="Service not found or you do not have access to this service.",
                http_status=status.HTTP_403_FORBIDDEN
            )
        except ValueError:
            return self.validation_error_response(
                errors={"service_id": ["Invalid service_id format."]},
                message="Invalid service_id."
            )
    else:
        # All services mode - get all active services for the shop
        shop_services = ShopOwnerService.objects.filter(
            shop_id=shop_id,
            is_active=True,
            deleted__isnull=True
        ).values_list('id', flat=True)

        service_ids = list(shop_services)

        if not service_ids:
            return self.success_response(
                data={
                    "products": [],
                    "next": None,
                    "previous": None
                },
                message="No active services found for this shop."
            )

    if variant_ids:
        # Fetch variants with product details
        variants_qs = ProductVariant.objects.filter(
            id__in=variant_ids,
            is_active=True,
            deleted__isnull=True,
            product__deleted__isnull=True,
            product__is_active=True,
            product__shop_service__shop_id=shop_id
        ).select_related(
            "product",
            "product__shop_service",
            "product__shop_service__general_service",
            "product__shop_service__general_service__main_category",
        )

        # Apply the same unified search behavior for variant_ids flow.
        if search_field and search_value:
            standard_fields = ["name", "color", "category", "model"]
            if search_field not in standard_fields:
                return self.validation_error_response(
                    errors={"search_field": [f"Invalid search_field. Allowed: {', '.join(standard_fields)}"]},
                    message="Invalid search field."
                )

            variant_name_q = Q(attribute__icontains=search_value)

            if search_field == "name":
                if service_obj is not None:
                    general_service_name = (
                        (service_obj.general_service.name or "").lower()
                        if service_obj.general_service else ""
                    )
                    main_category_name = (
                        (service_obj.general_service.main_category.name or "").lower()
                        if service_obj.general_service and service_obj.general_service.main_category else ""
                    )
                    is_gadget_service = (
                        "gadget" in main_category_name
                        or "gadget" in general_service_name
                    )

                    if is_gadget_service:
                        variants_qs = variants_qs.filter(variant_name_q)
                    else:
                        variants_qs = variants_qs.filter(
                            Q(product__name__icontains=search_value) | variant_name_q
                        )
                else:
                    gadget_service_q = (
                        Q(product__shop_service__general_service__name__icontains="gadget")
                        | Q(product__shop_service__general_service__main_category__name__icontains="gadget")
                    )
                    variants_qs = variants_qs.filter(
                        (gadget_service_q & variant_name_q)
                        |
                        (~gadget_service_q & (Q(product__name__icontains=search_value) | variant_name_q))
                    )
            else:
                variants_qs = variants_qs.filter(
                    Q(**{f"product__{search_field}__icontains": search_value}) | variant_name_q
                )

        # Get availability map
        availability_map = self.get_variants_availability(
            variant_ids=variant_ids,
            event_date=event_date,
            return_date=return_date,
            event_datetime=event_datetime,
            return_datetime=return_datetime,
            booking_id=booking_id,
        )

        # Group variants by product - include ALL requested variants
        product_variant_map = {}
        variants_found = set()

        # First, add all variants that exist in the database
        for variant in variants_qs:
            variants_found.add(variant.id)
            availability = availability_map.get(variant.id, {
                "variant_id": variant.id,
                "stock": variant.stock,
                "remaining_stock": 0,
            })

            product = variant.product

            if product.id not in product_variant_map:
                general_service = product.shop_service.general_service
                main_category = (
                    general_service.main_category
                    if general_service else None
                )

                product_variant_map[product.id] = {
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
                    "variants": [],
                    "main_service_name": main_category.name if main_category else None,
                }

            product_variant_map[product.id]["variants"].append({
                "id": variant.id,
                "attribute": variant.attribute,
                "price": variant.price,
                "stock": variant.stock,
                "remaining_stock": availability["remaining_stock"],
            })

        # Add any variant IDs that were requested but not found
        not_found_ids = set(variant_ids) - variants_found
        if not_found_ids:
            logger.warning(f"Requested variant IDs not found or inactive: {not_found_ids}")
            for vid in not_found_ids:
                availability = availability_map.get(vid, {
                    "variant_id": vid,
                    "stock": 0,
                    "remaining_stock": 0,
                })
                pass

        product_list = list(product_variant_map.values())

        paginator = StandardPageNumberPagination()
        paginator.request = request
        paginated_products = paginator.paginate_queryset(product_list, request)

        return paginator.get_paginated_response({
            "products": paginated_products,
            "meta": {
                "requested_ids": variant_ids,
                "found_ids": list(variants_found),
                "not_found_ids": list(not_found_ids) if not_found_ids else []
            }
        })


    # === STEP 4: Build product queryset ===
    variant_qs = ProductVariant.objects.filter(
        is_active=True,
        stock__gt=0,
        deleted__isnull=True
    ).select_related("product")

    product_qs = Product.objects.filter(
        shop_service_id__in=service_ids,
        shop_service__shop_id=shop_id,
        is_active=True,
        deleted__isnull=True
    ).prefetch_related(
        Prefetch("variants", queryset=variant_qs)
    ).select_related(
        "shop_service",
        "shop_service__general_service",
        "shop_service__general_service__main_category",
    )

    # === STEP 5: Apply search filters ===
    if search_field and search_value:
        product_qs, search_error = self._apply_product_variant_search(
            product_qs=product_qs,
            search_field=search_field,
            search_value=search_value,
            service_obj=service_obj,
        )
        if search_error:
            return search_error

    # === STEP 6: Apply price filters ===
    try:
        min_price = int(request.GET.get("min_price", 0))
        if min_price < 0:
            raise ValueError("min_price cannot be negative")
    except (ValueError, TypeError):
        return self.validation_error_response(
            errors={"min_price": ["Invalid min_price value. Must be a non-negative integer."]},
            message="Invalid min_price value."
        )

    max_price_raw = request.GET.get("max_price")
    if max_price_raw:
        try:
            max_price = int(max_price_raw)
            if max_price < 0:
                raise ValueError("max_price cannot be negative")
            if max_price < min_price:
                raise ValueError("max_price cannot be less than min_price")
        except (ValueError, TypeError) as e:
            return self.validation_error_response(
                errors={"max_price": [f"Invalid max_price value: {str(e)}"]},
                message="Invalid max_price value."
            )

        product_qs = product_qs.filter(
            Q(price__isnull=True) | Q(price__gte=min_price, price__lte=max_price)
        )
    else:
        product_qs = product_qs.filter(
            Q(price__isnull=True) | Q(price__gte=min_price)
        )

    # === STEP 7: Apply search-priority ordering + pagination ===
    paginator = ProductCursorPagination()

    if search_field and search_value:
        # Annotate each product with a search relevance score (lower = higher relevance):
        #   0 → exact match, 1 → starts with, 2 → contains
        # Works for any standard field (name, color, category, model).
        exact_q      = Q(**{f"{search_field}__iexact": search_value})
        startswith_q = Q(**{f"{search_field}__istartswith": search_value})
        product_qs = product_qs.annotate(
            search_priority=Case(
                When(exact_q,      then=Value(0)),
                When(startswith_q, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        paginator.ordering = ("search_priority", "id")
        paginated_products = paginator.paginate_queryset(
            product_qs.order_by("search_priority", "id"),
            request,
        )
    else:
        paginated_products = paginator.paginate_queryset(
            product_qs.order_by("id"),
            request,
        )

    # Get the booking being edited if booking_id is provided
    editing_booking = None
    editing_booked_variants = {}
    if booking_id:
        try:
            editing_booking = BookingV2.objects.get(
                id=booking_id,
                shop_id=shop_id
            )
            if not editing_booking:
                return self.validation_error_response(
                    errors={"booking_id": [f"Booking with ID {booking_id} not found for this shop."]},
                    message="Invalid booking provided."
                )
            # Get all booked variants for this booking with their quantities
            booked_items = BookedProductVariant.objects.filter(
                booking_id=booking_id,
                deleted__isnull=True
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
        except Exception as e:
            logger.error(f"Error fetching booking {booking_id}: {str(e)}", exc_info=True)

    # === STEP 8: Bulk fetch availability data to prevent N+1 queries ===
    # Collect all variant IDs from paginated products
    all_variant_ids = []
    for product in paginated_products:
        for variant in product.variants.all():
            all_variant_ids.append(variant.id)

    # Bulk fetch overlapping bookings for timestamp-based check
    overlap_map = {}
    if event_datetime and return_datetime and all_variant_ids:
        # Base query for overlapping bookings
        overlapping_qs = BookedProductVariant.objects.filter(
            product_variant_id__in=all_variant_ids,
            booking__deleted__isnull=True,
            booking__booking_status="upcoming",
            deleted__isnull=True
        )

        # EXCLUDE the current booking if we're editing it
        if editing_booking:
            overlapping_qs = overlapping_qs.exclude(
                booking_id=editing_booking.id
            )

        overlapping_qs = overlapping_qs.annotate(
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
        ).values("product_variant_id").annotate(total=Sum("quantity"))

        overlap_map = {
            row["product_variant_id"]: row["total"]
            for row in overlapping_qs
        }

    # Bulk fetch availability data for date-based check
    availability_map = {}
    date_wise_booked_map = {}

    if not (event_datetime and return_datetime) and all_variant_ids:
        # Generate date range
        date_range = [
            event_date + timedelta(days=i)
            for i in range((return_date - event_date).days + 1)
        ]

        # Get all availability records for these variants in the date range
        availabilities = ProductVariantAvailability.objects.filter(
            variant_id__in=all_variant_ids,
            date__in=date_range
        ).values('variant_id', 'date', 'booked_quantity')

        # Group by variant_id
        for item in availabilities:
            variant_id = item['variant_id']
            if variant_id not in availability_map:
                availability_map[variant_id] = {}
            availability_map[variant_id][item['date']] = item['booked_quantity']

        # If we're editing a booking, we need to adjust the availability map
        if editing_booking and editing_booked_variants:
            # Get original booking dates including cooling period
            original_start = editing_booking.pickup_date.date()
            original_end = editing_booking.return_date.date() if editing_booking.return_date else original_start

            # Adjust for cooling period if present
            if editing_booking.cooling_period_end:
                cooling_date = editing_booking.cooling_period_end.date()
                if cooling_date < original_start:
                    original_start = cooling_date
                elif cooling_date > original_end:
                    original_end = cooling_date

            # Generate original date range
            original_dates = [
                original_start + timedelta(days=i)
                for i in range((original_end - original_start).days + 1)
            ]

            # For each variant in the editing booking, adjust booked quantities
            for variant_id, info in editing_booked_variants.items():
                if variant_id in availability_map:
                    # For each date in the requested range that overlaps with original dates
                    for date in date_range:
                        if date in original_dates:
                            current_booked = availability_map[variant_id].get(date, 0)
                            # Subtract this booking's quantity (ensure non-negative)
                            availability_map[variant_id][date] = max(0, current_booked - info['quantity'])
                else:
                    # No existing availability records for this variant
                    # But if dates overlap with original booking, we need to create entries with 0
                    # (meaning the variant is available on these dates since we're excluding our own booking)
                    for date in date_range:
                        if date in original_dates:
                            if variant_id not in availability_map:
                                availability_map[variant_id] = {}
                            availability_map[variant_id][date] = 0

    # === STEP 9: Check availability and build response ===
    final_products = []

    for product in paginated_products:
        available_variants = []

        for variant in product.variants.all():
            # Check if this variant is in the booking being edited
            is_editing_variant = variant.id in editing_booked_variants

            if event_datetime and return_datetime:
                # Timestamp-based availability
                total_booked = overlap_map.get(variant.id, 0)

                remaining_stock = variant.stock - total_booked
                is_available = remaining_stock > 0

                if is_available:
                    variant_data = ProductVariantSerializer(variant).data
                    variant_data["remaining_stock"] = remaining_stock
                    available_variants.append(variant_data)

            else:
                # Date-based availability
                # Get adjusted booked map for this variant
                variant_booked_map = availability_map.get(variant.id, {})

                # Generate date range for this specific check
                date_range = [
                    event_date + timedelta(days=i)
                    for i in range((return_date - event_date).days + 1)
                ]

                # Calculate remaining stock per day using adjusted booked quantities
                remaining_per_day = []
                is_fully_available = True

                for date in date_range:
                    booked_qty = variant_booked_map.get(date, 0)
                    remaining = variant.stock - booked_qty
                    remaining_per_day.append(remaining)

                    if remaining <= 0:
                        is_fully_available = False

                # Minimum available across the period
                remaining_stock = min(remaining_per_day) if remaining_per_day else variant.stock

                if is_fully_available and remaining_stock > 0:
                    variant_data = ProductVariantSerializer(variant).data
                    variant_data["remaining_stock"] = remaining_stock
                    available_variants.append(variant_data)

        # Only include products with available variants
        if available_variants:
            general_service = product.shop_service.general_service
            main_category = (
                general_service.main_category
                if general_service else None
            )
            product_data = {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "sale_price": product.sale_price,
                "color": product.color,
                "category": product.category,
                "model": product.model,
                "image": product.image,
                "thumbnail_image": product.thumbnail_image,
                "variants": available_variants,
                "main_service_name": main_category.name if main_category else None
            }
            final_products.append(product_data)

    return paginator.get_paginated_response({
        "products": final_products
    })
