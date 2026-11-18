from datetime import datetime, date
from django.utils.timezone import make_aware, now
from django.db import transaction
from rest_framework.exceptions import ValidationError
from shop.models import Shop
from booking_v2.models import BookingV2, Client, BookedProductVariant, PaymentDetailsV2, ClientRequirementV2
from services.models import ProductVariant
from booking_v2.api.v4.utils.availability import check_product_variant_availability, add_product_variant_availability
import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")


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
    summary="Create booking",
    description=(
        "Creates a new booking for an existing client and reserves product variant availability.\n\n"
        "Purpose: Main API to create future bookings with pricing, payment, and optional cooling period.\n\n"
        "Context: Use in booking create flow after choosing shop, client, schedule, and variants. "
        "Supports an optional cooling_period_end to extend inventory hold beyond the return date."
    ),
    request=inline_serializer(
        name="BookingCreateRequestV4",
        fields={
            "client_id": serializers.IntegerField(
                help_text="Purpose: Identifies the existing client for this booking. Location: request body. Format/Type: integer. Required."
            ),
            "client_address": serializers.CharField(
                help_text="Purpose: Stores the client address for booking reference. Location: request body. Format/Type: string. Required."
            ),
            "pickup_date": serializers.CharField(
                help_text="Purpose: Datetime when items are picked up. Location: request body. Format/Type: string; accepted: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Required."
            ),
            "return_date": serializers.CharField(
                help_text="Purpose: Datetime when items must be returned. Location: request body. Format/Type: string; accepted: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Required."
            ),
            "cooling_period_end": serializers.CharField(
                required=False,
                help_text="Purpose: Optional extended hold end date beyond return date (keeps variants unavailable for cleaning/maintenance). Location: request body. Format/Type: string; accepted: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS; must be >= return_date. Optional."
            ),
            "delivery_status": serializers.CharField(
                required=False,
                help_text="Purpose: Initial delivery workflow state. Location: request body. Format/Type: string. Optional. Default: booked."
            ),
            "variants": serializers.ListField(
                child=serializers.DictField(),
                help_text="Purpose: Selected product variants with id, amount, quantity, and optional measurements. Location: request body. Format/Type: array of objects. Required."
            ),
            "discount_amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Discount deducted from the total amount. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."
            ),
            "security_amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Refundable security deposit amount. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."
            ),
            "advance_amount": serializers.IntegerField(
                required=False,
                help_text="Purpose: Upfront partial payment amount. Location: request body. Format/Type: integer >= 0 and <= payable total. Optional. Default: 0."
            ),
            "payment_method": serializers.CharField(
                required=False,
                help_text="Purpose: Payment mode used for the initial advance record. Location: request body. Format/Type: string. Optional. Default: cash."
            ),
            "service_id": serializers.IntegerField(
                required=False,
                help_text="Purpose: Links the booking to a shop service. Location: request body. Format/Type: integer. Optional."
            ),
            "purchase_mode": serializers.CharField(
                required=False,
                help_text="Purpose: Business purchase mode marker for the booking. Location: request body. Format/Type: string. Optional."
            ),
            "staff_name": serializers.CharField(
                required=False,
                help_text="Purpose: Captures the name of the assigned staff member. Location: request body. Format/Type: string. Optional."
            ),
            "description": serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="Purpose: Client requirement or special notes. Location: request body. Format/Type: string. Optional."
            ),
            "details": serializers.DictField(
                required=False,
                help_text="Purpose: Stores additional structured booking metadata. Location: request body. Format/Type: object. Optional."
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success.\n\nExact message:\n- Booking created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages (one per case):\n"
                "- Client is required.\n"
                "- Client address is required.\n"
                "- Shop not found.\n"
                "- Client not found.\n"
                "- Pickup date is required.\n"
                "- Return date is required.\n"
                "- Pickup date cannot be in the past.\n"
                "- Return time must be after pickup time.\n"
                "- Please set the cooling period after the return date.\n"
                "- Invalid cooling period end format.\n"
                "- Invalid date format.\n"
                "- New bookings can't be marked as returned.\n"
                "- Can set to 'delivered' only on pickup day.\n"
                "- Product variants are required.\n"
                "- Some product variants are invalid.\n"
                "- Failed to validate product variants.\n"
                "- Total amount must be greater than zero.\n"
                "- Invalid variant amounts.\n"
                "- Discount cannot be negative.\n"
                "- Discount amount cannot exceed total amount.\n"
                "- Invalid discount amount.\n"
                "- Security amount cannot be negative.\n"
                "- Invalid security amount.\n"
                "- Advance amount must be positive."
            )
        ),
        500: OpenApiResponse(description="Server error.\n\nExact message:\n- Something went wrong while creating the booking."),
    },
    examples=[
        OpenApiExample(
            "Create booking request",
            value={
                "client_id": 12,
                "client_address": "Main Road, Surat",
                "pickup_date": "20-05-2026T10:00:00",
                "return_date": "22-05-2026T10:00:00",
                "cooling_period_end": "24-05-2026T10:00:00",
                "variants": [{"id": 101, "amount": 1500, "quantity": 1}],
                "discount_amount": 100,
                "security_amount": 500,
                "advance_amount": 500,
                "payment_method": "cash",
            },
            request_only=True,
        )
    ],
)
@transaction.atomic
def create(self, request):
    savepoint = transaction.savepoint()

    try:
        # --- STEP 1: Validate Client Identity
        client_id = request.data.get("client_id")
        client_address = request.data.get("client_address")

        if not client_id:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"client_id": ["Client is required."]},
                message="Client is required."
            )
        if not client_address:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"client_address": ["Client address is required."]},
                message="Client address is required."
            )

        shop_id = self.shop_id
        try:
            shop = Shop.objects.get(id=shop_id)
        except Shop.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"shop": ["Shop not found."]},
                message="Shop not found."
            )

        try:
            client_id = Client.objects.get(id=client_id, shop=shop)
        except Client.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"client_id": ["Client not found for this shop."]},
                message="Client not found."
            )

        # --- STEP 2: Dates
        pickup_date_str = request.data.get("pickup_date")
        return_date_str = request.data.get("return_date")
        cooling_period_end_str = request.data.get("cooling_period_end")

        if not pickup_date_str:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"pickup_date": ["Pickup date is required."]},
                message="Pickup date is required."
            )
        if not return_date_str:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"return_date": ["Return date is required."]},
                message="Return date is required."
            )

        try:
            pickup_date = make_aware(parse_date_string(pickup_date_str))
            return_date = make_aware(parse_date_string(return_date_str))
            if pickup_date.date() < date.today():
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"pickup_date": ["Pickup date cannot be in the past."]},
                    message="Pickup date cannot be in the past."
                )
            if return_date <= pickup_date:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"return_date": ["Return time must be after pickup time."]},
                    message="Return time must be after pickup time."
                )

            cooling_period_end = None
            if cooling_period_end_str:
                try:
                    cooling_period_end = make_aware(parse_date_string(cooling_period_end_str))
                    if cooling_period_end < return_date:
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
        except Exception:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"date": ["Invalid date format."]},
                message="Invalid date format."
            )

        # --- STEP 2B: Validate delivery status
        delivery_status = request.data.get("delivery_status", "booked")

        # Cannot set delivery status to "returned" for new bookings
        if delivery_status == "returned":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"delivery_status": ["Cannot set delivery status to 'returned' for a new booking."]},
                message="New bookings can't be marked as returned."
            )

        # Can only set delivery status to "delivered" if pickup date is today
        if delivery_status == "delivered" and pickup_date.date() != date.today():
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"delivery_status": ["Items can only be marked as delivered on the pickup date."]},
                message="Can set to 'delivered' only on pickup day."
            )

        # --- STEP 3: Product variants & stock check
        variants = request.data.get("variants")
        if not variants:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"variants": ["Product variants are required."]},
                message="Product variants are required."
            )

        variant_ids = [v["id"] for v in variants]

        try:
            valid_variants = (
                ProductVariant.objects.filter(
                    id__in=variant_ids, product__shop_service__shop=shop
                )
                .select_related("product__shop_service__shop")
                .values_list("id", flat=True)
            )

            invalid_ids = set(variant_ids) - set(valid_variants)
            if invalid_ids:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"invalid_variant_ids": list(invalid_ids)},
                    message="Some product variants are invalid."
                )

            stock_end_date = cooling_period_end if cooling_period_end else return_date
            stock_check = check_product_variant_availability(
                variants, pickup_date, stock_end_date
            )
            if stock_check:
                transaction.savepoint_rollback(savepoint)
                return stock_check

        except Exception as e:
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Product variant validation failed: {str(e)}")
            return self.error_response(
                message="Failed to validate product variants.",
                dev_message=str(e)
            )

        # --- STEP 4: Total and advance validation
        try:
            total_amount = sum([v.get("amount", 0) for v in variants])
            # Ensure total amount > 0
            if total_amount <= 0:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"total_amount": ["Total amount must be greater than zero."]},
                    message="Total amount must be greater than zero."
                )
        except Exception:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"variants": ["Invalid variant amounts."]},
                message="Invalid variant amounts."
            )

        # Discount validation
        discount = request.data.get("discount_amount", 0)
        try:
            discount = int(discount)
            if discount < 0:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"discount_amount": ["Discount cannot be negative."]},
                    message="Discount cannot be negative."
                )
            if discount > total_amount:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                    message="Discount amount cannot exceed total amount."
                )
        except (ValueError, TypeError):
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"discount_amount": ["Invalid discount amount."]},
                message="Invalid discount amount."
            )

        # Security validation
        security = request.data.get("security_amount", 0)
        try:
            security = int(security)
            if security < 0:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"security_amount": ["Security amount cannot be negative."]},
                    message="Security amount cannot be negative."
                )
        except Exception:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_amount": ["Invalid security amount."]},
                message="Invalid security amount."
            )

        # Advance validation
        advance = request.data.get("advance_amount", 0)
        try:
            advance = int(advance)
            if advance < 0:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"advance_amount": ["Advance amount must be positive."]},
                    message="Advance amount must be positive."
                )
            final_payable = total_amount - discount
            if advance > final_payable:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"advance_amount": ["Advance cannot be more than final total amount."]},
                    message="Advance cannot be more than total amount."
                )
        except Exception:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"advance_amount": ["Invalid advance amount."]},
                message="Invalid advance amount."
            )

        # --- STEP 5: Validate other fields
        details = request.data.get("details")
        if details is not None and not isinstance(details, dict):
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"details": ["Details must be a valid JSON object."]},
                message="Invalid details format."
            )

        # --- STEP 6: Create Booking
        try:
            booking = BookingV2.objects.create(
                user=request.user,
                shop=shop,
                client_identity=client_id,
                client_address=client_address,
                pickup_date=pickup_date,
                return_date=return_date,
                cooling_period_end=cooling_period_end,
                total_amount=total_amount,
                advance_amount=advance,
                discount_amount=discount,
                security_amount=security,
                service_id=request.data.get("service_id"),
                purchase_mode=request.data.get("purchase_mode"),
                payment_status=(advance >= (total_amount - discount)),
                delivery_status=delivery_status,
                staff_name=request.data.get("staff_name"),
                details=details
            )
        except Exception as e:
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Failed to create booking: {str(e)}")
            return self.error_response(
                message="Failed to create booking.",
                dev_message=str(e)
            )

        # --- STEP 7: Create Booked Items (with measurements) ---
        try:
            for item in variants:
                variant = ProductVariant.objects.get(id=item["id"])
                measurements = item.get("measurements", None)
                if isinstance(measurements, dict):
                    measurements = [{"name": k, "value": v} for k, v in measurements.items()]
                BookedProductVariant.objects.create(
                    booking=booking,
                    product_variant=variant,
                    amount=item["amount"],
                    quantity=item.get("quantity", 1),
                    user=request.user,
                    shop=shop,
                    measurements=measurements
                )
        except Exception as e:
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Failed to create booked variants for booking {booking.id}: {str(e)}")
            return self.error_response(
                message="Failed to create booked items.",
                dev_message=str(e)
            )

        # --- STEP 8: Add availability
        try:
            add_product_variant_availability(
                variants, pickup_date.date(), stock_end_date.date()
            )
        except Exception as e:
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Failed to update availability for booking {booking.id}: {str(e)}")
            return self.error_response(
                message="Failed to update product availability.",
                dev_message=str(e)
            )

        # --- STEP 9: Client Requirement
        description = request.data.get("description", "")
        if description:
            try:
                ClientRequirementV2.objects.create(
                    shop=shop, booking=booking, description=description
                )
            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Failed to create client requirement for booking {booking.id}: {str(e)}")
                return self.error_response(
                    message="Failed to create client requirement.",
                    dev_message=str(e)
                )

        # --- STEP 10: Payment Details
        if advance > 0:
            payment_method = request.data.get("payment_method", "cash")
            try:
                PaymentDetailsV2.objects.create(
                    booking=booking,
                    payment_amount=advance,
                    payment_method=payment_method,
                )
            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Failed to create payment record for booking {booking.id}: {str(e)}")
                return self.error_response(
                    message="Failed to create payment record.",
                    dev_message=str(e)
                )

        # Commit the savepoint - all operations successful
        transaction.savepoint_commit(savepoint)

        return self.success_response(
            data={"booking_id": booking.id},
            message="Booking created successfully."
        )

    except Exception as e:
        # Rollback savepoint on any unexpected error
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Booking create error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the booking.",
            dev_message=str(e)
        )
