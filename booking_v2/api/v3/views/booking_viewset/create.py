from datetime import datetime, date
from django.db import transaction
from booking_v2.models import (
    PaymentDetailsV2,
    ClientRequirementV2,
    BookingV2,
    BookedProductVariant,
    Client,
)
from services.models import ProductVariant
from shop.models import Shop
from django.utils.timezone import make_aware
from booking_v2.api.v3.utils.availability import (
    check_product_variant_availability,
    add_product_variant_availability,
)
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


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
    summary="Create booking",
    description=(
        "Creates a new booking for an existing client and reserves product variant availability.\n\n"
        "Purpose: Main API to create future bookings with pricing, payment, and optional requirement notes.\n\n"
        "Context: Use in booking create flow after choosing shop/client, schedule, and variants."
    ),
    request=inline_serializer(
        name="BookingCreateRequestV3",
        fields={
            "client_id": serializers.IntegerField(help_text="Purpose: Identifies existing client in active shop. Location: request body. Format/Type: integer. Required."),
            "client_address": serializers.CharField(help_text="Purpose: Stores address for booking reference. Location: request body. Format/Type: string. Required."),
            "pickup_date": serializers.CharField(help_text="Purpose: Pickup datetime for booking start. Location: request body. Format/Type: string; accepted DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Required."),
            "return_date": serializers.CharField(help_text="Purpose: Return datetime for booking end. Location: request body. Format/Type: string; accepted DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Required."),
            "delivery_status": serializers.CharField(required=False, default="booked", help_text="Purpose: Initial delivery state. Location: request body. Format/Type: string. Optional. Default: booked."),
            "variants": serializers.ListField(child=serializers.DictField(), help_text="Purpose: Selected product variants with id, amount, quantity, optional measurements. Location: request body. Format/Type: array<object>. Required."),
            "discount_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Discount deducted from total. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."),
            "security_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Security deposit amount. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."),
            "advance_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Upfront payment amount. Location: request body. Format/Type: integer >= 0 and <= payable total. Optional. Default: 0."),
            "payment_method": serializers.CharField(required=False, default="cash", help_text="Purpose: Method for initial advance payment record. Location: request body. Format/Type: string. Optional. Default: cash."),
            "service_id": serializers.IntegerField(required=False, help_text="Purpose: Links booking to shop service. Location: request body. Format/Type: integer. Optional."),
            "purchase_mode": serializers.CharField(required=False, help_text="Purpose: Booking business mode marker. Location: request body. Format/Type: string. Optional."),
            "staff_name": serializers.CharField(required=False, help_text="Purpose: Captures assigned staff name. Location: request body. Format/Type: string. Optional."),
            "description": serializers.CharField(required=False, allow_blank=True, help_text="Purpose: Client requirement notes. Location: request body. Format/Type: string. Optional."),
            "details": serializers.DictField(required=False, help_text="Purpose: Stores additional structured metadata. Location: request body. Format/Type: object. Optional."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success exact message: Booking created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors exact messages include: Client is required.; Client address is required.; Client not found.; "
                "Pickup date is required.; Return date is required.; Pickup date cannot be in the past.; Return time must be after pickup time.; "
                "Invalid date format.; New bookings can't be marked as returned.; Can set to 'delivered' only on pickup day.; "
                "Product variants are required.; Some product variants are invalid.; Total amount must be greater than zero.; "
                "Discount cannot be negative.; Discount amount cannot exceed total amount.; Invalid discount amount.; "
                "Security amount cannot be negative.; Invalid security amount.; Advance amount must be positive.; "
                "Advance cannot be more than total amount.; Invalid advance amount."
            )
        ),
        500: OpenApiResponse(description="Server error exact message: Something went wrong while creating the booking."),
    },
    examples=[
        OpenApiExample(
            "Create booking request",
            value={
                "client_id": 12,
                "client_address": "Main Road, Surat",
                "pickup_date": "20-05-2026T10:00:00",
                "return_date": "22-05-2026T10:00:00",
                "variants": [{"id": 101, "amount": 1500, "quantity": 1}],
                "discount_amount": 100,
                "security_amount": 500,
                "advance_amount": 500,
                "payment_method": "cash"
            },
            request_only=True,
        )
    ],
)
def create(self, request):
    try:

        # --- STEP 1: Validate Client Identity
        client_id = request.data.get("client_id")
        client_address = request.data.get("client_address")

        if not client_id:
            return self.validation_error_response(
                errors={"client_id": ["Client is required."]},
                message="Client is required."
            )
        if not client_address:
            return self.validation_error_response(
                errors={"client_address": ["Client address is required."]},
                message="Client address is required."
            )

        shop_id = self.shop_id
        shop = Shop.objects.get(id=shop_id)

        try:
            client_id = Client.objects.get(id=client_id, shop=shop)
        except Client.DoesNotExist:
            return self.validation_error_response(
                errors={"client_id": ["Client not found for this shop."]},
                message="Client not found."
            )

        # --- STEP 2: Dates
        pickup_date_str = request.data.get("pickup_date")
        return_date_str = request.data.get("return_date")

        if not pickup_date_str:
            return self.validation_error_response(
                errors={"pickup_date": ["Pickup date is required."]},
                message="Pickup date is required."
            )
        if not return_date_str:
            return self.validation_error_response(
                errors={"return_date": ["Return date is required."]},
                message="Return date is required."
            )

        try:
            pickup_date = make_aware(parse_date_string(pickup_date_str))
            return_date = make_aware(parse_date_string(return_date_str))
            if pickup_date.date() < date.today():
                return self.validation_error_response(
                    errors={"pickup_date": ["Pickup date cannot be in the past."]},
                    message="Pickup date cannot be in the past."
                )
            if return_date <= pickup_date:
                return self.validation_error_response(
                    errors={"return_date": ["Return time must be after pickup time."]},
                    message="Return time must be after pickup time."
                )
        except Exception:
            return self.validation_error_response(
                errors={"date": ["Invalid date format."]},
                message="Invalid date format."
            )

        # --- STEP 2B: Validate delivery status
        delivery_status = request.data.get("delivery_status", "booked")

        # Cannot set delivery status to "returned" for new bookings
        if delivery_status == "returned":
            return self.validation_error_response(
                errors={"delivery_status": ["Cannot set delivery status to 'returned' for a new booking."]},
                message="New bookings can't be marked as returned."
            )

        # Can only set delivery status to "delivered" if pickup date is today
        if delivery_status == "delivered" and pickup_date.date() != date.today():
            return self.validation_error_response(
                errors={"delivery_status": ["Items can only be marked as delivered on the pickup date."]},
                message="Can set to 'delivered' only on pickup day."
            )

        # --- STEP 3: Product variants & stock check
        variants = request.data.get("variants")
        if not variants:
            return self.validation_error_response(
                errors={"variants": ["Product variants are required."]},
                message="Product variants are required."
            )

        variant_ids = [v["id"] for v in variants]

        valid_variants = (
            ProductVariant.objects.filter(
                id__in=variant_ids, product__shop_service__shop=shop
            )
            .select_related("product__shop_service__shop")
            .values_list("id", flat=True)
        )

        invalid_ids = set(variant_ids) - set(valid_variants)
        if invalid_ids:
            return self.validation_error_response(
                errors={"invalid_variant_ids": list(invalid_ids)},
                message="Some product variants are invalid."
            )

        stock_check = check_product_variant_availability(
            variants, pickup_date, return_date
        )
        if stock_check:
            return stock_check

        # --- STEP 4: Total and advance validation
        total_amount = sum([v.get("amount", 0) for v in variants])
        # Ensure total amount > 0
        if total_amount <= 0:
            return self.validation_error_response(
                errors={"total_amount": ["Total amount must be greater than zero."]},
                message="Total amount must be greater than zero."
            )

        # Discount
        discount = request.data.get("discount_amount", 0)
        try:
            discount = int(discount)
            if discount < 0:
                return self.validation_error_response(
                    errors={"discount_amount": ["Discount cannot be negative."]},
                    message="Discount cannot be negative."
                )
            if discount > total_amount:
                return self.validation_error_response(
                    errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                    message="Discount amount cannot exceed total amount."
                )
        except (ValueError, TypeError):
            return self.validation_error_response(
                errors={"discount_amount": ["Invalid discount amount."]},
                message="Invalid discount amount."
            )

        # Security
        security = request.data.get("security_amount", 0)
        try:
            security = int(security)
            if security < 0:
                return self.validation_error_response(
                    errors={"security_amount": ["Security amount cannot be negative."]},
                    message="Security amount cannot be negative."
                )
        except Exception:
            return self.validation_error_response(
                errors={"security_amount": ["Invalid security amount."]},
                message="Invalid security amount."
            )

        advance = request.data.get("advance_amount", 0)

        try:
            advance = int(advance)
            if advance < 0:
                return self.validation_error_response(
                    errors={"advance_amount": ["Advance amount must be positive."]},
                    message="Advance amount must be positive."
                )
            if advance > (total_amount - discount):
                return self.validation_error_response(
                    errors={"advance_amount": ["Advance cannot be more than final total amount."]},
                    message="Advance cannot be more than total amount."
                )
        except Exception:
            return self.validation_error_response(
                errors={"advance_amount": ["Invalid advance amount."]},
                message="Invalid advance amount."
            )

        # --- STEP 6: Create Booking
        details = request.data.get("details")
        booking = BookingV2.objects.create(
            user=request.user,
            shop=shop,
            client_identity=client_id,
            client_address=client_address,
            pickup_date=pickup_date,
            return_date=return_date,
            total_amount=total_amount,
            advance_amount=advance,
            discount_amount=discount,
            security_amount=security,
            service_id=request.data.get("service_id"),
            purchase_mode=request.data.get("purchase_mode"),
            payment_status=(advance == total_amount - discount),
            delivery_status=delivery_status,
            staff_name=request.data.get("staff_name"),
            details=details if isinstance(details, dict) else None
        )

        # --- STEP 7: Booked Items (with measurements) ---
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

        # --- STEP 8: Add availability
        add_product_variant_availability(
            variants, pickup_date.date(), return_date.date()
        )

        # --- STEP 9: Client Requirement
        description = request.data.get("description", "")
        if description:
            ClientRequirementV2.objects.create(
                shop=shop, booking=booking, description=description
            )

        # --- STEP 10: Payment Details
        if advance > 0:
            PaymentDetailsV2.objects.create(
                booking=booking,
                payment_amount=advance,
                payment_method=request.data.get("payment_method", "cash"),
            )

        return self.success_response(
            data={"booking_id": booking.id},
            message="Booking created successfully."
        )

    except Exception as e:
        logger.error(f"Booking create error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the booking.",
            dev_message=str(e)
        )
