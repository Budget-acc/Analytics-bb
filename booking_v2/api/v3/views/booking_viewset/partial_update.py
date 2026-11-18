from datetime import datetime
from django.db import transaction
from django.utils.timezone import make_aware, now
from django.utils import timezone
from booking_v2.models import (
    PaymentDetailsV2,
    ClientRequirementV2,
    BookingV2,
    BookedProductVariant,
)
from services.models import ProductVariant
from booking_v2.api.v3.utils.availability import (
    check_product_variant_availability,
    add_product_variant_availability,
    remove_product_variant_availability,
)
from safedelete.models import HARD_DELETE
from ERP.utils.shop_activity_logs import create_activity_log
from pytz import timezone as pytz_timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
import logging

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


@transaction.atomic
@extend_schema(
    tags=["Booking Management V3"],
    summary="Update booking",
    description=(
        "Partially updates a booking including payment, status, dates, variants, and details.\n\n"
        "Purpose: Main API for operational booking edits after creation.\n\n"
        "Context: Supports controlled update rules to protect completed bookings, dates, and payment integrity."
    ),
    request=inline_serializer(
        name="BookingPartialUpdateRequestV3",
        fields={
            "discount_amount": serializers.IntegerField(required=False, help_text="Purpose: Updates discount. Location: request body. Format/Type: integer >= 0. Optional."),
            "amount": serializers.IntegerField(required=False, help_text="Purpose: Records additional payment amount. Location: request body. Format/Type: integer > 0. Optional, requires payment_method."),
            "payment_method": serializers.CharField(required=False, help_text="Purpose: Payment mode for additional payment. Location: request body. Format/Type: string. Optional, requires amount."),
            "delivery_status": serializers.CharField(required=False, help_text="Purpose: Updates delivery workflow state. Location: request body. Format/Type: string. Optional."),
            "description": serializers.CharField(required=False, allow_blank=True, help_text="Purpose: Updates client requirement text. Location: request body. Format/Type: string. Optional."),
            "pickup_date": serializers.CharField(required=False, help_text="Purpose: Updates pickup datetime. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Optional."),
            "return_date": serializers.CharField(required=False, help_text="Purpose: Updates return datetime. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Optional."),
            "variants": serializers.ListField(child=serializers.DictField(), required=False, help_text="Purpose: Updates booked variant set/amounts/quantities/measurements. Location: request body. Format/Type: array<object>. Optional."),
            "booking_status": serializers.CharField(required=False, help_text="Purpose: Marks booking state; completed requires returned + paid. Location: request body. Format/Type: string. Optional."),
            "details": serializers.DictField(required=False, help_text="Purpose: Updates structured booking metadata. Location: request body. Format/Type: object. Optional."),
            "advance_amount": serializers.IntegerField(required=False, help_text="Purpose: Directly updates advance amount field. Location: request body. Format/Type: integer. Optional."),
            "security_amount": serializers.IntegerField(required=False, help_text="Purpose: Directly updates security amount field. Location: request body. Format/Type: integer. Optional."),
            "purchase_mode": serializers.CharField(required=False, help_text="Purpose: Updates purchase mode. Location: request body. Format/Type: string. Optional."),
            "payment_status": serializers.BooleanField(required=False, help_text="Purpose: Directly updates payment status flag. Location: request body. Format/Type: boolean. Optional."),
            "staff_name": serializers.CharField(required=False, help_text="Purpose: Updates staff name. Location: request body. Format/Type: string. Optional."),
            "client_address": serializers.CharField(required=False, help_text="Purpose: Updates client address. Location: request body. Format/Type: string. Optional."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success exact message: Booking updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation/business errors exact messages include: Cannot update a completed booking.; Discount amount must be non-negative.; "
                "Invalid discount amount.; Both amount and payment method required.; Amount must be positive.; Amount must be an integer.; "
                "Payment already completed.; Cannot update the delivery status before pickup date.; Can only set to 'returned' after pickup.; "
                "Cannot modify dates - booking is already complete.; You can only extend the return date; Return date must be after pickup date.; "
                "Return date cannot be in the past.; Pickup date cannot be in the past.; Return time must be after pickup time.; "
                "Invalid date format. Use DD-MM-YYYYTHH:MM:SS; Total cannot be less than paid amount.; Total amount must be greater than zero.; "
                "Discount amount cannot exceed total amount.; Total cannot be less than paid advance amount.; "
                "Mark as 'returned' and complete payment to finish.; Invalid format for details."
            )
        ),
        404: OpenApiResponse(description="Not found exact message: Booking not found."),
        500: OpenApiResponse(description="Server error exact message: An error occurred while updating the booking. Please try again."),
    },
    examples=[
        OpenApiExample(
            "Update booking request",
            value={
                "return_date": "25-05-2026T10:00:00",
                "discount_amount": 200,
                "amount": 500,
                "payment_method": "upi",
                "details": {"trial": "done"}
            },
            request_only=True,
        )
    ],
)
def partial_update(self, request, pk=None):
    try:
        booking = (
            BookingV2.objects
            .prefetch_related("booked_items")
            .get(id=pk)
        )

        data = request.data
        changed_fields = []

        # Prevent update if booking already completed
        if booking.booking_status == "completed":
            return self.validation_error_response(
                errors={"booking_status": ["Cannot update a completed booking."]},
                message="Cannot update a completed booking."
            )

        # Discount Amount
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

        # --- Optional Payment Update ---
        amount = data.get("amount")
        payment_method = data.get("payment_method")
        if amount is not None or payment_method is not None:
            if not amount or not payment_method:
                return self.validation_error_response(
                    errors={"payment": ["Both amount and payment method required."]},
                    message="Both amount and payment method required."
                )
            try:
                amount = int(amount)
                if amount <= 0:
                    return self.validation_error_response(
                        errors={"amount": ["Amount must be positive."]},
                        message="Amount must be positive."
                    )
            except Exception:
                return self.validation_error_response(
                    errors={"amount": ["Amount must be an integer."]},
                    message="Amount must be an integer."
                )

            payment_method = payment_method.lower().strip()

            total_amount = booking.total_amount or 0
            discount_amount = booking.discount_amount or 0
            advance_amount = booking.advance_amount or 0
            remaining = (total_amount - discount_amount) - advance_amount

            if booking.payment_status or remaining <= 0:
                return self.error_response(
                    data=None,
                    message="Payment already completed."
                )
            if amount > remaining:
                return self.validation_error_response(
                    errors={"amount": [f"Payment exceeds balance of ₹{remaining}."]},
                    message=f"Payment exceeds balance of ₹{remaining}."
                )

            booking.advance_amount += amount
            if booking.advance_amount >= (booking.total_amount - booking.discount_amount):
                booking.payment_status = True
            booking.payment_method = payment_method

            PaymentDetailsV2.objects.create(
                booking=booking,
                payment_amount=amount,
                payment_method=payment_method,
            )
            changed_fields.append("payment")

        # --- Delivery status ---
        if "delivery_status" in data:
            delivery_status = data.get("delivery_status")
            current_datetime = now().astimezone(india)  # Convert to Asia/Kolkata
            pickup_datetime = booking.pickup_date.astimezone(india)

            # Check if trying to set status to delivered before pickup date
            if delivery_status == "delivered" and current_datetime.date() < pickup_datetime.date():
                return self.validation_error_response(
                    errors={"delivery_status": ["You can't update the delivery status yet. Please wait until the pickup date."]},
                    message="Cannot update the delivery status before pickup date."
                )

            # Check if trying to set status to returned before pickup date
            if delivery_status == "returned" and current_datetime < pickup_datetime:
                return self.validation_error_response(
                    errors={"delivery_status": ["You can't mark an item as returned before the pickup date."]},
                    message="Can only set to 'returned' after pickup."
                )

            booking.delivery_status = delivery_status
            changed_fields.append("delivery_status")

        # --- Optional client requirement ---
        if "description" in data:
            description = data["description"]
            req, _ = ClientRequirementV2.objects.get_or_create(
                booking=booking, shop=booking.shop
            )
            req.description = description
            req.save()
            changed_fields.append("client_requirement")

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
                            return self.validation_error_response(
                                errors={"pickup_date": ["You can't change the pickup date because it has already passed. You can only extend the return date."]},
                                message="You can only extend the return date"
                            )

                    if return_date_str:
                        new_return_date = make_aware(parse_date_string(return_date_str))
                        # Ensure return is after the old pickup date and not in the past
                        if new_return_date <= old_pickup_date:
                            return self.validation_error_response(
                                errors={"return_date": ["The return date you entered is before the pickup date. The return date must be after the pickup date."]},
                                message="Return date must be after pickup date."
                            )
                        if new_return_date.date() < current_date:
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
                            return self.validation_error_response(
                                errors={"pickup_date": ["Pickup date cannot be in the past."]},
                                message="Pickup date cannot be in the past."
                            )

                    if return_date_str:
                        new_return_date = make_aware(parse_date_string(return_date_str))

                    # Ensure return is after pickup (using the updated pickup date)
                    if new_return_date <= new_pickup_date:
                        return self.validation_error_response(
                            errors={"return_date": ["Return time must be after pickup time."]},
                            message="Return time must be after pickup time."
                        )

                if new_pickup_date != old_pickup_date:
                    changed_fields.append("pickup_date")
                if new_return_date != old_return_date:
                    changed_fields.append("return_date")

            except ValueError:
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

        def variants_equal(old, new):
            def extract_data(v):
                return {"id": v["id"], "quantity": v.get("quantity", 1)}
            return sorted(map(extract_data, old), key=lambda x: x["id"]) == \
                sorted(map(extract_data, new), key=lambda x: x["id"])

        if any(k in data for k in ["variants", "pickup_date", "return_date"]):

            variants_same = variants_equal(old_variants, new_variants)
            dates_same = new_pickup_date == old_pickup_date and new_return_date == old_return_date

            # Handle availability only if dates or variant IDs changed
            if not (variants_same and dates_same):
                remove_product_variant_availability(old_variants, old_pickup_date, old_return_date)

                availability_resp = check_product_variant_availability(
                    new_variants, new_pickup_date, new_return_date, exclude_booking_id=booking.id
                )
                if availability_resp:
                    add_product_variant_availability(old_variants, old_pickup_date, old_return_date)
                    return availability_resp

                add_product_variant_availability(new_variants, new_pickup_date, new_return_date)
                changed_fields.append("variants_or_availability")

            # Always update variant details (amount, measurements)
            if not variants_same:
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
                    return self.validation_error_response(
                        errors={"total_amount": ["The total amount cannot be less than the amount that has already been paid."]},
                        message="Total cannot be less than paid amount."
                    )
                # Ensure total amount > 0
                if payable_amount <= 0:
                    return self.validation_error_response(
                        errors={"total_amount": ["Total amount must be greater than zero."]},
                        message="Total amount must be greater than zero."
                    )
                booking.total_amount = total
                if discount_amount > booking.total_amount:
                    return self.validation_error_response(
                        errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                        message="Discount amount cannot exceed total amount."
                    )
            else:
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
                        changed_fields.append("variant_amounts")
                    # Update measurements if provided
                    if "measurements" in item:
                        existing.measurements = item["measurements"]
                        changed_fields.append("variant_measurements")
                    existing.save()
                    total += existing.amount
                # Validation: New total cannot be less than advance already paid
                if total < booking.advance_amount:
                    return self.validation_error_response(
                        errors={"total_amount": ["Total amount cannot be less than the already paid advance amount."]},
                        message="Total cannot be less than paid advance amount."
                    )
                # Ensure total amount > 0
                if total <= 0:
                    return self.validation_error_response(
                        errors={"total_amount": ["Total amount must be greater than zero."]},
                        message="Total amount must be greater than zero."
                    )
                booking.total_amount = total
                if discount_amount > booking.total_amount:
                    return self.validation_error_response(
                        errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                        message="Discount amount cannot exceed total amount."
                    )

            booking.pickup_date = new_pickup_date
            booking.return_date = new_return_date

        # Updating the discount amount
        booking.discount_amount = discount_amount
        if "discount_amount" in data:
            changed_fields.append("discount_amount")

        # --- Conditional Booking Completion ---
        booking_status = data.get("booking_status")
        if booking_status == "completed":
            if booking.delivery_status != "returned" or not booking.payment_status:
                return self.validation_error_response(
                    errors={"booking_status": ["Cannot mark booking as completed. You must first confirm that the item has been returned and that the payment is complete."]},
                    message="Mark as 'returned' and complete payment to finish."
                )
            else:
                booking.booking_status = "completed"
                booking.completed_at = now()
                changed_fields.append("booking status")

                current_date = now().date()
                return_date = booking.return_date.date()

                if current_date < return_date:
                    # Remove availability from today to return date
                    remove_product_variant_availability(
                        old_variants,
                        timezone.now(),  # From today
                        booking.return_date  # To original return date
                    )

        # --- Details update ---
        if "details" in data:
            details = data["details"]

            # Ensure it's a valid dictionary
            if not isinstance(details, dict):
                return self.validation_error_response(
                    errors={"details": ["Invalid format for details."]},
                    message="Invalid format for details."
                )

            booking.details = details
            changed_fields.append("details")

        # --- Update Other Fields ---
        updatable_fields = [
            "advance_amount",
            "security_amount",
            "purchase_mode",
            "payment_status",
            "staff_name",
            "client_address"
        ]
        for field in updatable_fields:
            if field in data:
                setattr(booking, field, data[field])
                changed_fields.append(field)

        booking.save()

        try:
            booking_descriptor = (booking.client_identity.client_name if booking.client_identity else (booking.user.full_name or "Unknown"))
            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action="updated",
                message=(
                    f"Booking for '{booking_descriptor}'. Changes: {', '.join(sorted(set(changed_fields))) if changed_fields else 'none'}."
                )
            )
        except Exception:
            logger.exception("Failed to create activity log for booking partial_update")

        return self.success_response(
            data=None,
            message="Booking updated successfully."
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
