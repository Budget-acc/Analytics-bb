from datetime import datetime, date
from django.utils.timezone import make_aware, now
from django.db import transaction
from django.utils import timezone
from safedelete.models import HARD_DELETE
from pytz import timezone as pytz_timezone
from booking_v2.models import BookingV2, BookedProductVariant, PaymentDetailsV2, ClientRequirementV2
from services.models import ProductVariant
from booking_v2.api.v4.utils.availability import (
    check_product_variant_availability,
    add_product_variant_availability,
    remove_product_variant_availability,
)
import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")

india = pytz_timezone('Asia/Kolkata')


def parse_date_string(date_str):
    formats = ["%d-%m-%Y", "%d-%m-%YT%H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError("Invalid date format")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Update booking",
    description=(
        "Partially updates a booking including payment, delivery status, dates, variants, cooling period, and metadata.\n\n"
        "Purpose: Main API for all operational booking edits after creation.\n\n"
        "Context: Enforces business rules — completed bookings cannot be edited, past pickup dates cannot be changed, "
        "and payment/discount integrity is validated on every save."
    ),
    request=inline_serializer(
        name="BookingPartialUpdateRequestV4",
        fields={
            "discount_amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Updates the discount applied to the total. Location: request body. Format/Type: integer >= 0. Optional."
            ),
            "amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Records an additional payment instalment. Location: request body. Format/Type: integer > 0. Optional; must be provided together with payment_method."
            ),
            "payment_method": serializers.CharField(
                required=False,
                help_text="Purpose: Payment mode for the additional payment. Location: request body. Format/Type: string. Optional; must be provided together with amount."
            ),
            "delivery_status": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the delivery workflow state (booked, delivered, returned). Location: request body. Format/Type: string. Optional."
            ),
            "description": serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="Purpose: Updates or sets client requirement/special notes. Location: request body. Format/Type: string. Optional."
            ),
            "pickup_date": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the pickup datetime. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Optional."
            ),
            "return_date": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the return datetime. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Optional."
            ),
            "cooling_period_end": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the extended inventory hold end (must be >= return_date). Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Optional."
            ),
            "variants": serializers.ListField(
                child=serializers.DictField(),
                required=False,
                help_text="Purpose: Replaces the booked variant set; supports updating id, amount, quantity, and measurements. Location: request body. Format/Type: array of objects. Optional."
            ),
            "booking_status": serializers.CharField(
                required=False,
                help_text="Purpose: Marks booking as completed (requires delivery_status=returned and payment fully settled). Location: request body. Format/Type: string. Optional."
            ),
            "details": serializers.DictField(
                required=False,
                help_text="Purpose: Updates additional structured booking metadata. Location: request body. Format/Type: object. Optional."
            ),
            "advance_amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Directly overrides the advance amount field. Location: request body. Format/Type: integer. Optional."
            ),
            "security_amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Directly overrides the security deposit field. Location: request body. Format/Type: integer. Optional."
            ),
            "purchase_mode": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the business purchase mode marker. Location: request body. Format/Type: string. Optional."
            ),
            "staff_name": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the assigned staff name. Location: request body. Format/Type: string. Optional."
            ),
            "client_address": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the client address stored on the booking. Location: request body. Format/Type: string. Optional."
            ),
        },
    ),
    responses={
        200: OpenApiResponse(
            description="Success.\n\nExact message:\n- Booking updated successfully."
        ),
        400: OpenApiResponse(
            description=(
                "Validation/business errors. Exact messages (one per case):\n"
                "- Cannot update a completed booking.\n"
                "- Discount amount must be non-negative.\n"
                "- Invalid discount amount.\n"
                "- Both amount and payment method required.\n"
                "- Amount must be positive.\n"
                "- Amount must be an integer.\n"
                "- Payment already completed.\n"
                "- Failed to record payment.\n"
                "- Cannot update the delivery status before pickup date.\n"
                "- Can only set to 'returned' after pickup.\n"
                "- Failed to update client requirement.\n"
                "- Cannot modify dates - booking is already complete.\n"
                "- You can only extend the return date\n"
                "- Return date must be after pickup date.\n"
                "- Return date cannot be in the past.\n"
                "- Pickup date cannot be in the past.\n"
                "- Return time must be after pickup time.\n"
                "- Invalid date format. Use DD-MM-YYYYTHH:MM:SS\n"
                "- Please set the cooling period after the return date.\n"
                "- Invalid cooling period end format.\n"
                "- Failed to check product availability.\n"
                "- Total cannot be less than paid amount.\n"
                "- Total amount must be greater than zero.\n"
                "- Discount amount cannot exceed total amount.\n"
                "- Failed to update booking variants.\n"
                "- Total cannot be less than paid advance amount.\n"
                "- Failed to update variant details.\n"
                "- Should mark as 'returned' and complete payment to finish.\n"
                "- Failed to save booking changes."
            )
        ),
        404: OpenApiResponse(
            description="Not found.\n\nExact message:\n- Booking not found."
        ),
        500: OpenApiResponse(
            description="Server error.\n\nExact message:\n- An error occurred while updating the booking. Please try again."
        ),
    },
    examples=[
        OpenApiExample(
            "Update booking request",
            value={
                "return_date": "25-05-2026T10:00:00",
                "cooling_period_end": "27-05-2026T10:00:00",
                "discount_amount": 200,
                "amount": 500,
                "payment_method": "upi",
            },
            request_only=True,
        )
    ],
)
@transaction.atomic
def partial_update(self, request, pk=None):
    try:
        booking = (
            BookingV2.objects
            .select_for_update()
            .prefetch_related("booked_items")
            .get(id=pk)
        )

        data = request.data

        # Prevent update if booking already completed
        if booking.booking_status == "completed":
            return self.validation_error_response(
                errors={"booking_status": ["Cannot update a completed booking."]},
                message="Cannot update a completed booking."
            )

        # Discount Amount validation
        discount_amount = data.get("discount_amount", booking.discount_amount or 0)
        try:
            discount_amount = int(discount_amount)
            if discount_amount < 0:
                return self.validation_error_response(
                    errors={"discount_amount": ["Discount amount must be non-negative."]},
                    message="Discount amount must be non-negative."
                )
        except ValueError:
            return self.validation_error_response(
                errors={"discount_amount": ["Invalid discount amount."]},
                message="Invalid discount amount."
            )

        # Create a savepoint for nested transaction handling
        savepoint = transaction.savepoint()

        try:
            # --- Payment Update Logic ---
            amount = data.get("amount")
            payment_method = data.get("payment_method")

            if amount is not None or payment_method is not None:
                if not amount or not payment_method:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"payment": ["Both amount and payment method required."]},
                        message="Both amount and payment method required."
                    )

                try:
                    amount = int(amount)
                    if amount <= 0:
                        transaction.savepoint_rollback(savepoint)
                        return self.validation_error_response(
                            errors={"amount": ["Amount must be positive."]},
                            message="Amount must be positive."
                        )
                except Exception:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"amount": ["Amount must be an integer."]},
                        message="Amount must be an integer."
                    )

                payment_method = payment_method.lower().strip()

                # Recalculate with fresh data from DB (since we have select_for_update)
                total_amount = booking.total_amount or 0
                current_discount = booking.discount_amount or 0
                current_advance = booking.advance_amount or 0

                # Calculate remaining amount to be paid
                remaining = (total_amount - current_discount) - current_advance

                # Check if payment is already completed
                if booking.payment_status or remaining <= 0:
                    transaction.savepoint_commit(savepoint)
                    return self.error_response(
                        data=None,
                        message="Payment already completed."
                    )

                # Validate payment amount
                if amount > remaining:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"amount": [f"Payment exceeds balance of \u20b9{remaining}."]},
                        message=f"Payment exceeds balance of \u20b9{remaining}."
                    )

                # Create payment record first to ensure it doesn't fail
                try:
                    PaymentDetailsV2.objects.create(
                        booking=booking,
                        payment_amount=amount,
                        payment_method=payment_method,
                    )
                except Exception as e:
                    transaction.savepoint_rollback(savepoint)
                    logger.error(f"Failed to create payment record for booking {pk}: {str(e)}")
                    return self.validation_error_response(
                        errors={"payment": ["Failed to record payment."]},
                        message="Failed to record payment."
                    )

                # Update booking payment details
                booking.advance_amount += amount

                # Calculate final payable amount after discount
                final_payable = total_amount - current_discount

                # Update payment status based on exact comparison
                if booking.advance_amount >= final_payable:
                    booking.payment_status = True
                else:
                    booking.payment_status = False

                booking.payment_method = payment_method

            # --- Delivery status ---
            if "delivery_status" in data:
                delivery_status = data.get("delivery_status")
                current_datetime = now().astimezone(india)  # Convert to Asia/Kolkata
                pickup_datetime = booking.pickup_date.astimezone(india)

                # Check if trying to set status to delivered before pickup date
                if delivery_status == "delivered" and current_datetime.date() < pickup_datetime.date():
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"delivery_status": ["You can't update the delivery status yet. Please wait until the pickup date."]},
                        message="Cannot update the delivery status before pickup date."
                    )

                # Check if trying to set status to returned before pickup date
                if delivery_status == "returned" and current_datetime < pickup_datetime:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"delivery_status": ["You can't mark an item as returned before the pickup date."]},
                        message="Can only set to 'returned' after pickup."
                    )

                booking.delivery_status = delivery_status

            # --- Optional client requirement ---
            if "description" in data:
                description = data["description"]
                try:
                    req, _ = ClientRequirementV2.objects.get_or_create(
                        booking=booking, shop=booking.shop
                    )
                    req.description = description
                    req.save()
                except Exception as e:
                    transaction.savepoint_rollback(savepoint)
                    logger.error(f"Failed to update client requirement for booking {pk}: {str(e)}")
                    return self.error_response(
                        message="Failed to update client requirement.",
                        dev_message=str(e)
                    )

            # --- Dates and Variants ---
            old_items = list(booking.booked_items.all())
            old_variants = [{
                "id": item.product_variant_id,
                "amount": item.amount,
                "quantity": item.quantity or 1
                } for item in old_items]
            old_pickup_date = booking.pickup_date.astimezone(india)
            old_return_date = booking.return_date.astimezone(india)

            # Date parsing logic
            pickup_date_str = data.get("pickup_date")
            return_date_str = data.get("return_date")
            new_pickup_date = old_pickup_date
            new_return_date = old_return_date

            if pickup_date_str or return_date_str:
                try:
                    current_date = timezone.now().date()
                    old_pickup_date_only = old_pickup_date.date()
                    old_return_date_only = old_return_date.date()

                    # Check if both pickup and return dates are in the past
                    pickup_is_past = old_pickup_date_only < current_date
                    return_is_past = old_return_date_only < current_date

                    if pickup_is_past and return_is_past:
                        # If both dates are past, no date modifications allowed
                        transaction.savepoint_rollback(savepoint)
                        return self.validation_error_response(
                            errors={"date": ["You can't change the dates for this booking because the pickup and return dates have already passed. The booking is over."]},
                            message="Cannot modify dates - booking is already complete."
                        )
                    elif pickup_is_past:
                        # If only pickup is past, allow return date extension (but return is still future)
                        if pickup_date_str:
                            new_pickup_date = make_aware(parse_date_string(pickup_date_str))
                            # Only throw error if trying to change pickup date to a different date
                            if new_pickup_date.date() != old_pickup_date.date():
                                transaction.savepoint_rollback(savepoint)
                                return self.validation_error_response(
                                    errors={"pickup_date": ["You can't change the pickup date because it has already passed. You can only extend the return date."]},
                                    message="You can only extend the return date"
                                )

                        if return_date_str:
                            new_return_date = make_aware(parse_date_string(return_date_str))
                            # Ensure return is after the old pickup date and not in the past
                            if new_return_date <= old_pickup_date:
                                transaction.savepoint_rollback(savepoint)
                                return self.validation_error_response(
                                    errors={"return_date": ["The return date you entered is before the pickup date. The return date must be after the pickup date."]},
                                    message="Return date must be after pickup date."
                                )
                            if new_return_date.date() < current_date:
                                transaction.savepoint_rollback(savepoint)
                                return self.validation_error_response(
                                    errors={"return_date": ["Return date cannot be in the past."]},
                                    message="Return date cannot be in the past."
                                )
                    else:
                        # Pickup is future, allow both date changes (pickup AND/OR return)
                        if pickup_date_str:
                            new_pickup_date = make_aware(parse_date_string(pickup_date_str))
                            # Ensure new pickup is not in the past
                            if new_pickup_date != old_pickup_date and new_pickup_date.date() < current_date:
                                transaction.savepoint_rollback(savepoint)
                                return self.validation_error_response(
                                    errors={"pickup_date": ["Pickup date cannot be in the past."]},
                                    message="Pickup date cannot be in the past."
                                )

                        if return_date_str:
                            new_return_date = make_aware(parse_date_string(return_date_str))

                        # Ensure return is after pickup (using the updated pickup date)
                        if new_return_date <= new_pickup_date:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"return_date": ["Return time must be after pickup time."]},
                                message="Return time must be after pickup time."
                            )

                except ValueError:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"date": ["Invalid date format. Use DD-MM-YYYYTHH:MM:SS"]},
                        message="Invalid date format. Use DD-MM-YYYYTHH:MM:SS"
                    )

            if "variants" in data:
                new_items = data["variants"]
                existing_items_map = {item.product_variant_id: item for item in booking.booked_items.all()}
                new_variants = []
                for item in new_items:
                    vid = item["id"]
                    existing = existing_items_map.get(vid)
                    new_variants.append({
                        "id": vid,
                        "amount": item.get("amount", existing.amount if existing else 0),
                        "quantity": item.get("quantity", existing.quantity if existing else 1),
                        "measurements": item.get("measurements", existing.measurements if existing else {})
                    })
            else:
                new_variants = old_variants

            # --- Cooling Period End ---
            cooling_period_end_str = data.get("cooling_period_end")
            old_cooling_end = booking.cooling_period_end.astimezone(india) if booking.cooling_period_end else None
            new_cooling_end = old_cooling_end

            if cooling_period_end_str:
                try:
                    new_cooling_end = make_aware(parse_date_string(cooling_period_end_str))
                    # Check against return date (new_return_date if updated, else old_return_date)
                    reference_return_date = new_return_date if 'return_date' in data else old_return_date
                    if new_cooling_end < reference_return_date:
                        transaction.savepoint_rollback(savepoint)
                        return self.validation_error_response(
                            errors={"cooling_period_end": ["Cooling period end must be greater than or equal to return date."]},
                            message="Please set the cooling period after the return date."
                        )
                except Exception:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"cooling_period_end": ["Invalid cooling period end format."]},
                        message="Invalid cooling period end format."
                    )

            def variants_equal(old, new):
                def extract_data(v):
                    return {"id": v["id"], "quantity": v.get("quantity", 1)}
                return sorted(map(extract_data, old), key=lambda x: x["id"]) == \
                    sorted(map(extract_data, new), key=lambda x: x["id"])

            # --- Handle availability if variants or dates changed ---
            if any(k in data for k in ["variants", "pickup_date", "return_date", "cooling_period_end"]):

                variants_same = variants_equal(old_variants, new_variants)
                dates_same = (
                    new_pickup_date == old_pickup_date
                    and new_return_date == old_return_date
                    and new_cooling_end == old_cooling_end
                )

                if not (variants_same and dates_same):
                    # old end date = old cooling end if exists
                    old_end_date = old_cooling_end if old_cooling_end else old_return_date
                    try:
                        remove_product_variant_availability(old_variants, old_pickup_date.date(), old_end_date.date())

                        # new end date = max(new_cooling_end if exists else new_return_date, new_return_date)
                        new_end_date = new_cooling_end if new_cooling_end else new_return_date
                        new_end_date = max(new_end_date, new_return_date)

                        availability_resp = check_product_variant_availability(
                            new_variants, new_pickup_date, new_end_date, exclude_booking_id=booking.id
                        )
                        if availability_resp:
                            add_product_variant_availability(old_variants, old_pickup_date.date(), old_end_date.date())
                            transaction.savepoint_rollback(savepoint)
                            return availability_resp

                        add_product_variant_availability(new_variants, new_pickup_date.date(), new_end_date.date())
                    except Exception as e:
                        transaction.savepoint_rollback(savepoint)
                        logger.error(f"Availability check failed for booking {pk}: {str(e)}")
                        return self.error_response(
                            message="Failed to check product availability.",
                            dev_message=str(e)
                        )

                # Always update variant details (amount, measurements)
                if not variants_same:
                    try:
                        for item in booking.booked_items.all():
                            item.delete(force_policy=HARD_DELETE)
                        total = 0
                        for item in new_variants:
                            variant = ProductVariant.objects.get(id=item["id"])
                            amt = item.get("amount", variant.price or 0)
                            total += amt
                            BookedProductVariant.objects.create(
                                booking=booking,
                                product_variant_id=item["id"],
                                user=request.user,
                                shop=booking.shop,
                                amount=amt,
                                quantity=item.get("quantity", 1),
                                product_name=variant.product.name,
                                variant_attribute=variant.attribute,
                                color=variant.product.color,
                                category=variant.product.category,
                                model=variant.product.model,
                                measurements=item.get("measurements", {})
                            )

                        # Validation: New total cannot be less than advance already paid
                        payable_amount = total - discount_amount
                        if payable_amount < booking.advance_amount:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"total_amount": ["The total amount cannot be less than the amount that has already been paid."]},
                                message="Total cannot be less than paid amount."
                            )
                        # Ensure total amount > 0
                        if payable_amount <= 0:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"total_amount": ["Total amount must be greater than zero."]},
                                message="Total amount must be greater than zero."
                            )
                        booking.total_amount = total
                        if discount_amount > booking.total_amount:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                                message="Discount amount cannot exceed total amount."
                            )
                    except Exception as e:
                        transaction.savepoint_rollback(savepoint)
                        logger.error(f"Failed to update variants for booking {pk}: {str(e)}")
                        return self.error_response(
                            message="Failed to update booking variants.",
                            dev_message=str(e)
                        )
                else:
                    try:
                        total = 0
                        existing_items = {item.product_variant_id: item for item in booking.booked_items.all()}
                        for item in new_variants:
                            variant_id = item["id"]
                            existing = existing_items.get(variant_id)
                            if not existing:
                                continue
                            # Update amount if provided
                            if "amount" in item and item["amount"] > 0:
                                existing.amount = item["amount"]
                            # Update measurements if provided
                            if "measurements" in item:
                                existing.measurements = item["measurements"]
                            existing.save()
                            total += existing.amount
                        # Validation: New total cannot be less than advance already paid
                        if total < booking.advance_amount:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"total_amount": ["Total amount cannot be less than the already paid advance amount."]},
                                message="Total cannot be less than paid advance amount."
                            )
                        # Ensure total amount > 0
                        if total <= 0:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"total_amount": ["Total amount must be greater than zero."]},
                                message="Total amount must be greater than zero."
                            )
                        booking.total_amount = total
                        if discount_amount > booking.total_amount:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                                message="Discount amount cannot exceed total amount."
                            )
                    except Exception as e:
                        transaction.savepoint_rollback(savepoint)
                        logger.error(f"Failed to update variant amounts for booking {pk}: {str(e)}")
                        return self.error_response(
                            message="Failed to update variant details.",
                            dev_message=str(e)
                        )

                booking.pickup_date = new_pickup_date
                booking.return_date = new_return_date
                booking.cooling_period_end = new_cooling_end

            # Updating the discount amount
            booking.discount_amount = discount_amount

            if discount_amount > (booking.total_amount or 0):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                    message="Discount amount cannot exceed total amount."
                )

            total_amount = booking.total_amount or 0
            current_advance = booking.advance_amount or 0
            final_payable = total_amount - discount_amount

            if final_payable <= 0:
                booking.payment_status = True
                if final_payable < 0:
                    logger.warning(f"Over-discounting detected for booking {pk}: discount={discount_amount}, total={total_amount}")
            else:
                booking.payment_status = (current_advance >= final_payable)

            if booking.payment_status and current_advance < final_payable and final_payable > 0:
                logger.error(f"Inconsistent payment state for booking {pk}: paid={current_advance}, payable={final_payable}, status={booking.payment_status}")
                booking.payment_status = False

            # --- Conditional Booking Completion ---
            booking_status = data.get("booking_status")
            if booking_status == "completed":
                if booking.delivery_status != "returned" or not booking.payment_status:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"booking_status": ["Cannot mark booking as completed. You must first confirm that the item has been returned and that the payment is complete."]},
                        message="Should mark as 'returned' and complete payment to finish."
                    )
                else:
                    try:
                        booking.booking_status = "completed"
                        booking.completed_at = now()

                        current_date = now().date()
                        return_date = booking.return_date.date()

                        if current_date < return_date:
                            # Remove availability from today to return date
                            remove_product_variant_availability(
                                old_variants,
                                timezone.now(),  # From today
                                booking.return_date  # To original return date
                            )
                    except Exception as e:
                        transaction.savepoint_rollback(savepoint)
                        logger.error(f"Failed to complete booking {pk}: {str(e)}")
                        return self.error_response(
                            message="Failed to complete booking.",
                            dev_message=str(e)
                        )

            # --- Details update ---
            if "details" in data:
                details = data["details"]
                # Ensure it's a valid dictionary
                if not isinstance(details, dict):
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"details": ["Invalid format for details."]},
                        message="Invalid format for details."
                    )
                booking.details = details

            # --- Update Other Fields ---
            updatable_fields = [
                "security_amount",
                "purchase_mode",
                "staff_name",
                "client_address"
            ]
            for field in updatable_fields:
                if field in data:
                    setattr(booking, field, data[field])

            # Final save - this will save all changes atomically
            try:
                booking.save()

                # Commit the savepoint
                transaction.savepoint_commit(savepoint)

                return self.success_response(
                    data=None,
                    message="Booking updated successfully."
                )

            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Failed to save booking {pk}: {str(e)}")
                return self.error_response(
                    message="Failed to save booking changes.",
                    dev_message=str(e)
                )

        except Exception as e:
            # Rollback to savepoint on any other errors
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Booking update failed for booking ID {pk}: {str(e)}", exc_info=True)
            return self.error_response(
                message="An error occurred while updating the booking. Please try again.",
                dev_message=str(e)
            )

    except BookingV2.DoesNotExist:
        return self.not_found_response(
            resource="Booking",
            message="Booking not found.",
            dev_message=f"Booking with id {pk} not found."
        )
    except Exception as e:
        logger.error(f"Booking update failed for booking ID {pk}: {str(e)}", exc_info=True)
        return self.error_response(
            message="An error occurred while updating the booking. Please try again.",
            dev_message=str(e)
        )
