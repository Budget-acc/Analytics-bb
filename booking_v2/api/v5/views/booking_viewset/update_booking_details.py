import logging
from django.db import transaction
from rest_framework.decorators import action
from booking_v2.models import BookingV2, BookedProductVariant, BookingAdditionalCharge
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from .create import MEASUREMENTS_HELP

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Update booking details",
    description="Updates editable booking fields like variants, dates, charges, description, security and related metadata.",
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID."),
    ],
    request=inline_serializer(
        name="UpdateBookingDetailsV5Request",
        fields={
            "pickup_date": serializers.CharField(
                required=False,
                help_text=(
                    "A changed pickup date cannot be earlier than the booking date (created_at). "
                    "Compared by calendar date, so the same day as the booking is allowed."
                ),
            ),
            "return_date": serializers.CharField(
                required=False,
                help_text=(
                    "Format: DD-MM-YYYY (time is taken from return_time, HH:MM). "
                    "Returned booking (delivery_status=returned): the return date can be corrected when it is after "
                    "the pickup date/time and not later than the current date/time; a date-only value for today uses "
                    "the current time. No availability check is performed, and every returned product whose return "
                    "date is later than the new booking return date is moved to that date. "
                    "Partially returned booking: the return date cannot be earlier than the latest product return date. "
                    "pickup_date stays locked for returned bookings (re-sending the same value is allowed, so amounts "
                    "of returned products can be edited)."
                ),
            ),
            "variants": serializers.ListField(
                child=serializers.DictField(),
                required=False,
                help_text=MEASUREMENTS_HELP + " Sending measurements for an item replaces its saved measurements.",
            ),
            "discount_amount": serializers.IntegerField(required=False),
            "security_amount": serializers.IntegerField(
                required=False,
                help_text=(
                    "Reducing (or zeroing) a paid security deposit takes the difference back out of the account "
                    "that holds it; rejected when that account's current available balance cannot cover it."
                ),
            ),
            "security_amount_is_paid": serializers.BooleanField(
                required=False,
                help_text=(
                    "Changing a paid deposit to false takes the full amount back out of its account; rejected "
                    "when that account's current available balance cannot cover it."
                ),
            ),
            "security_account_id": serializers.IntegerField(
                required=False,
                help_text=(
                    "Account for the security deposit. Required when security_amount_is_paid is being set "
                    "to true (else a validation error is returned) - ignored when security_amount_is_paid "
                    "is false. If the security deposit is already paid and staying paid, omitting this "
                    "keeps its existing account. Switching a paid deposit to another account takes the full "
                    "amount out of the current account first; rejected when that account's current available "
                    "balance cannot cover it. payment_method for the security deposit is never accepted "
                    "from the request; it is always derived from the account's account_type "
                    "(cash -> cash, bank -> upi)."
                ),
            ),
            "security_paid_at": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Optional date/time the security deposit was paid - only used when the security deposit "
                    "is (or is being marked) paid; ignored when security_amount_is_paid is false. "
                    "Formats: DD-MM-YYYY (current time is used as the time component) or DD-MM-YYYYTHH:MM:SS. "
                    "Cannot be a future date, cannot be earlier than the booking date (created_at) - it may be "
                    "earlier than the date the deposit was recorded - "
                    "and cannot be later than an existing security refund/deduction. Supplying it on an "
                    "already-paid deposit updates its paid date (and its ledger entry date). If omitted, a newly "
                    "paid deposit uses the current date and time and an already-paid deposit keeps its paid date."
                ),
            ),
            "advance_amount": serializers.IntegerField(required=False),
            "description": serializers.CharField(required=False),
            "purchase_mode": serializers.ChoiceField(
                choices=["normal", "package", "courier", "pickup"],
                required=False,
                help_text="Delivery/fulfillment mode for this booking."
            ),
            "additional_charges": serializers.ListField(child=serializers.DictField(), required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response from build_success_response helper."),
        400: OpenApiResponse(description="Exact messages include: Cannot update a completed booking.; Booking not found.; Cannot modify dates for returned bookings. (only when a different pickup_date is sent for a returned booking); Return date must be in the past for a returned booking.; Return date must be after pickup date.; Pickup date cannot be earlier than the booking date.; Return date cannot be earlier than the latest product return date. (partially returned bookings only); Invalid product selected.; Some product variants are invalid.; The selected product has been deleted and cannot be used for a booking.; Invalid or inactive account selected for security payment.; account is required when security amount is marked as paid.; Invalid security_paid_at format.; Security paid date cannot be a future date.; Security paid date cannot be earlier than the booking date.; security paid date cannot be later than an existing security refund/deduction date.; Insufficient balance in <account> to reduce the security deposit from ₹<old> to ₹<new>. Required: ₹<X>, available: ₹<Y>.; Insufficient balance in <account> to mark the security deposit as unpaid. Required: ₹<X>, available: ₹<Y>.; Insufficient balance in <account> to move the security deposit to <new account>. Required: ₹<X>, available: ₹<Y>.; Insufficient balance in <account> to remove the security deposit. Required: ₹<X>, available: ₹<Y>."),
        500: OpenApiResponse(description="Exact messages include: Failed to update security details.; Failed to save booking changes.; An error occurred while updating the booking. Please try again."),
    },
)
@action(detail=False, methods=["patch"], url_path="update-details/(?P<pk>[^/.]+)")
@transaction.atomic
def update_booking_details(self, request, *args, **kwargs):
    """
    Update booking details including variants, dates, additional charges, staff, etc.
    Supports grace extension for bookings that ended yesterday.
    """
    pk = self.kwargs.get("pk")

    try:
        # Lock the booking row for update (atomic)
        booking = BookingV2.objects.select_for_update().get(id=pk, shop_id=self.shop_id)

        # Lock child rows explicitly to avoid FOR UPDATE on outer joins..
        booked_items_qs = BookedProductVariant.objects.select_for_update().filter(booking=booking)
        booked_items = list(booked_items_qs)

        additional_charges_qs = BookingAdditionalCharge.objects.select_for_update().filter(booking=booking)
        additional_charges = list(additional_charges_qs)

        # Parse request data based on content type (same as create method)
        content_type = request.content_type or ''

        if 'multipart/form-data' in content_type:
            # Multipart form data - parse bracket notation and JSON strings
            data = self._parse_multipart_to_json(request.data)
            logger.info(f"Parsed multipart data for update: {data}")
        else:
            # JSON or other formats - use as-is
            data = dict(request.data)

        # Prevent update if booking already completed
        if booking.booking_status == "completed":
            return self.validation_error_response(
                errors={"booking_status": ["Cannot update a completed booking."]},
                message="Cannot update a completed booking."
            )

        # Create a savepoint for nested transaction handling
        savepoint = transaction.savepoint()

        urls_to_cleanup_after_commit = []

        try:
            # Reject newly added variants whose variant/product is soft-deleted.
            # Items already on the booking are left as-is so old bookings stay editable.
            incoming_variants = data.get("variants")
            if isinstance(incoming_variants, list) and incoming_variants:
                existing_variant_ids = {item.product_variant_id for item in booked_items}
                new_variant_ids = []
                for v in incoming_variants:
                    vid = v.get("id") if isinstance(v, dict) else None
                    try:
                        if int(vid) in existing_variant_ids:
                            continue
                    except (TypeError, ValueError):
                        pass  # Let the helper return the format error
                    new_variant_ids.append(vid)

                if new_variant_ids:
                    variant_error = self._validate_variant_products(new_variant_ids, self.shop_id)
                    if variant_error:
                        transaction.savepoint_rollback(savepoint)
                        return variant_error

            # Basic validations (discount, staff, client)
            validation_result = self._validate_basic_fields(booking, data, savepoint)
            if validation_result:
                return validation_result

            # Handle client requirement update
            client_req_result = self._update_client_requirement(booking, data, savepoint)
            if client_req_result:
                return client_req_result

            # Process dates and variants changes (pass cached child lists)
            date_variant_result = self._process_dates_and_variants(booking, data, savepoint, request.user, booked_items)
            if date_variant_result:
                return date_variant_result
            # After possible variant updates, refresh booked_items cache (ensures atomic view)
            try:
                booked_items = list(BookedProductVariant.objects.select_for_update().filter(booking=booking))
            except Exception:
                # Fallback to non-locked fetch if DB does not allow re-locking; still within transaction
                booked_items = list(BookedProductVariant.objects.filter(booking=booking))

            # Handle additional charges (pass cached additional charges). The helper will update data['_updated_additional_charges_cache'] with fresh list.
            charges_result = self._update_additional_charges(booking, data, savepoint, additional_charges)
            if charges_result:
                return charges_result
            # After additional charges update, prefer the updated cache placed into data by helper
            additional_charges = data.get('_updated_additional_charges_cache', additional_charges)

            # Update other fields (including security amount and payment method).
            # Must run BEFORE _calculate_and_validate_totals: the totals
            # calculation reads booking.security_amount directly to build the
            # taxable subtotal for inclusive tax, so security_amount has to
            # already reflect this request's new value (if any) by then -
            # otherwise a request that changes security_amount and
            # discount_amount/items together would compute tax_amount from
            # the stale, pre-request security_amount.
            try:
                update_other_fields_result = self._update_other_fields(booking, data, savepoint)
                if update_other_fields_result:
                    # A validation error returned as a value here (e.g.
                    # security_account_id required when security is marked
                    # paid) is not an exception, so it must be rolled back
                    # explicitly - unlike the except branches below, nothing
                    # else on this path does it automatically, and by this
                    # point _process_dates_and_variants/_update_additional_charges
                    # may have already written real rows earlier in this request.
                    transaction.savepoint_rollback(savepoint)
                    return update_other_fields_result
            except ValueError as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"security_amount": [str(e)]},
                    message=str(e)
                )
            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Failed to update security details for booking {pk}: {str(e)}")
                return self.error_response(
                    message="Failed to update security details.",
                    dev_message=str(e)
                )

            # Calculate totals and validate (use refreshed child lists)
            totals_result = self._calculate_and_validate_totals(booking, data, savepoint, booked_items, additional_charges)
            if totals_result:
                return totals_result

            if request.FILES or "remove_documents" in data:
                doc_result, urls_to_delete = self._update_booking_documents(booking, request, data, savepoint)
                if doc_result:
                    return doc_result
                if urls_to_delete:
                    urls_to_cleanup_after_commit.extend(urls_to_delete)
                    logger.info(f"Scheduled {len(urls_to_delete)} documents for post-commit deletion")

            # Final save
            try:
                booking.save()
                transaction.savepoint_commit(savepoint)
                if urls_to_cleanup_after_commit:
                    logger.info(f"Deleting {len(urls_to_cleanup_after_commit)} documents from S3 after successful commit")
                    self._cleanup_s3_documents(urls_to_cleanup_after_commit)

                try:
                    create_activity_log(
                        shop=booking.shop,
                        user=request.user,
                        instance=booking,
                        action="updated",
                        message=f"Booking details were updated."
                    )
                except Exception as log_error:
                    logger.exception(f"Failed to create activity log for booking update: {log_error}")

                success_message = self._handle_send_invoice(booking, data, request.user, "Booking details updated successfully.")
                return self._build_success_response(booking, message=success_message, data=data)

            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Failed to save booking {pk}: {str(e)}")
                return self.error_response(
                    message="Failed to save booking changes.",
                    dev_message=str(e)
                )

        except Exception as e:
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


__all__ = ["update_booking_details"]
