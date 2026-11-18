import logging
from booking_v2.models import BookingV2, BookedProductVariant, BookingAdditionalCharge, ClientRequirementV2
from booking_v2.utils.measurements import to_stored_measurements
from services.models import ProductVariant
from shop.models import Staff
from booking_v2.api.v5.utils.availability import add_product_variant_availability
from datetime import datetime, date

logger = logging.getLogger("api_requests")


def _validate_purchase_mode(self, request_data, default="normal"):
    """
    Validate purchase_mode field against allowed choices.
    Returns (mode, None) on success or (None, error_response) on failure.
    Derives valid values from BookingV2.PURCHASE_MODE_CHOICES — no hardcoded list.
    """
    valid_modes = [c[0] for c in BookingV2.PURCHASE_MODE_CHOICES]
    mode = request_data.get("purchase_mode", default)
    if mode not in valid_modes:
        return None, self.validation_error_response(
            errors={"purchase_mode": [f"Invalid value. Must be one of: {', '.join(valid_modes)}."]},
            message="Invalid purchase mode value."
        )
    return mode, None


def _validate_staff(self, request_data, shop):
    """
    Validate staff if staff_id is provided.
    Returns (staff, error_response)
    """
    staff_id = request_data.get("staff_id")
    if not staff_id:
        return None, None

    try:
        staff = Staff.objects.get(id=staff_id, shop=shop)
        return staff, None
    except Staff.DoesNotExist:
        return None, self.validation_error_response(
            errors={"staff_id": ["Staff not found for this shop."]},
            message="Staff not found."
        )
    except Exception as e:
        logger.error(f"Staff validation error: {str(e)}")
        return None, self.error_response(
            message="Failed to validate staff.",
            dev_message=str(e)
        )


def _create_booking_record(self, request, shop, client, dates_data, amounts_data, delivery_status, staff=None, rental_duration_type="daily", effective_dt=None, tax_result=None):
    """
    Create the main booking record.
    Returns (booking, error_response)
    """
    pickup_date, return_date, cooling_period_end, pickup_time, return_time, cooling_period_type = dates_data
    total_amount, discount, security, advance, additional_charges = amounts_data

    details = request.data.get("details")
    if details is not None and not isinstance(details, dict):
        return None, self.validation_error_response(
            errors={"details": ["Details must be a valid JSON object."]},
            message="Invalid details format."
        )

    try:
        booking_data = {
            'user': request.user,
            'shop': shop,
            'client_identity': client,
            'client_address': request.data.get("client_address"),
            'pickup_date': pickup_date,
            'return_date': return_date,
            'pickup_time': pickup_time,
            'return_time': return_time,
            'original_pickup_date': pickup_date,
            'original_return_date': return_date,
            'cooling_period_end': cooling_period_end,
            'total_amount': total_amount,
            'advance_amount': advance,
            'discount_amount': discount,
            'security_amount': security,
            'purchase_mode': request.data.get("purchase_mode", "normal"),
            # `total_amount` is always the raw, pre-discount payable figure
            # (both tax-enabled and no-tax paths - see
            # calculate_booking_tax_v2), so discount is always subtracted
            # here, uniformly.
            'payment_status': (advance >= (total_amount - discount)),
            'delivery_status': delivery_status,
            'details': details,
            'rental_duration_type': rental_duration_type,
            **({"created_at": effective_dt} if effective_dt else {}),
        }

        # Tax snapshot fields - only populated when the shop has an active
        # tax configuration; otherwise every tax field stays at its model
        # default (is_tax_applied=False, amounts=0), i.e. no behavior change.
        if tax_result:
            booking_data.update({
                'subtotal': tax_result['subtotal'],
                'taxable_amount': tax_result['taxable_amount'],
                'tax_amount': tax_result['tax_amount'],
                'tax_rate': tax_result['tax_rate'],
                'tax_name': tax_result['tax_name'],
                'tax_calculation_type': tax_result['tax_calculation_type'],
                'is_tax_applied': tax_result['is_tax_applied'],
                'taxable_components': tax_result['taxable_components'],
            })

        # Store cooling_period_type in details JSON field
        if cooling_period_end:
            if booking_data['details'] is None:
                booking_data['details'] = {}
            booking_data['details']['cooling_period_type'] = cooling_period_type

        # Only add staff to the booking data if it's not None
        if staff is not None:
            booking_data['staff'] = staff

        booking = BookingV2.objects.create(**booking_data)

        return booking, None
    except Exception as e:
        logger.error(f"Failed to create booking: {str(e)}")
        return None, self.error_response(
            message="Failed to create booking.",
            dev_message=str(e)
        )


def _create_additional_charges(self, booking, additional_charges, shop):
    """
    Create additional charge records for the booking.
    Returns error_response or None
    """
    if not additional_charges:
        return None

    try:
        charge_objects = [
            BookingAdditionalCharge(
                booking=booking,
                name=charge["name"],
                amount=charge["amount"],
            ) for charge in additional_charges
        ]
        BookingAdditionalCharge.objects.bulk_create(charge_objects)
        return None
    except Exception as e:
        logger.error(f"Failed to create additional charges for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to create additional charges.",
            dev_message=str(e)
        )


