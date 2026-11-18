import logging
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Min, Sum
from django.utils import timezone
from booking_v2.models import BookedProductVariant, BookingAdditionalCharge, ClientRequirementV2
from shop.models import Staff
from shop.api.v3.utils.tax_calculation import resolve_taxable_flags, split_inclusive_amount

logger = logging.getLogger("api_requests")


def _validate_basic_fields(self, booking, data, savepoint):
    """Validate discount amount, staff, and client"""
    # Discount Amount validation
    discount_amount = data.get("discount_amount", booking.discount_amount or 0)
    try:
        discount_amount = int(discount_amount)
        if discount_amount < 0:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"discount_amount": ["Discount amount must be non-negative."]},
                message="Discount amount must be non-negative."
            )
    except ValueError:
        transaction.savepoint_rollback(savepoint)
        return self.validation_error_response(
            errors={"discount_amount": ["Invalid discount amount."]},
            message="Invalid discount amount."
        )

    # Security payment method validation
    if "security_payment_method" in data:
        payment_method = data["security_payment_method"]

        if payment_method not in self.VALID_PAYMENT_METHODS:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_payment_method": [f"Invalid payment method. Must be one of: {', '.join(self.VALID_PAYMENT_METHODS)}"]},
                message="Invalid security payment method."
            )

    # Staff validation
    staff_id = data.get("staff_id")
    if staff_id:
        try:
            staff = Staff.objects.get(id=staff_id, shop=booking.shop)
            data['_validated_staff'] = staff
        except Staff.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"staff_id": ["Staff not found for this shop."]},
                message="Staff not found."
            )

    # Client validation
    client_id = data.get("client_id")
    client_info = data.get("client", {})

    if client_id or client_info:
        # Prepare request data format for _validate_client
        request_data = {
            "client_id": client_id,
            "client": client_info,
            "client_address": data.get("client_address", booking.client_address)
        }

        # Use the existing _validate_client method
        client, error_response = self._validate_client(request_data, booking.shop)

        if error_response:
            transaction.savepoint_rollback(savepoint)
            return error_response

        if client:
            data['_validated_client'] = client

    # Rental duration type validation
    if "rental_duration_type" in data:
        rental_duration_type, error = self._validate_rental_duration_type(data)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error
        data['_validated_rental_duration_type'] = rental_duration_type

    # Purchase mode validation
    if "purchase_mode" in data:
        purchase_mode, error = self._validate_purchase_mode(data)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error
        data['_validated_purchase_mode'] = purchase_mode

    data['_validated_discount_amount'] = discount_amount
    return None


def _update_client_requirement(self, booking, data, savepoint):
    """Update client requirement description"""
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
            logger.error(f"Failed to update client requirement for booking {booking.id}: {str(e)}")
            return self.error_response(
                message="Failed to update client requirement.",
                dev_message=str(e)
            )
    return None


