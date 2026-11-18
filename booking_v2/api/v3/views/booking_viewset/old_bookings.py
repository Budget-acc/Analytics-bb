from datetime import datetime
from django.db import transaction
from django.utils.timezone import make_aware, now
from booking_v2.models import (
    PaymentDetailsV2,
    ClientRequirementV2,
    BookingV2,
    BookedProductVariant,
    Client,
)
from services.models import ProductVariant
from shop.models import Shop, Staff
from rest_framework.decorators import action
from shop.api.admin_v1.utils.shop import get_active_shop_id
from pytz import timezone as pytz_timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
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
    summary="Create historical booking",
    description=(
        "Creates an already completed/delivered historical booking entry.\n\n"
        "Purpose: Backfill old/manual bookings into the system with payment and ledger integration.\n\n"
        "Context: Supports either existing client_id or inline client object create/get behavior.\n\n"
        "Tax handling: Unlike the v5 Booking API, this endpoint does not derive tax from the "
        "shop's tax configuration. The frontend supplies the final tax_amount directly, which is "
        "saved as-is; is_tax_applied is set to true only when tax_amount > 0. No other tax fields "
        "(subtotal, tax_rate, tax_name, tax_calculation_type, taxable_components) are populated.\n\n"
        "Amount calculation (this endpoint has no additional-charges concept): "
        "total_amount is saved as product total + tax_amount (before discount); the discount is "
        "stored separately in discount_amount. The payable amount is "
        "total_amount_after_discount = total_amount - discount_amount, and advance_amount cannot "
        "exceed it. taxable_amount (= product total - discount_amount) is saved only when "
        "tax_amount is explicitly included in the request body; if tax_amount is omitted "
        "entirely, taxable_amount is left unset.\n\n"
        "Payment status: payment_status is true when advance_amount equals "
        "total_amount_after_discount (fully paid), otherwise false.\n\n"
        "Completion date: completed_at is set only when booking_status is 'completed' AND the "
        "booking is fully paid (payment_status true). It uses the completed_at value from the "
        "request when provided - which must be equal to or later than return_date and not in the "
        "future; otherwise it falls back to the booking's return_date (or the "
        "current time if return_date is in the future). For any other booking completed_at stays "
        "null and a completed_at sent in the request is ignored.\n\n"
        "Staff: the booking's staff is linked by staff_id (a Staff of the active shop). The old "
        "free-text staff_name field is no longer accepted or saved."
    ),
    request=inline_serializer(
        name="OldBookingCreateRequestV3",
        fields={
            "pickup_date": serializers.CharField(help_text="Purpose: Historical pickup datetime. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Required."),
            "return_date": serializers.CharField(help_text="Purpose: Historical return datetime. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Required."),
            "variants": serializers.ListField(child=serializers.DictField(), help_text="Purpose: Booked variants list with id, amount, quantity, optional measurements. Location: request body. Format/Type: array<object>. Required."),
            "client_id": serializers.IntegerField(required=False, help_text="Purpose: Uses existing client in shop. Location: request body. Format/Type: integer. Optional (required if client object absent)."),
            "client": serializers.DictField(required=False, help_text="Purpose: Client payload for get-or-create flow (client_name, phone_1_e164, phone_2_e164). Location: request body. Format/Type: object. Optional (required if client_id absent)."),
            "client_address": serializers.CharField(required=False, allow_blank=True, help_text="Purpose: Stores client address for booking reference. Location: request body. Format/Type: string. Optional."),
            "booked_date": serializers.CharField(required=False, help_text="Purpose: Overrides booking created timestamp date/time. Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Optional."),
            "discount_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Historical discount amount. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."),
            "security_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Historical security amount. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."),
            "tax_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Historical tax amount, supplied directly by the frontend (not recalculated server-side from the shop's tax configuration). Added to taxable_amount (product total minus discount) to form total_amount; sets is_tax_applied=true when > 0. Location: request body. Format/Type: integer >= 0. Optional. Default: 0."),
            "advance_amount": serializers.IntegerField(required=False, default=0, help_text="Purpose: Historical paid advance. Location: request body. Format/Type: integer >= 0 and <= final total. Optional. Default: 0."),
            "payment_method": serializers.CharField(required=False, default="cash", help_text="Purpose: Payment mode used while recording advance. Location: request body. Format/Type: string. Optional. Default: cash."),
            "account_id": serializers.IntegerField(required=False, help_text="Purpose: Optional finance account for ledger entry. Location: request body. Format/Type: integer. Optional."),
            "service_id": serializers.IntegerField(required=False, help_text="Purpose: Service linkage for booking. Location: request body. Format/Type: integer. Optional."),
            "purchase_mode": serializers.CharField(required=False, default="normal", help_text="Purpose: Business purchase mode marker. Location: request body. Format/Type: string. Optional. Default: normal."),
            "delivery_status": serializers.CharField(required=False, default="delivered", help_text="Purpose: Initial delivery status for old booking. Location: request body. Format/Type: string. Optional. Default: delivered."),
            "booking_status": serializers.CharField(required=False, default="completed", help_text="Purpose: Booking status of the old booking; 'completed' together with full payment sets completed_at. Location: request body. Format/Type: string. Allowed values: upcoming, completed. Optional. Default: completed."),
            "completed_at": serializers.CharField(required=False, help_text="Purpose: Date/time the old booking was completed. Applied only when booking_status is 'completed' and advance_amount equals total_amount_after_discount (fully paid); otherwise ignored. If omitted for such a booking, the booking's return_date is used (or the current time if return_date is in the future). Location: request body. Format/Type: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS. Must be equal to or later than return_date and cannot be a future date (date-only values compare by date, so the return day itself is accepted). Optional."),
            "staff_id": serializers.IntegerField(required=False, help_text="Purpose: Staff member who handled the booking; saved in the booking's staff field. Must be a non-deleted Staff of the active shop. Location: request body. Format/Type: integer. Optional - if omitted, no staff is linked."),
            "description": serializers.CharField(required=False, allow_blank=True, help_text="Purpose: Client requirement note. Location: request body. Format/Type: string. Optional."),
            "details": serializers.DictField(required=False, help_text="Purpose: Additional structured metadata. Location: request body. Format/Type: object. Optional."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success exact message: Booking created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation/business errors exact messages include: Pickup date is required.; Return date is required.; Variants is required.; "
                "Client information is required.; Client not found.; Return time must be after pickup time.; Invalid date format.; "
                "Invalid booked date format.; Product variants are required.; Some product are invalid.; Total amount must be greater than zero.; "
                "Discount cannot be negative.; Discount amount cannot exceed total amount.; Invalid discount amount.; "
                "Security amount cannot be negative.; Invalid security amount.; Tax amount cannot be negative.; "
                "Invalid tax amount.; Advance amount must be positive.; "
                "Advance cannot be more than final total amount.; Invalid advance amount.; Invalid account selected.; "
                "Booked date cannot be a future date.; Booked date cannot be later than the pickup date.; "
                "Invalid staff.; Staff not found.; Invalid booking status.; Invalid completed date format.; "
                "Completed date cannot be a future date.; Completed date cannot be earlier than the return date."
            )
        ),
        500: OpenApiResponse(description="Server error exact message: Something went wrong while creating the booking."),
    },
    examples=[
        OpenApiExample(
            "Old booking request",
            value={
                "client_id": 12,
                "pickup_date": "10-04-2026T10:00:00",
                "return_date": "12-04-2026T10:00:00",
                "variants": [{"id": 101, "amount": 1500, "quantity": 1}],
                "discount_amount": 200,
                "tax_amount": 234,
                "advance_amount": 1534,
                "booking_status": "completed",
                "delivery_status": "returned",
                "staff_id": 7,
                "completed_at": "12-04-2026T18:30:00"
            },
            request_only=True,
        )
    ],
)
@action(detail=False, methods=["post"], url_path="old-bookings")
def old_bookings(self, request):
    try:
        required_fields = ["pickup_date", "return_date", "variants"]
        for field in required_fields:
            if not request.data.get(field):
                return self.validation_error_response(
                    errors={field: [f"{field.replace('_', ' ').capitalize()} is required."]},
                    message=f"{field.replace('_', ' ').capitalize()} is required."
                )

        shop_id = get_active_shop_id(request)
        shop = Shop.objects.get(id=shop_id)

        # --- STEP 1: Validate Client (client_id OR client object)

        client_id_value = request.data.get("client_id")
        client_payload = request.data.get("client")

        client_address = request.data.get("client_address")

        # if not client_address:
        #     return self.validation_error_response(
        #         errors={"client_address": ["Client address is required."]},
        #         message="Client address is required."
        #     )

        if not client_id_value and not client_payload:
            return self.validation_error_response(
                errors={"client": ["Either client_id or client details are required."]},
                message="Client information is required."
            )
        # The client itself is resolved (and possibly created) in STEP 6.4,
        # after every validation - an early `return` doesn't roll back the
        # atomic block, so no write may happen before the last check.

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
            if return_date < pickup_date:
                return self.validation_error_response(
                    errors={"return_date": ["Return time must be after pickup time."]},
                    message="Return time must be after pickup time."
                )
        except Exception:
            return self.validation_error_response(
                errors={"date": ["Invalid date format."]},
                message="Invalid date format."
            )

        # --- STEP 3: Optional booked date
        booked_date_str_raw = request.data.get("booked_date")
        booked_date_is_date_only = False
        if booked_date_str_raw:
            # Detect whether the caller supplied only a date (no time component)
            try:
                datetime.strptime(booked_date_str_raw, "%d-%m-%Y")
                booked_date_is_date_only = True
            except ValueError:
                pass  # datetime format — leave flag False
        try:
            booked_date = make_aware(parse_date_string(booked_date_str_raw)) if booked_date_str_raw else pickup_date
        except Exception:
            return self.validation_error_response(
                errors={"booked_date": ["Invalid booked date format."]},
                message="Invalid booked date format."
            )

        # Validate booked_date is not a future date.
        # When date-only, compare only date portions so today is always accepted.
        eff_for_future  = booked_date.date() if booked_date_is_date_only else booked_date
        now_for_future  = now().date()        if booked_date_is_date_only else now()
        if eff_for_future > now_for_future:
            return self.validation_error_response(
                errors={"booked_date": ["booked_date cannot be a future date."]},
                message="Booked date cannot be a future date.",
            )

        # Validate booked_date ≤ pickup_date; date-only → compare dates only.
        eff_cmp = booked_date.date() if booked_date_is_date_only else booked_date
        pu_cmp  = pickup_date.date() if booked_date_is_date_only else pickup_date
        if eff_cmp > pu_cmp:
            return self.validation_error_response(
                errors={"booked_date": ["booked_date cannot be later than the pickup date."]},
                message="Booked date cannot be later than the pickup date.",
            )

        # --- STEP 4: Product variants & stock check
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
                message="Some product are invalid."
            )

        # --- STEP 5: Total and advance validation
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
        except Exception:
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

        # --- STEP 5.5: Tax amount (supplied directly by the frontend) ---
        # Unlike the v5 Booking API, this endpoint does not derive tax from the
        # shop's tax configuration - the frontend already has the correct
        # historical tax amount, which is trusted and saved as-is.
        tax_amount_provided = "tax_amount" in request.data
        tax_amount = request.data.get("tax_amount", 0)
        try:
            tax_amount = int(tax_amount)
            if tax_amount < 0:
                return self.validation_error_response(
                    errors={"tax_amount": ["Tax amount cannot be negative."]},
                    message="Tax amount cannot be negative."
                )
        except Exception:
            return self.validation_error_response(
                errors={"tax_amount": ["Invalid tax amount."]},
                message="Invalid tax amount."
            )

        # No additional-charges concept in this endpoint, so the taxable base is
        # the product total after discount. total_amount below already reflects
        # the discount via taxable_amount, so it must not be deducted again.
        # taxable_amount is only saved when the request explicitly includes
        # tax_amount - otherwise it's left unset (model default).
        taxable_amount = total_amount - discount
        is_tax_applied = tax_amount > 0
        total_amount = total_amount + tax_amount
        total_amount_after_discount = total_amount - discount


        advance = request.data.get("advance_amount", 0)

        try:
            advance = int(advance)
            if advance < 0:
                return self.validation_error_response(
                    errors={"advance_amount": ["Advance amount must be positive."]},
                    message="Advance amount must be positive."
                )
            if advance > total_amount_after_discount:
                return self.validation_error_response(
                    errors={"advance_amount": ["Advance cannot be more than final total amount."]},
                    message="Advance cannot be more than final total amount."
                )
        except Exception:
            return self.validation_error_response(
                errors={"advance_amount": ["Invalid advance amount."]},
                message="Invalid advance amount."
            )

        # --- STEP 6: Validate account_id (optional)
        payment_account = None
        payment_method = request.data.get("payment_method", "cash")
        account_id = request.data.get("account_id")
        if account_id:
            from finance.models import Account as _Account
            try:
                payment_account = _Account.objects.get(
                    id=account_id,
                    shop_id=shop.id,
                    deleted__isnull=True,
                    is_active=True,
                )
                payment_method = 'cash' if payment_account.account_type == 'cash' else 'upi'
            except _Account.DoesNotExist:
                return self.validation_error_response(
                    errors={'account_id': ['Invalid or inactive account.']},
                    message='Invalid account selected.'
                )

        # --- STEP 6.1: Validate staff_id (optional) - replaces the old free-text staff_name
        staff = None
        staff_id = request.data.get("staff_id")
        if staff_id not in (None, ""):
            try:
                staff_id = int(staff_id)
            except (TypeError, ValueError):
                return self.validation_error_response(
                    errors={"staff_id": ["Staff id must be an integer."]},
                    message="Invalid staff."
                )
            staff = Staff.objects.filter(id=staff_id, shop=shop, is_deleted=False).first()
            if staff is None:
                return self.validation_error_response(
                    errors={"staff_id": ["Staff not found for this shop."]},
                    message="Staff not found."
                )

        # --- STEP 6.2: Validate booking_status
        booking_status = request.data.get("booking_status") or "completed"
        valid_booking_statuses = [choice for choice, _ in BookingV2.BOOKING_STATUS_CHOICES]
        if booking_status not in valid_booking_statuses:
            return self.validation_error_response(
                errors={"booking_status": [f"Allowed values: {', '.join(valid_booking_statuses)}."]},
                message="Invalid booking status."
            )

        # --- STEP 6.3: Payment status and completed_at
        # Fully paid when the advance covers the payable amount after discount
        # (advance can never exceed it - validated above).
        payment_status = advance == total_amount_after_discount

        # completed_at only applies to completed, fully paid bookings - the
        # provided date, otherwise the return date (never in the future).
        # A completed_at sent for any other booking is ignored.
        completed_at = None
        if booking_status == "completed" and payment_status:
            completed_at_str = request.data.get("completed_at")
            if completed_at_str:
                try:
                    completed_at = make_aware(parse_date_string(completed_at_str))
                except Exception:
                    return self.validation_error_response(
                        errors={"completed_at": ["Invalid format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."]},
                        message="Invalid completed date format."
                    )
                # Date-only values compare by date, like booked_date above.
                completed_is_date_only = "T" not in completed_at_str
                completed_cmp = completed_at.date() if completed_is_date_only else completed_at
                if completed_cmp > (now().date() if completed_is_date_only else now()):
                    return self.validation_error_response(
                        errors={"completed_at": ["completed_at cannot be a future date."]},
                        message="Completed date cannot be a future date."
                    )
                # A booking can't be completed before its return date
                # (return_date >= pickup_date is already validated).
                if completed_cmp < (return_date.date() if completed_is_date_only else return_date):
                    return self.validation_error_response(
                        errors={"completed_at": ["completed_at cannot be earlier than the return date."]},
                        message="Completed date cannot be earlier than the return date."
                    )
                # A date-only value parses to midnight; on the return day that
                # would be stored before the return time - never save it earlier.
                if completed_is_date_only:
                    completed_at = max(completed_at, return_date)
            else:
                completed_at = min(return_date, now())

        # --- STEP 6.4: Resolve the client (client_id OR client object) - the
        # last step before any write, so a failed validation above never
        # leaves a newly created client behind.
        # Case 1: Use existing client_id
        if client_id_value:
            try:
                client_obj = Client.objects.get(id=client_id_value, shop=shop)
            except Client.DoesNotExist:
                return self.validation_error_response(
                    errors={"client_id": ["Client not found for this shop."]},
                    message="Client not found."
                )

        # Case 2: Use client object → get or create
        else:
            client_data = {
                "client_name": client_payload.get("client_name"),
                "phone_1_e164": client_payload.get("phone_1_e164"),
                "phone_2_e164": client_payload.get("phone_2_e164"),
            }

            client_obj, created, error_msg = self._get_or_create_client(
                shop_id, client_data
            )

            if error_msg:
                return self.validation_error_response(
                    errors={"client": [error_msg]},
                    message=error_msg
                )

        # --- STEP 7: Create Booking
        details = request.data.get("details")
        booking = BookingV2.objects.create(
            user=request.user,
            shop=shop,
            # client_identity=client_id,
            client_identity=client_obj,
            client_address=client_address,
            pickup_date=pickup_date,
            return_date=return_date,
            total_amount=total_amount,
            advance_amount=advance,
            discount_amount=discount,
            security_amount=security,
            service_id=request.data.get("service_id"),
            # total_amount is saved pre-discount (product total + tax); the
            # payable amount is total_amount_after_discount.
            payment_status=payment_status,
            purchase_mode=request.data.get("purchase_mode", "normal"),
            delivery_status=request.data.get("delivery_status", "delivered"),
            booking_status=booking_status,
            staff=staff,
            completed_at=completed_at,
            details=details if isinstance(details, dict) else None,
            created_at=booked_date,
            tax_amount=tax_amount,
            is_tax_applied=is_tax_applied,
            **({"taxable_amount": taxable_amount} if tax_amount_provided else {}),
        )

        # --- STEP 8: Booked Items (with measurements) ---
        # One query for all variants (already validated to belong to the shop).
        variants_by_id = ProductVariant.objects.in_bulk(variant_ids)
        for item in variants:
            measurements = item.get("measurements", None)
            BookedProductVariant.objects.create(
                booking=booking,
                product_variant=variants_by_id[item["id"]],
                amount=item["amount"],
                quantity=item.get("quantity", 1),
                user=request.user,
                shop=shop,
                measurements=measurements
            )

        # --- STEP 9: Client Requirement
        description = request.data.get("description", "")
        if description:
            ClientRequirementV2.objects.create(
                shop=shop, booking=booking, description=description
            )

        # --- STEP 10: Payment Details
        if advance > 0:
            from django.utils import timezone as tz
            india_now = tz.now().astimezone(india)
            payment_date = booked_date.date()
            current_time = india_now.time()

            # Combine date with IST time
            payment_datetime = datetime.combine(payment_date, current_time)

            # Make timezone-aware in India timezone
            payment_timestamp = india.localize(payment_datetime)

            record = PaymentDetailsV2.objects.create(
                booking=booking,
                payment_amount=advance,
                payment_method=payment_method,
                account=payment_account,
                created_at=payment_timestamp
            )

            if payment_account:
                from finance.api.v3.utils.create_ledger_entries import create_ledger_entries
                create_ledger_entries(
                    payments=[{
                        'account': payment_account,
                        'amount': advance,
                        'direction': 'in',
                        'payment_method': payment_method,
                        'reference_id': record.id,
                        'created_at': payment_timestamp,
                    }],
                    shop=shop,
                    transaction_type='booking_payment',
                    reference_type='PaymentDetailsV2',
                )

        return self.success_response(
            data={"booking_id": booking.id},
            message="Booking created successfully."
        )

    except Exception as e:
        # The exception is swallowed here, so @transaction.atomic would commit
        # whatever was already written (e.g. a booking and payment without its
        # ledger entries). Mark the transaction for rollback so it's all-or-nothing.
        transaction.set_rollback(True)
        logger.error(f"Old booking create error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the booking.",
            dev_message=str(e),
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