def _create_booked_items(self, booking, variants, shop, request):
    """
    Create booked product variant records using bulk_create
    """
    try:
        booked_items = []
        variant_ids = [item["id"] for item in variants]

        # Prefetch all product variants in one query
        product_variants = ProductVariant.objects.filter(
            id__in=variant_ids
        ).select_related('product').in_bulk()

        for item in variants:
            variant = product_variants.get(item["id"])
            if not variant:
                continue

            measurements = to_stored_measurements(item.get("measurements"))

            # Safe string handling with fallback for None values
            product_name = variant.product.name[:500] if variant.product and variant.product.name else ""

            # Handle variant attribute safely
            variant_attribute = ""
            if variant.attribute:
                variant_attribute = variant.attribute[:50]

            # Handle product fields safely
            color = ""
            category = ""
            model = ""

            if variant.product:
                color = variant.product.color[:50] if variant.product.color else ""
                category = variant.product.category[:50] if variant.product.category else ""
                model = variant.product.model[:50] if variant.product.model else ""

            # Handle thumbnail safely
            product_thumbnail = None
            if variant.product and variant.product.thumbnail_image:
                product_thumbnail = variant.product.thumbnail_image

            booked_item = BookedProductVariant(
                booking=booking,
                product_variant=variant,
                amount=item["amount"],
                quantity=item.get("quantity", 1),
                return_status="not_returned",
                user=request.user,
                shop=shop,
                product_name=product_name,
                variant_attribute=variant_attribute,
                color=color,
                category=category,
                model=model,
                product_thumbnail=product_thumbnail,
                measurements=measurements
            )
            booked_items.append(booked_item)

        # Single bulk create query
        BookedProductVariant.objects.bulk_create(booked_items)

        logger.info(f"Created {len(booked_items)} booked items for booking {booking.id}")
        return None

    except Exception as e:
        logger.error(f"Failed to create booked variants for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to create booked items.",
            dev_message=str(e)
        )


def _update_availability(self, variants, pickup_date, stock_end_date, booking_id, cooling_period_end=None, cooling_period_type="after"):
    """
    Update product variant availability.
    Returns error_response or None
    """
    try:
        today = date.today()

        # Determine availability_start_date based on cooling_period_type
        if cooling_period_end and cooling_period_type == "before":
            # Cooling period is before pickup date
            availability_start_date = cooling_period_end
            if hasattr(availability_start_date, 'date'):
                check_date = availability_start_date.date()
            else:
                check_date = availability_start_date

            # If cooling period is in the past, use today
            if check_date < today:
                if isinstance(availability_start_date, datetime):
                    availability_start_date = datetime.combine(today, availability_start_date.time())
                else:
                    availability_start_date = today
        else:
            # Default behavior: use pickup_date
            availability_start_date = pickup_date
            if hasattr(availability_start_date, 'date'):
                check_date = availability_start_date.date()
            else:
                check_date = availability_start_date

            if check_date < today:
                if isinstance(availability_start_date, datetime):
                    availability_start_date = datetime.combine(today, availability_start_date.time())
                else:
                    availability_start_date = today

        # For add_product_variant_availability, pass date or datetime as is
        # If stock_end_date is datetime, pass as is; else, pass as date
        add_product_variant_availability(
            variants,
            availability_start_date.date(),
            stock_end_date.date()
        )
        return None
    except Exception as e:
        logger.error(f"Failed to update availability for booking {booking_id}: {str(e)}")
        return self.error_response(
            message="Failed to update product availability.",
            dev_message=str(e)
        )


def _create_client_requirement(self, shop, booking, description):
    """
    Create client requirement record if description provided.
    Returns error_response or None
    """
    if not description:
        return None

    try:
        ClientRequirementV2.objects.create(
            shop=shop, booking=booking, description=description
        )
        return None
    except Exception as e:
        logger.error(f"Failed to create client requirement for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to create client requirement.",
            dev_message=str(e)
        )


def _handle_send_invoice(self, booking, data, user, base_message):
    """
    Check send_invoice flag and queue a WhatsApp invoice task if eligible.
    Returns the final success message reflecting the invoice outcome.
    All existing subscription, feature, and rate-limit checks are preserved as-is.
    """
    from django.core.cache import cache

    send_invoice = data.get("send_invoice", False)
    if send_invoice not in [True, "true", "True", 1, "1"]:
        return base_message

    try:
        from subscriptions.models import has_feature_access
        from shop.models import ShopUser, Shop

        shop_obj = Shop.objects.select_related('subscription').get(id=self.shop_id)

        if not hasattr(shop_obj, 'subscription') or not shop_obj.subscription:
            return f"{base_message} Invoice not sent - Shop does not have an active subscription."
        elif not shop_obj.subscription.is_active():
            return f"{base_message} Invoice not sent - Shop subscription is inactive or expired."

        shop_user = ShopUser.objects.filter(
            shop_id=self.shop_id,
            user=user
        ).first()

        if not shop_user:
            return f"{base_message} Invoice not sent - User not associated with this shop."
        elif not has_feature_access(shop_user, "WHATSAPP_MESSAGE"):
            return f"{base_message} Invoice not sent - Feature not available."

        cache_key = f"wa_task_queue_booking_{shop_obj.id}"
        try:
            current_count = cache.incr(cache_key)
        except ValueError:
            cache.add(cache_key, 1, timeout=60)
            current_count = 1

        if current_count > 10:
            cache.decr(cache_key)
            return f"{base_message} Too many pending invoices, please try again later."

        from whatsapp_integration.v3.tasks import send_booking_invoice_task
        send_booking_invoice_task.delay(booking.id)
        return f"{base_message} Invoice queued for sending."

    except Exception as e:
        logger.error(f"WhatsApp invoice validation/queue failed for booking {booking.id}: {str(e)}", exc_info=True)
        return f"{base_message} WhatsApp invoice not sent - {str(e)}"