def _process_dates_and_variants(self, booking, data, savepoint, user, booked_items_cache=None):
    """
    Process date and variant changes with enhanced logic for grace extensions
    """
    from pytz import timezone as pytz_timezone
    india = pytz_timezone('Asia/Kolkata')

    # Get current state (use cached list if provided)
    old_items = list(booked_items_cache) if booked_items_cache is not None else list(booking.booked_items.all())
    old_variants = [{
        "id": item.product_variant_id,
        "amount": item.amount,
        "quantity": item.quantity or 1,
        "measurements": item.measurements or [],
        "return_status": item.return_status or "not_returned",
        "returned_at": item.returned_at or None,
        "extended": item.extended_until or None,
        } for item in old_items]

    old_pickup_date = booking.pickup_date.astimezone(india)
    old_return_date = booking.return_date.astimezone(india)
    old_cooling_end = booking.cooling_period_end.astimezone(india) if booking.cooling_period_end else None

    # Parse new dates
    date_result = self._parse_and_validate_dates(booking, data, savepoint, old_pickup_date, old_return_date, old_cooling_end)
    if isinstance(date_result, dict) and 'error' in date_result:
        return date_result['error']

    new_pickup_date, new_return_date, new_cooling_end, new_pickup_time, new_return_time, cooling_period_type = date_result

    # Pickup date must not be earlier than the booking date (created_at).
    # Only checked when the pickup day actually changes, so legacy bookings
    # are still editable when the unchanged pickup date is re-sent.
    if new_pickup_date.date() != old_pickup_date.date():
        pickup_error = self._validate_pickup_not_before_booking(new_pickup_date, booking.created_at)
        if pickup_error:
            transaction.savepoint_rollback(savepoint)
            return pickup_error

    is_returned_booking = booking.delivery_status == "returned"

    # Partially returned booking: a changed return date must not be earlier than the
    # latest product return (by calendar day, uses the locked rows already loaded).
    # Fully returned bookings instead pull later product returns back - see below.
    return_date_changed = new_return_date.date() != old_return_date.date()
    if return_date_changed and not is_returned_booking:
        returned_ats = [
            item.returned_at.astimezone(india)
            for item in old_items
            if item.return_status == "returned" and item.returned_at
        ]
        if returned_ats:
            latest_returned_at = max(returned_ats)
            if new_return_date.date() < latest_returned_at.date():
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"return_date": [
                        f"Latest product return date is {latest_returned_at.strftime('%d-%m-%Y')}."
                    ]},
                    message="Return date cannot be earlier than the latest product return date.",
                    dev_message=(
                        f"requested_return_date={new_return_date.date().isoformat()}, "
                        f"latest_returned_at={latest_returned_at.isoformat()}"
                    ),
                )

    # Process variants (use cached old_items to avoid extra DB query)
    variant_result = self._process_variants(booking, data, old_variants, old_items)
    if isinstance(variant_result, dict) and 'error' in variant_result:
        transaction.savepoint_rollback(savepoint)
        return variant_result['error']

    new_variants = variant_result

    # Determine if we need availability check
    need_availability_check = self._need_availability_check(
        old_variants, new_variants,
        old_pickup_date, new_pickup_date,
        old_return_date, new_return_date,
        old_cooling_end, new_cooling_end,
        data
    )

    # Returned booking, date correction only (same variants/quantities, pickup unchanged,
    # new return date not in the future): products are already back, so skip the
    # availability check and leave historical availability untouched.
    if need_availability_check and is_returned_booking:
        same_items = sorted((v["id"], v.get("quantity", 1)) for v in old_variants) == \
            sorted((v["id"], v.get("quantity", 1)) for v in new_variants)
        if (
            same_items
            and new_pickup_date.date() == old_pickup_date.date()
            and new_return_date <= timezone.now()
        ):
            need_availability_check = False

    # Handle availability and update variants
    if need_availability_check:
        availability_result = self._handle_availability_check(
            booking, old_variants, new_variants,
            old_pickup_date, new_pickup_date,
            old_return_date, new_return_date,
            old_cooling_end, new_cooling_end,
            savepoint, user,
            cooling_period_type
        )
        if availability_result:
            return availability_result
    else:
        # Just update variant amounts without availability check
        update_result = self._update_variant_amounts_only(booking, new_variants, savepoint)
        if update_result:
            return update_result

    # Returned booking with a corrected return date: any product returned later than
    # the new booking return date is pulled back to it (rows already locked by the view).
    if is_returned_booking and return_date_changed:
        BookedProductVariant.objects.filter(
            booking=booking,
            return_status="returned",
            returned_at__gt=new_return_date,
        ).update(returned_at=new_return_date)

    # Update dates
    booking.pickup_date = new_pickup_date
    booking.return_date = new_return_date
    booking.cooling_period_end = new_cooling_end
    booking.pickup_time = new_pickup_time
    booking.return_time = new_return_time

    # Update cooling_period_type in details
    if new_cooling_end:
        if booking.details is None:
            booking.details = {}
        booking.details['cooling_period_type'] = cooling_period_type

    return None


