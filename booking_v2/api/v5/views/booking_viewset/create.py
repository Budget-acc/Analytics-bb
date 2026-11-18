import logging
from datetime import datetime as _datetime
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.timezone import now as _now, make_aware as _make_aware, localtime as _localtime
from shop.models import Shop
from booking_v2.models import BookingV2
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from shop.api.v3.utils.tax_calculation import (
    calculate_booking_tax_v2, get_transaction_tax_config, resolve_apply_tax,
)
from ERP.utils.s3_image_uploads import SYNC_DOCUMENT_UPLOAD_COUNT

logger = logging.getLogger("api_requests")

MEASUREMENTS_HELP = (
    "Each item: id, amount, quantity and optional measurements. "
    "measurements (object) accepts either format: flat key-value pairs, e.g. "
    '{"chest": "46", "sleeve_length": "4"}, or measurements grouped per person, e.g. '
    '{"Person 1": {"chest": "46", "sleeve_length": "4"}, "Person 2": {"chest": "40"}}. '
    "Values are stored as sent, and the booking details API returns the same structure "
    "in the same key order."
)


@extend_schema(
    tags=["Booking Management V5"],
    summary="Create booking",
    description=(
        "Creates a booking with client, variant, amount, charge, payment, and optional document details. "
        "Supports JSON and multipart requests."
    ),
    request=inline_serializer(
        name="BookingCreateV5Request",
        fields={
            "client_id": serializers.IntegerField(required=False),
            "client": serializers.DictField(required=False),
            "pickup_date": serializers.CharField(
                required=True,
                help_text=(
                    "Cannot be earlier than the booking date: booked_date when given, otherwise today. "
                    "Compared by calendar date, so a same-day pickup is always allowed."
                ),
            ),
            "return_date": serializers.CharField(required=True),
            "variants": serializers.ListField(
                child=serializers.DictField(),
                required=True,
                help_text=MEASUREMENTS_HELP,
            ),
            "discount_amount": serializers.IntegerField(required=False),
            "security_amount": serializers.IntegerField(required=False),
            "security_amount_is_paid": serializers.BooleanField(required=False),
            "advance_amount": serializers.IntegerField(required=False),
            "payment_method": serializers.CharField(required=False),
            "account_id": serializers.IntegerField(required=False),
            "security_account_id": serializers.IntegerField(
                required=False,
                help_text=(
                    "Account for the security deposit. Required when security_amount_is_paid=true "
                    "(else a validation error is returned) - ignored when security_amount_is_paid=false. "
                    "payment_method for the security deposit is never accepted from the request; it is "
                    "always derived from this account's account_type (cash -> cash, bank -> upi)."
                ),
            ),
            "security_paid_at": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Optional date/time the security deposit was paid - only used when "
                    "security_amount_is_paid=true (ignored otherwise). "
                    "Formats: DD-MM-YYYY (current time is used as the time component) or DD-MM-YYYYTHH:MM:SS. "
                    "Cannot be a future date and cannot be earlier than the booking created date (booked_date). "
                    "If omitted, defaults to booked_date when given, else the current date and time."
                ),
            ),
            "description": serializers.CharField(required=False),
            "purchase_mode": serializers.ChoiceField(
                choices=["normal", "package", "courier", "pickup"],
                required=False,
                help_text="Delivery/fulfillment mode for this booking. Defaults to 'normal'."
            ),
            "additional_charges": serializers.ListField(child=serializers.DictField(), required=False),
            "apply_tax": serializers.BooleanField(
                required=False,
                default=True,
                help_text=(
                    "Purpose: Turns tax calculation on or off for this transaction only. "
                    "Location: request body. Format/Type: boolean (true/false; multipart: \"true\"/\"false\"). "
                    "true (default): the shop's active tax configuration is applied as usual. "
                    "false: no tax is applied to this transaction (is_tax_applied=false); the shop's tax "
                    "configuration and other transactions are not affected. Optional. Default: true."
                ),
            ),
            "booked_date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Optional historical booking date. "
                    "Formats: DD-MM-YYYY (e.g. 20-06-2026) or DD-MM-YYYYTHH:MM:SS (e.g. 20-06-2026T10:30:00). "
                    "When only a date is provided, the current server time is used as the time component "
                    "so the timestamp is never midnight. "
                    "Cannot be a future date. "
                    "If omitted, defaults to the current date and time. "
                    "Affects booking created_at, payment records, ledger entries, and daily balance calculations."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Booking created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation/business errors from helper validations. Exact messages include:\n"
                "- Shop not found.\n"
                "- Failed to set rental duration type.\n"
                "- Failed to validate product variants.\n"
                "- Invalid product selected.\n"
                "- Some product variants are invalid.\n"
                "- The selected product has been deleted and cannot be used for a booking.\n"
                "- Invalid variant amounts.\n"
                "- Pickup date cannot be earlier than the booking date.\n"
                "- Booked date cannot be later than the pickup date.\n"
                "- account is required when security amount is marked as paid.\n"
                "- Invalid security_paid_at format.\n"
                "- Security paid date cannot be a future date.\n"
                "- Security paid date cannot be earlier than the booking date.\n"
                "- apply_tax must be a boolean value."
            )
        ),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the booking."),
    },
    examples=[
        OpenApiExample(
            "Create booking sample",
            value={
                "client_id": 1,
                "pickup_date": "06-05-2026T10:00:00",
                "return_date": "07-05-2026T18:00:00",
                "variants": [{"id": 1, "quantity": 1, "price": 500}],
                "advance_amount": 200,
                "payment_method": "cash",
            },
            request_only=True,
        ),
        OpenApiExample(
            "Create booking without tax",
            value={
                "client_id": 1,
                "pickup_date": "06-05-2026T10:00:00",
                "return_date": "07-05-2026T18:00:00",
                "variants": [{"id": 1, "quantity": 1, "price": 500}],
                "advance_amount": 200,
                "payment_method": "cash",
                "apply_tax": False,
            },
            request_only=True,
        ),
    ],
)
@transaction.atomic
def create(self, request):
    savepoint = transaction.savepoint()
    uploaded_files = []  # Track temp files for cleanup
    preview_urls = []    # Track sync-uploaded preview urls for cleanup on rollback
    try:
        # Determine request format and parse data accordingly
        content_type = request.content_type or ''

        if 'multipart/form-data' in content_type:
            request_data = self._parse_multipart_to_json(request.data)
            logger.info(f"Parsed multipart data for booking creation")
        else:
            request_data = dict(request.data)

        # Resolve effective datetime from optional booked_date field.
        # Pattern mirrors add_payment.py / update_payment.py.
        booked_date_str = request_data.get("booked_date")
        effective_dt = None
        booked_date_is_date_only = False
        if booked_date_str:
            # Try date+time format first
            try:
                effective_dt = _make_aware(_datetime.strptime(booked_date_str, "%d-%m-%YT%H:%M:%S"))
            except ValueError:
                # Date-only: combine with current time (never midnight)
                try:
                    parsed_date = _datetime.strptime(booked_date_str, "%d-%m-%Y").date()
                    effective_dt = _make_aware(
                        _datetime.combine(parsed_date, _localtime(_now()).time())
                    )
                    booked_date_is_date_only = True
                except ValueError:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"booked_date": ["Invalid format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."]},
                        message="Invalid booked_date format.",
                    )
            # Reject future dates; use date-only comparison when no time was provided
            eff_for_future = effective_dt.date() if booked_date_is_date_only else effective_dt
            now_for_future = _now().date()        if booked_date_is_date_only else _now()
            if eff_for_future > now_for_future:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"booked_date": ["booked_date cannot be a future date."]},
                    message="Booked date cannot be a future date.",
                )

        # Per-transaction tax switch (default: tax applies as configured)
        apply_tax, apply_tax_error = resolve_apply_tax(request_data)
        if apply_tax_error:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"apply_tax": [apply_tax_error]},
                message=apply_tax_error,
            )

        # Get shop
        try:
            shop = Shop.objects.get(id=self.shop_id)
        except Shop.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"shop": ["Shop not found."]},
                message="Shop not found."
            )

        # STEP 0: Collect + validate documents FIRST (before any other validation)
        document_files = self._collect_document_files(request) if request.FILES else []
        total_document_count = len(document_files)

        if document_files:
            is_valid, error_response, _files = self._validate_documents_sync(
                request, shop, savepoint, files=document_files
            )
            if not is_valid:
                return error_response  # Return validation error immediately

        # Step 1: Validate and get/create client
        client, error = self._validate_client(request_data, shop)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 2: Validate dates
        pickup_date, return_date, cooling_period_end, pickup_time, return_time, cooling_period_type, error = self._validate_dates(request_data)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 2.5: Validate rental_duration_type (NEW)
        rental_duration_type, error = self._validate_rental_duration_type(request_data)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 2.6: Pickup date must not be earlier than the booking date
        # (booked_date when given, else now - the booking's created_at).
        # Compared by calendar date, so a same-day pickup is always accepted.
        error = self._validate_pickup_not_before_booking(
            pickup_date,
            effective_dt or _now(),
            field="booked_date" if effective_dt else "pickup_date",
        )
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 3: Validate delivery status
        delivery_status = request_data.get("delivery_status", "booked")
        error = self._validate_delivery_status(delivery_status, pickup_date)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 4: Validate product variants
        variants = request_data.get("variants")
        if isinstance(variants, list) and variants:
            error = self._validate_variant_products(
                [v.get("id") if isinstance(v, dict) else None for v in variants], shop.id
            )
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
        stock_end_date = cooling_period_end if (cooling_period_end and cooling_period_type == "after") else return_date
        error = self._validate_variants(variants, shop, pickup_date, stock_end_date, cooling_period_end, cooling_period_type)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 5: Validate amounts (products + additional charges)
        total_amount, discount, security, advance, additional_charges, error = self._validate_amounts(
            variants, request_data
        )
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 5.5: Apply GST calculation, if the shop has an active tax configuration.
        # For inclusive tax, `variants`/`additional_charges`/`security` amounts
        # are left exactly as received - only `total_amount` (and the
        # Booking's own tax_amount/taxable_amount, set later) are derived
        # from tax_result. Overrides `total_amount` only when tax is actually
        # enabled - if disabled or unconfigured, the existing pre-tax flow is
        # completely unaffected. `security` is never tax-adjusted here -
        # calculate_booking_tax_v2 always returns security_amount=None.
        # apply_tax=false: this booking only takes the existing no-tax path.
        tax_result = None
        tax_config = get_transaction_tax_config(shop, apply_tax)
        if tax_config:
            tax_result = calculate_booking_tax_v2(tax_config, variants, additional_charges, security, discount=discount)
            total_amount = tax_result["total_amount"]

        # `total_amount` is always the raw, pre-discount payable figure now
        # (both tax-enabled and no-tax paths - see calculate_booking_tax_v2),
        # so discount is always subtracted here, uniformly.
        discount_for_advance_check = discount

        # Validate payments (split payments or single payment)
        validated_payments, error = self._validate_payments(request_data, total_amount, discount_for_advance_check)
        if error:
            validated_payments = None
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 6: Validate staff
        staff = None
        staff_id = request_data.get("staff_id")
        if staff_id:
            staff, error = self._validate_staff(request_data, shop)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

        # Step 6.5: Validate purchase_mode
        purchase_mode, error = self._validate_purchase_mode(request_data)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error
        request_data["purchase_mode"] = purchase_mode

        # Step 7: Create booking record
        class RequestDataWrapper:
            def __init__(self, user, data):
                self.user = user
                self.data = data

        temp_request = RequestDataWrapper(request.user, request_data)
        dates_data = (pickup_date, return_date, cooling_period_end, pickup_time, return_time, cooling_period_type)
        amounts_data = (total_amount, discount, security, advance, additional_charges)
        booking, error = self._create_booking_record(
            temp_request, shop, client, dates_data, amounts_data, delivery_status, staff, rental_duration_type,
            effective_dt=effective_dt, tax_result=tax_result,
        )
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Set rental_duration_type on the booking
        try:
            booking.rental_duration_type = rental_duration_type
            booking.save(update_fields=['rental_duration_type'])
        except Exception as e:
            logger.error(f"Failed to set rental_duration_type for booking: {str(e)}")
            transaction.savepoint_rollback(savepoint)
            return self.error_response(
                message="Failed to set rental duration type.",
                dev_message=str(e)
            )

        # Initialize documents + documents_details
        booking.documents = []
        booking.documents_details = {
            "status": "uploading" if total_document_count > SYNC_DOCUMENT_UPLOAD_COUNT else "completed",
            "total_count": total_document_count,
            "uploaded_count": 0,
        }
        booking.save(update_fields=['documents', 'documents_details'])

        # Step 8: Create additional charges
        error = self._create_additional_charges(booking, additional_charges, shop)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 9: Create booked items
        error = self._create_booked_items(booking, variants, shop, request)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 10: Update availability
        error = self._update_availability(variants, pickup_date, stock_end_date, booking.id, cooling_period_end, cooling_period_type)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 11: Create client requirement
        description = request_data.get("description", "")
        error = self._create_client_requirement(shop, booking, description)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 12: Create payment records (handles both single and split payments)
        error = self._create_payment_records(booking, validated_payments, shop=shop, effective_dt=effective_dt)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # STEP 12.5: Create security deposit payment record
        security_amount_is_paid, paid_flag_error = self._resolve_security_paid_flag(
            request_data,
            default=True,
        )
        if paid_flag_error:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_amount_is_paid": [paid_flag_error]},
                message=paid_flag_error,
            )

        security_account = None
        security_payment_method = None
        security_paid_at = None

        # account_id/payment_method are only ever relevant when the security
        # deposit is paid - when security_amount_is_paid is False, this
        # whole block is skipped entirely: security_account_id is never
        # read from the request, never validated, never resolved. An
        # unpaid request can supply anything (or nothing, or garbage) in
        # security_account_id with zero effect and no error - the deposit
        # is always saved with account=NULL, payment_method=NULL (enforced
        # centrally in _sync_security_deposit_payment below).
        #
        # Mandatory-when-paid check (missing account_id while paid) lives
        # centrally in _sync_security_deposit_payment (called below) - it
        # returns a validation error if security_amount_is_paid=True and no
        # account was resolved here, applying identically to Create and
        # Update.
        if security_amount_is_paid:
            security_account_id = request_data.get('security_account_id')
            if security_account_id:
                from finance.models import Account as _Account
                try:
                    security_account = _Account.objects.get(
                        id=security_account_id,
                        shop_id=self.shop_id,
                        deleted__isnull=True,
                        is_active=True,
                    )
                    security_payment_method = 'cash' if security_account.account_type == 'cash' else 'upi'
                except _Account.DoesNotExist:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={'security_account_id': ['Invalid or inactive account for security payment.']},
                        message='Invalid security account selected.',
                    )

            # Optional security_paid_at - never earlier than the booking's created_at.
            security_paid_at, error = self._resolve_security_paid_at(
                request_data,
                lower_bound=booking.created_at,
            )
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

        error = self._sync_security_deposit_payment(
            booking=booking,
            security_amount=security,
            security_amount_is_paid=security_amount_is_paid,
            payment_method=security_payment_method,
            savepoint=savepoint,
            account=security_account,
            effective_dt=effective_dt,
            paid_at=security_paid_at,
        )
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # STEP 12.6: Upload first 2 documents synchronously, save rest for async
        if total_document_count > 0:
            preview_files = document_files[:SYNC_DOCUMENT_UPLOAD_COUNT]
            remaining_files = document_files[SYNC_DOCUMENT_UPLOAD_COUNT:]

            preview_urls, error = self._upload_documents_to_s3_sync(
                preview_files, shop, request.user, savepoint
            )
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

            if remaining_files:
                uploaded_files = self._save_temp_documents(
                    request, booking.id, files=remaining_files
                )
                if isinstance(uploaded_files, dict) and 'error' in uploaded_files:
                    transaction.savepoint_rollback(savepoint)
                    # preview_urls are safe to cleanup if the DB tx rolls back
                    if preview_urls:
                        self._cleanup_s3_documents_sync(preview_urls)
                    return uploaded_files['error']

            booking.documents = preview_urls or []
            booking.documents_details = {
                "status": "uploading" if remaining_files else "completed",
                "total_count": total_document_count,
                "uploaded_count": len(booking.documents),
            }
            booking.save(update_fields=['documents', 'documents_details'])

        # Commit the savepoint - all operations successful
        transaction.savepoint_commit(savepoint)

        # Queue async tasks ONLY after successful commit. If the Celery
        # broker (e.g. Redis) is unavailable, .delay() raises a connection
        # error - since the booking is already committed at this point, we
        # must not let that surface as a 500. Instead we catch it, log it,
        # and flag it so the response can carry a warning.
        celery_unavailable = False
        from booking_v2.tasks import generate_booking_thumbnails_task, upload_booking_documents_task
        try:
            generate_booking_thumbnails_task.delay(booking.id)
        except Exception as e:
            celery_unavailable = True
            logger.error(
                f"Celery broker unavailable, could not queue thumbnail task for booking {booking.id}: {str(e)}"
            )

        # Queue async document upload AFTER transaction commit
        if uploaded_files:
            try:
                upload_booking_documents_task.delay(
                    booking.id,
                    uploaded_files,
                    shop.id,
                    request.user.id
                )
            except Exception as e:
                celery_unavailable = True
                logger.error(
                    f"Celery broker unavailable, could not queue document upload task for booking {booking.id}: {str(e)}"
                )

        # Handle invoice with subscription and feature validation
        success_message = self._handle_send_invoice(booking, request_data, request.user, "Booking created successfully.")

        if celery_unavailable:
            success_message = (
                f"{success_message} Note: background processing is temporarily unavailable "
                "(worker offline); documents/thumbnails will not be generated automatically."
            )

        return self.success_response(
            data={
                "booking_id": booking.id,
                "status": "processing",
                "message": "Booking created successfully. Processing documents in background."
            },
            message=success_message
        )

    except ValidationError as ve:
        # Cleanup temp files on validation error
        if uploaded_files:
            self._cleanup_temp_files(uploaded_files)

        # Cleanup any preview uploads if we rolled back the DB tx
        if preview_urls:
            self._cleanup_s3_documents_sync(preview_urls)

        transaction.savepoint_rollback(savepoint)
        if hasattr(ve, "message_dict"):
            errors = ve.message_dict
            first_field = next(iter(errors))
            first_msg = errors[first_field][0] if errors[first_field] else "Validation error."
            return self.validation_error_response(
                errors=errors,
                message=first_msg
            )
        else:
            msgs = ve.messages if hasattr(ve, "messages") else [str(ve)]
            return self.validation_error_response(
                errors={"detail": msgs},
                message=msgs[0] if msgs else "Validation error."
            )

    except Exception as e:
        # Cleanup temp files on any error
        if uploaded_files:
            self._cleanup_temp_files(uploaded_files)

        # Cleanup any preview uploads if we rolled back the DB tx
        if preview_urls:
            self._cleanup_s3_documents_sync(preview_urls)

        transaction.savepoint_rollback(savepoint)
        logger.error(f"Booking create error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the booking.",
            dev_message=str(e)
        )


__all__ = ["create"]