def _update_additional_charges(self, booking, data, savepoint, existing_charges_cache=None):
    """Handle additional charges update"""
    if "additional_charges" not in data:
        return None

    additional_charges_data = data["additional_charges"]

    if not isinstance(additional_charges_data, list):
        transaction.savepoint_rollback(savepoint)
        return self.validation_error_response(
            errors={"additional_charges": ["Additional charges must be a list."]},
            message="Invalid additional charges format."
        )

    try:
        # Get existing charges mapped by ID (use cache when provided)
        existing_charges = {charge.id: charge for charge in existing_charges_cache} if existing_charges_cache is not None else {charge.id: charge for charge in booking.additional_charges.all()}
        processed_ids = set()
        updated_charges = []
        created_charges = []

        for charge_data in additional_charges_data:
            if not isinstance(charge_data, dict):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"additional_charges": ["Each additional charge must be an object."]},
                    message="Invalid additional charge format."
                )

            charge_id = charge_data.get("id")
            name = charge_data.get("name", "").strip()
            amount = charge_data.get("amount", 0)

            if not name:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"additional_charges": ["Charge name is required."]},
                    message="Additional charge name is required."
                )

            try:
                amount = float(amount)
                if amount <= 0:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"additional_charges": [f"Amount for '{name}' must be positive."]},
                        message="Additional charge amounts must be positive."
                    )
            except (ValueError, TypeError):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"additional_charges": [f"Invalid amount for '{name}'."]},
                    message="Invalid additional charge amount."
                )

            # Tax + discount are no longer applied here - `amount` is set to
            # the raw, as-received value for every charge; the caller
            # (_calculate_and_validate_totals) is the single place that
            # applies discount/tax across ALL of a booking's charges,
            # without mutating any individual charge's stored amount.
            amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if charge_id:
                # Update existing charge
                if charge_id in existing_charges:
                    charge = existing_charges[charge_id]
                    charge.name = name
                    charge.amount = amount
                    updated_charges.append(charge)
                    processed_ids.add(charge_id)
                else:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"additional_charges": [f"Additional charge with ID {charge_id} not found."]},
                        message="Additional charge not found."
                    )
            else:
                # Create new charge
                created_charges.append(BookingAdditionalCharge.objects.create(
                    booking=booking,
                    name=name,
                    amount=amount,
                ))

        if updated_charges:
            BookingAdditionalCharge.objects.bulk_update(updated_charges, ["name", "amount"])

        # Delete charges that weren't included in the update
        charges_to_delete = [
            charge_id for charge_id in existing_charges.keys()
            if charge_id not in processed_ids
        ]
        if charges_to_delete:
            BookingAdditionalCharge.objects.filter(
                id__in=charges_to_delete,
                booking=booking
            ).delete()

        # Build the updated cache in-memory instead of re-querying the DB:
        # existing charges minus deleted ones (already updated in-place above) plus newly created ones.
        updated_charges_cache = [
            charge for charge_id, charge in existing_charges.items()
            if charge_id not in charges_to_delete
        ] + created_charges

        # Store for caller to use when calculating totals
        data['_updated_additional_charges_cache'] = updated_charges_cache

        return None

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Failed to update additional charges for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to update additional charges.",
            dev_message=str(e)
        )


def _calculate_and_validate_totals(self, booking, data, savepoint, booked_items_cache=None, additional_charges_cache=None):
    """Calculate totals and validate against constraints"""
    discount_amount = data.get('_validated_discount_amount', booking.discount_amount or 0)

    # Calculate totals
    # Use cached lists if provided (these are already locked); always
    # materialize to a list since the tax-enabled path below mutates and
    # bulk_updates every item/charge, not just the ones touched this call.
    current_booked_items = list(booked_items_cache) if booked_items_cache is not None else list(BookedProductVariant.objects.filter(booking=booking))
    current_additional_charges = list(additional_charges_cache) if additional_charges_cache is not None else list(BookingAdditionalCharge.objects.filter(booking=booking))

    # Tax: recalculate only if this booking already has tax applied (no
    # retroactive enabling of tax via update). Uses the shop's current live
    # taxable_components for category applicability, combined with the
    # booking's own snapshotted tax_rate (fixed at creation/last
    # recalculation, so it never drifts if the shop's rate changes mid-edit).
    #
    # Discount is applied against every item/charge on this booking (not
    # just ones touched this call), so Product Total + Additional Charges +
    # Tax always relate consistently to total_amount. This means the totals
    # are recomputed from ALL current siblings whenever discount or any
    # item/charge changes, even ones not in this request's payload. Item/
    # charge `amount` values themselves are never mutated by tax/discount -
    # they always hold exactly what was received.
    if getattr(booking, "is_tax_applied", False):
        product_taxable, additional_taxable, security_taxable = resolve_taxable_flags(
            booking.taxable_components
        )
        discount_dec = Decimal(str(discount_amount))

        items_raw_total = sum(
            (Decimal(str(i.amount)) for i in current_booked_items),
            Decimal("0.00"),
        )
        charges_raw_total = sum(
            (Decimal(str(c.amount)) for c in current_additional_charges),
            Decimal("0.00"),
        )
        combined_raw = items_raw_total + charges_raw_total
        # Only needed by the Exclusive branch below (discount is applied
        # per-bucket, proportional to each bucket's raw share); the
        # Inclusive branch discounts the combined taxable subtotal directly.
        if combined_raw > 0:
            discount_on_items = (discount_dec * items_raw_total / combined_raw).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            discount_on_items = Decimal("0.00")
        discount_on_additional = (discount_dec - discount_on_items).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        tax_amount = Decimal("0.00")

        if booking.tax_calculation_type == "inclusive":
            # Inclusive: item/charge amounts are NEVER mutated - they keep
            # exactly the raw value received (matches the Exclusive branch
            # below). Discount and tax are computed purely at the combined-
            # subtotal level: sum the taxable buckets (products/charges/
            # security, per taxable_components), subtract the discount from
            # that taxable subtotal, then extract GST (inclusive) from the
            # result. Only booking.tax_amount/taxable_amount reflect this -
            # the individual item/charge/security amounts are untouched.
            # Mirrors calculate_booking_tax_v2 (Create) exactly - kept as a
            # separate implementation here since this file is Booking-only.
            taxable_subtotal = Decimal("0.00")
            if product_taxable:
                taxable_subtotal += items_raw_total
            if additional_taxable:
                taxable_subtotal += charges_raw_total
            security_dec = Decimal(str(booking.security_amount or 0))
            if security_taxable:
                taxable_subtotal += security_dec

            # Clamp at zero: discount_amount is only validated against
            # combined_raw (items + charges) below, not against this
            # taxable-scoped subtotal - when taxable_components excludes a
            # bucket with a large raw total, an otherwise-valid discount can
            # exceed taxable_subtotal. Without this floor, split_inclusive_amount
            # would return a negative base/tax that gets persisted as-is.
            discounted_taxable = max(Decimal("0.00"), taxable_subtotal - discount_dec)
            taxable_amount, tax_amount = split_inclusive_amount(discounted_taxable, booking.tax_rate)

            variants_total_dec = items_raw_total
            additional_charges_total_dec = charges_raw_total

            booking.subtotal = variants_total_dec + additional_charges_total_dec
            booking.taxable_amount = taxable_amount
            booking.tax_amount = tax_amount
            # total_amount is the raw, PRE-discount payable figure - tax is
            # already embedded in the raw amounts (inclusive), so nothing is
            # added on top. discount_amount is kept entirely separate and
            # must never reduce this stored value (mirrors Sale/Create).
            total_amount = int((items_raw_total + charges_raw_total).to_integral_value(rounding=ROUND_HALF_UP))
            variants_total = float(variants_total_dec)
            additional_charges_total = float(additional_charges_total_dec)

        else:
            # Exclusive: item/charge amounts are NEVER mutated - they keep
            # exactly their raw value (already the case, since
            # _process_variants/_update_additional_charges only pass raw
            # values through). Discount and tax are computed purely at the
            # bucket/aggregate level from the raw totals.
            discounted_items = items_raw_total - discount_on_items
            discounted_additional = charges_raw_total - discount_on_additional

            taxable_amount = Decimal("0.00")
            if product_taxable:
                tax_amount += (discounted_items * booking.tax_rate / Decimal("100"))
                taxable_amount += discounted_items
            if additional_taxable:
                tax_amount += (discounted_additional * booking.tax_rate / Decimal("100"))
                taxable_amount += discounted_additional
            if security_taxable:
                security_dec = Decimal(str(booking.security_amount or 0))
                tax_amount += (security_dec * booking.tax_rate / Decimal("100"))
                taxable_amount += security_dec
            tax_amount = tax_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            taxable_amount = taxable_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # subtotal is the raw, undiscounted sum - matches what's actually
            # stored per item (unchanged).
            booking.subtotal = items_raw_total + charges_raw_total
            booking.taxable_amount = taxable_amount
            booking.tax_amount = tax_amount
            # total_amount is the raw, PRE-discount payable figure plus tax
            # (tax itself is still correctly computed on the discounted
            # base, via discounted_items/discounted_additional above - only
            # the base it's added to switches from discounted to raw).
            # discount_amount is kept entirely separate and must never
            # reduce this stored value (mirrors Sale/Create).
            total_amount = int((items_raw_total + charges_raw_total + tax_amount).to_integral_value(rounding=ROUND_HALF_UP))
            variants_total = float(items_raw_total)
            additional_charges_total = float(charges_raw_total)

        # total_amount is raw/pre-discount in both tax branches now, so the
        # discount-bound check below uses the same base either way.
        discount_check_base = float(combined_raw)
    else:
        variants_total = sum(float(item.amount) for item in current_booked_items)
        additional_charges_total = sum(float(charge.amount) for charge in current_additional_charges)
        total_amount = variants_total + additional_charges_total
        discount_check_base = total_amount

    # `total_amount` is always the raw, pre-discount payable figure now
    # (both tax-enabled and no-tax paths), so discount is always subtracted
    # here, uniformly.

    # Validation: Discount cannot exceed total
    if discount_amount > discount_check_base:
        transaction.savepoint_rollback(savepoint)
        return self.validation_error_response(
            errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
            message="Discount amount cannot exceed total amount."
        )

    # Validation: Payable amount cannot be less than advance paid
    payable_amount = total_amount - discount_amount
    if payable_amount < booking.advance_amount:
        transaction.savepoint_rollback(savepoint)
        return self.validation_error_response(
            errors={"total_amount": ["The total amount cannot be less than the amount that has already been paid."]},
            message="Total cannot be less than paid amount."
        )

    # Ensure payable amount > 0
    if payable_amount < 0:
        transaction.savepoint_rollback(savepoint)
        return self.validation_error_response(
            errors={"total_amount": ["Total amount must be greater than zero."]},
            message="Total amount must be greater than zero."
        )

    # Update booking totals
    booking.total_amount = total_amount
    booking.discount_amount = discount_amount

    # Recalculate payment status
    current_advance = booking.advance_amount or 0
    final_payable = payable_amount

    if final_payable <= 0:
        booking.payment_status = True
        if final_payable < 0:
            logger.warning(f"Over-discounting detected for booking {booking.id}: discount={discount_amount}, total={total_amount}")
    else:
        booking.payment_status = (current_advance >= final_payable)

    if booking.payment_status and current_advance < final_payable and final_payable > 0:
        logger.error(f"Inconsistent payment state for booking {booking.id}: paid={current_advance}, payable={final_payable}, status={booking.payment_status}")
        booking.payment_status = False

    # Store calculated values for response
    data['_calculated_variants_total'] = variants_total
    data['_calculated_additional_charges_total'] = additional_charges_total
    data['_calculated_final_payable'] = final_payable
    data['_calculated_current_advance'] = current_advance

    return None


def _update_other_fields(self, booking, data, savepoint):
    """Update security amount and other simple fields"""
    from booking_v2.models import SecurityDepositPayment

    security_updated = False
    old_security_amount = booking.security_amount or 0
    new_security_amount = old_security_amount
    new_payment_method = None
    rental_duration_updated = False
    existing_sdp = SecurityDepositPayment.objects.filter(booking=booking).first()
    security_amount_is_paid = existing_sdp.is_paid if existing_sdp else True

    # # Block security amount changes once the booking has been returned
    # if (
    #     "security_amount" in data or "security_payment_method" in data or "security_account_id" in data
    # ) and str(getattr(booking, "delivery_status", "") or "").strip().lower() == "returned":
    #     raise ValueError("Security amount cannot be updated for the returned booking.")

    # Update security amount
    if "security_amount" in data:
        try:
            new_security_amount = int(data["security_amount"])
            if new_security_amount >= 0:
                # Must not drop below what's already been refunded/deducted
                # against this booking's security deposit - otherwise the
                # adjustment history would no longer be consistent with the
                # (now smaller) deposit it was adjusted against. Returned
                # directly (not raised) so this validation error goes
                # straight to the API response without passing through the
                # except (ValueError, TypeError) block below.
                from booking_v2.models import SecurityDepositAdjustment
                total_adjusted = SecurityDepositAdjustment.objects.filter(
                    booking=booking
                ).aggregate(total=Sum("amount"))["total"] or 0
                if new_security_amount < total_adjusted:
                    message = (
                        f"Security amount cannot be less than the total amount "
                        f"already refunded or deducted (₹{total_adjusted})."
                    )
                    return self.validation_error_response(
                        errors={"security_amount": [message]},
                        message=message,
                    )

                # Security amount is always stored exactly as received - no
                # inclusive-tax stripping. Tax on a taxable security amount
                # is accounted for at the Booking level only, in
                # _calculate_and_validate_totals.
                booking.security_amount = new_security_amount
                security_updated = True
            else:
                try:
                    raise ValueError("Security amount cannot be negative.")
                except ValueError as e:
                    raise e from None
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Invalid security amount value: {data.get('security_amount')} - {str(e)}",
                exc_info=True
            )
            raise ValueError(f"{str(e)}") from e

    # NOTE: security_payment_method is intentionally never read here anymore -
    # the security deposit's payment method is always derived from the
    # selected account's account_type (see _sync_security_deposit_payment),
    # never accepted manually. The key is still accepted on the request
    # (backward-compatible - no schema key removed) and simply ignored.

    # Canonical paid-flag update (alias supported for backward compatibility)
    if "security_amount_is_paid" in data or "security_is_paid" in data:
        resolved_flag, flag_error = self._resolve_security_paid_flag(
            data,
            default=security_amount_is_paid,
        )
        if flag_error:
            raise ValueError(flag_error)
        security_amount_is_paid = resolved_flag
        security_updated = True

    # New flow: security_account_id provided. account_id/payment_method are
    # only ever relevant when the security deposit is (or is becoming)
    # paid - when security_amount_is_paid is False, this whole block is
    # skipped entirely: security_account_id is never read from `data`,
    # never validated, never resolved. An unpaid PATCH can supply anything
    # (or garbage) in security_account_id with zero effect and no error -
    # the deposit is always saved with account=NULL, payment_method=NULL
    # (enforced centrally in _sync_security_deposit_payment).
    security_account = None
    security_paid_at = None

    if security_amount_is_paid:
        # Optional security_paid_at - never earlier than the booking's
        # created_at. The deposit row's own created_at is deliberately not a
        # bound: a deposit recorded late can carry its earlier real paid date.
        # Returned (not raised) so the caller rolls back the savepoint and
        # sends it straight through.
        security_paid_at, paid_at_error = self._resolve_security_paid_at(
            data,
            lower_bound=booking.created_at,
        )
        if paid_at_error:
            return paid_at_error
        if security_paid_at:
            # Refunds/deductions are validated against paid_at, so the
            # payment cannot move after the earliest existing adjustment.
            from booking_v2.models import SecurityDepositAdjustment
            first_adjustment_at = SecurityDepositAdjustment.objects.filter(
                booking=booking
            ).aggregate(first=Min("created_at"))["first"]
            if first_adjustment_at and security_paid_at > first_adjustment_at:
                message = "security paid date cannot be later than an existing security refund/deduction date."
                return self.validation_error_response(
                    errors={"security_paid_at": [message]},
                    message=message,
                )
            security_updated = True

        security_account_id = data.get('security_account_id')

        if security_account_id:
            from finance.models import Account as _Account
            try:
                security_account = _Account.objects.get(
                    id=security_account_id,
                    shop_id=self.shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except _Account.DoesNotExist:
                raise ValueError("Invalid or inactive account selected for security payment.")
            security_updated = True
        elif existing_sdp and existing_sdp.is_paid and existing_sdp.account_id:
            # Already paid and staying paid, no new account supplied this
            # request - reuse the existing account (Case C: edit amount/other
            # fields without having to resend the account every time). This
            # fallback deliberately does NOT apply on a false->true transition
            # (existing_sdp.is_paid is False there, or there's no existing_sdp
            # at all) - security_account_id must be explicitly supplied when
            # security is newly marked paid; _sync_security_deposit_payment
            # below returns a validation error if it's still missing at that
            # point. payment_method is always derived there too, from whichever
            # account ends up used - never passed in from the request.
            from finance.models import Account as _Account
            security_account = _Account.objects.filter(
                id=existing_sdp.account_id,
                deleted__isnull=True,
                is_active=True,
            ).first()

    # If security amount, paid-flag, or account changed, sync SecurityDepositPayment
    if security_updated:
        security_sync_error = self._sync_security_deposit_payment(
            booking=booking,
            security_amount=new_security_amount,
            security_amount_is_paid=security_amount_is_paid,
            payment_method=new_payment_method,
            savepoint=savepoint,
            account=security_account,
            paid_at=security_paid_at,
            check_balance=True,
        )
        if security_sync_error:
            return security_sync_error

    updatable_fields = ["client_address"]
    for field in updatable_fields:
        if field in data:
            setattr(booking, field, data[field])

    if '_validated_purchase_mode' in data:
        booking.purchase_mode = data['_validated_purchase_mode']

    # Update staff and client
    if '_validated_staff' in data:
        booking.staff = data['_validated_staff']
    if '_validated_client' in data:
        booking.client_identity = data['_validated_client']

    # Update rental duration type
    if '_validated_rental_duration_type' in data:
        booking.rental_duration_type = data['_validated_rental_duration_type']
        rental_duration_updated = True

    # Merge incoming 'details' JSON into booking.details (JSONField)
    if 'details' in data:
        incoming = data['details']
        try:
            # Accept JSON string or dict
            if isinstance(incoming, str):
                import json

                try:
                    incoming_parsed = json.loads(incoming)
                except Exception:
                    raise ValueError('Invalid JSON string for details field.')
            elif isinstance(incoming, dict):
                incoming_parsed = incoming
            else:
                raise ValueError('Details field must be a JSON string or an object.')

            if not isinstance(incoming_parsed, dict):
                raise ValueError('Parsed details must be a JSON object.')

            existing = booking.details or {}
            if not isinstance(existing, dict):
                existing = {}

            # Merge incoming keys into existing (incoming takes precedence)
            existing.update(incoming_parsed)
            booking.details = existing

        except ValueError:
            raise

    if rental_duration_updated and not any([
        security_updated,
        '_validated_staff' in data,
        '_validated_client' in data,
        any(field in data for field in updatable_fields),
        'details' in data
    ]):
        booking.save(update_fields=['rental_duration_type'])

    return None


def _build_success_response(self, booking, message="Booking details updated successfully.", data=None):
    """Build success response with calculated totals"""
    # Reuse totals already computed in _calculate_and_validate_totals when available,
    # to avoid re-querying BookedProductVariant/BookingAdditionalCharge from the DB.
    if data is not None and '_calculated_variants_total' in data and '_calculated_additional_charges_total' in data:
        variants_total = data['_calculated_variants_total']
        additional_charges_total = data['_calculated_additional_charges_total']
    else:
        current_booked_items = BookedProductVariant.objects.filter(booking=booking)
        current_additional_charges = BookingAdditionalCharge.objects.filter(booking=booking)

        variants_total = sum(float(item.amount) for item in current_booked_items)
        additional_charges_total = sum(float(charge.amount) for charge in current_additional_charges)

    final_payable = float(booking.total_amount) - float(booking.discount_amount or 0)

    return self.success_response(
        data={
            "total_amount": float(booking.total_amount),
            "variants_total": variants_total,
            "additional_charges_total": additional_charges_total,
            "discount_amount": float(booking.discount_amount or 0),
            "final_payable": final_payable,
            "paid_amount": float(booking.advance_amount or 0),
            "payment_status": booking.payment_status
        },
        message=message
    )
