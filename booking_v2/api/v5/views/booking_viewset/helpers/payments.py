import logging
from django.db import transaction
from django.utils import timezone
from booking_v2.models import PaymentDetailsV2, SecurityDepositPayment

logger = logging.getLogger("api_requests")


def _validate_amounts(self, variants, request_data):
    """
    Validate and calculate all amounts (products, additional charges, discount, security, advance).
    Returns (total_amount, discount, security, advance, additional_charges, error_response)
    """
    # Calculate product amounts
    try:
        product_total = sum([v.get("amount", 0) for v in variants])
        # if product_total <= 0:
        #     return None, None, None, None, None, None, self.validation_error_response(
        #         errors={"variants": ["Product amounts must be greater than zero."]},
        #         message="Product amounts must be greater than zero."
        #     )
    except Exception:
        return None, None, None, None, None, self.validation_error_response(
            errors={"variants": ["Invalid variant amounts."]},
            message="Invalid variant amounts."
        )

    # Validate additional charges
    additional_charges = request_data.get("additional_charges", [])
    additional_total = 0

    if additional_charges:
        if not isinstance(additional_charges, list):
            return None, None, None, None, None, self.validation_error_response(
                errors={"additional_charges": ["Additional charges must be a list."]},
                message="Invalid additional charges format."
            )

        for charge in additional_charges:
            if not isinstance(charge, dict) or "name" not in charge or "amount" not in charge:
                return None, None, None, None, None, self.validation_error_response(
                    errors={"additional_charges": ["Each additional charge must have 'name' and 'amount'."]},
                    message="Invalid additional charge format."
                )

            try:
                amount = float(charge["amount"])
                if amount <= 0:
                    return None, None, None, None, None, self.validation_error_response(
                        errors={"additional_charges": [f"Additional charge '{charge['name']}' amount must be positive."]},
                        message="Additional charge amounts must be positive."
                    )
                additional_total += amount
            except (ValueError, TypeError):
                return None, None, None, None, None, self.validation_error_response(
                    errors={"additional_charges": [f"Invalid amount for additional charge '{charge['name']}'."]},
                    message="Invalid additional charge amount."
                )

    # Total amount = product total + additional charges
    total_amount = product_total + additional_total

    # Discount validation
    discount = request_data.get("discount_amount", 0)
    try:
        discount = int(discount)
        if discount < 0:
            return None, None, None, None, None, self.validation_error_response(
                errors={"discount_amount": ["Discount cannot be negative."]},
                message="Discount cannot be negative."
            )
        if discount > total_amount:
            return None, None, None, None, None, self.validation_error_response(
                errors={"discount_amount": ["Discount amount cannot exceed total amount."]},
                message="Discount amount cannot exceed total amount."
            )
    except (ValueError, TypeError):
        return None, None, None, None, None, self.validation_error_response(
            errors={"discount_amount": ["Invalid discount amount."]},
            message="Invalid discount amount."
        )

    # Security validation
    security = request_data.get("security_amount", 0)
    # security_payment_method = request_data.get("security_payment_method", "cash")

    try:
        security = int(security)
        if security < 0:
            return None, None, None, None, None, self.validation_error_response(
                errors={"security_amount": ["Security amount cannot be negative."]},
                message="Security amount cannot be negative."
            )

        # If security_payment_method is provided in request, validate it
        # if "security_payment_method" in request_data:
        #     if security_payment_method not in self.VALID_PAYMENT_METHODS:
        #         return None, None, None, None, None, self.validation_error_response(
        #             errors={"security_payment_method": [
        #                 f"Invalid payment method. Must be one of: {', '.join(self.VALID_PAYMENT_METHODS)}"
        #             ]},
        #             message="Invalid security payment method."
        #         )
        # # If security > 0 but no method provided, use default
        # elif security > 0:
        #     security_payment_method = "cash"

    except Exception:
        return None, None, None, None, None, self.validation_error_response(
            errors={"security_amount": ["Invalid security amount."]},
            message="Invalid security amount."
        )

    # Advance validation
    advance = request_data.get("advance_amount", 0)
    try:
        advance = int(advance)
        if advance < 0:
            return None, None, None, None, None, self.validation_error_response(
                errors={"advance_amount": ["Advance amount must be positive."]},
                message="Advance amount must be positive."
            )
        # Ceiling check (advance vs total-after-discount) is intentionally NOT
        # done here: total_amount at this point is always pre-tax (tax is
        # applied afterward in create.py Step 5.5). The authoritative check
        # happens in _validate_payments(), which is called with the
        # tax-adjusted total_amount.
    except Exception:
        return None, None, None, None, None, self.validation_error_response(
            errors={"advance_amount": ["Invalid advance amount."]},
            message="Invalid advance amount."
        )

    return total_amount, discount, security, advance, additional_charges, None


def _resolve_security_paid_flag(self, request_data, default=True):
    """
    Resolve security paid flag from request payload.
    Canonical key: security_amount_is_paid
    Backward-compatible alias: security_is_paid
    Returns: (bool_value, error_message_or_None)
    """
    if "security_amount_is_paid" in request_data:
        raw = request_data.get("security_amount_is_paid")
    elif "security_is_paid" in request_data:
        raw = request_data.get("security_is_paid")
    else:
        return bool(default), None

    if isinstance(raw, bool):
        return raw, None

    if isinstance(raw, (int, float)):
        if raw in (0, 1):
            return bool(raw), None
        return bool(default), "security_amount_is_paid must be a boolean value."

    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True, None
        if normalized in {"false", "0", "no", "n", "off"}:
            return False, None

    return bool(default), "security_amount_is_paid must be a boolean value."


def _resolve_security_paid_at(self, request_data, lower_bound=None):
    """
    Resolve optional security_paid_at from request payload.
    Formats: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS (same as booked_date).
    Date-only values are combined with the current local time.

    Validation:
    - cannot be a future date (date-only compares dates only)
    - cannot be earlier than lower_bound (the booking's created_at);
      date-only compares dates, date+time compares at minute precision, and an
      accepted value still earlier than lower_bound is clamped up to it.

    Returns: (aware_datetime_or_None, error_response_or_None).
    None means "not supplied" - the caller keeps its existing default.
    """
    from datetime import datetime as _datetime

    raw = request_data.get("security_paid_at")
    if raw in (None, ""):
        return None, None

    raw = str(raw).strip()
    is_date_only = False
    try:
        paid_at = timezone.make_aware(_datetime.strptime(raw, "%d-%m-%YT%H:%M:%S"))
    except ValueError:
        try:
            parsed_date = _datetime.strptime(raw, "%d-%m-%Y").date()
        except ValueError:
            return None, self.validation_error_response(
                errors={"security_paid_at": ["Invalid format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."]},
                message="Invalid security_paid_at format.",
            )
        paid_at = timezone.make_aware(
            _datetime.combine(parsed_date, timezone.localtime(timezone.now()).time())
        )
        is_date_only = True

    now = timezone.now()
    if (paid_at.date() > timezone.localtime(now).date()) if is_date_only else (paid_at > now):
        return None, self.validation_error_response(
            errors={"security_paid_at": ["security paid date cannot be a future date."]},
            message="Security paid date cannot be a future date.",
        )

    if lower_bound:
        # Date-only compares dates; date+time compares at minute precision so
        # a client sending "now" (seconds behind the server's created_at) is
        # not rejected. Anything accepted but still earlier is clamped up.
        local_bound = timezone.localtime(lower_bound)
        if is_date_only:
            too_early = paid_at.date() < local_bound.date()
        else:
            too_early = paid_at < local_bound.replace(second=0, microsecond=0)
        if too_early:
            return None, self.validation_error_response(
                errors={"security_paid_at": ["security paid date cannot be earlier than the booking date."]},
                message="Security paid date cannot be earlier than the booking date.",
            )
        if paid_at < lower_bound:
            paid_at = lower_bound

    return paid_at, None


def _security_deduction_balance_error(self, existing_payment, new_amount, is_paid, new_account):
    """
    Balance check before a paid security deposit's money leaves its account
    (marked unpaid, reduced, removed, or moved to another account).

    Measured from the existing ledger entry - what was actually credited - and
    checked against the account's live balance, with the account row locked
    (same pattern as deleting an Investment/ManualIncome inflow). Runs before
    any write, so a rejection leaves no ledger/daily-balance changes behind.
    Returns error_response or None.
    """
    from decimal import Decimal
    from finance.models import LedgerEntry
    from finance.api.v3.utils import get_available_balance_as_of
    from ERP.utils.refund_validation import format_rupees

    if not existing_payment:
        return None
    ledger = LedgerEntry.objects.filter(
        reference_id=existing_payment.id,
        reference_type='SecurityDepositPayment',
        deleted__isnull=True,
    ).select_related('account').first()
    if not ledger or not ledger.account_id:
        return None  # nothing was credited, so nothing is taken back

    credited = Decimal(str(ledger.amount or 0))
    stays_in_account = is_paid and new_account is not None and new_account.id == ledger.account_id
    deduction = credited - (new_amount if stays_in_account else Decimal("0"))
    if deduction <= 0:
        return None

    available = Decimal(str(get_available_balance_as_of(ledger.account_id, timezone.now(), lock_for_update=True)))
    if deduction <= available:
        return None

    if new_amount <= 0:
        field, action = "security_amount", "remove the security deposit"
    elif not is_paid:
        field, action = "security_amount_is_paid", "mark the security deposit as unpaid"
    elif not stays_in_account:
        field, action = "security_account_id", f"move the security deposit to {new_account.name}"
    else:
        field = "security_amount"
        action = f"reduce the security deposit from {format_rupees(credited)} to {format_rupees(new_amount)}"

    account_name = ledger.account.name if ledger.account else "the account"
    message = (
        f"Insufficient balance in {account_name} to {action}. "
        f"Required: {format_rupees(deduction)}, available: {format_rupees(max(available, Decimal('0')))}."
    )
    return self.validation_error_response(
        errors={field: [message]},
        message=message,
        dev_message=f"account_id={ledger.account_id}, available={available}, required={deduction}",
    )


def _sync_security_deposit_payment(
    self,
    booking,
    security_amount,
    security_amount_is_paid=True,
    payment_method=None,
    savepoint=None,
    account=None,
    effective_dt=None,
    paid_at=None,
    check_balance=False,
):
    """
    Centralized sync for SecurityDepositPayment and corresponding ledger rows.

    Behavior:
    - security_amount <= 0: remove security payment and related ledger entries.
    - security_amount > 0 and security_amount_is_paid=False: keep payment row as unpaid, no ledger entry.
    - security_amount > 0 and security_amount_is_paid=True: ensure payment row is paid and ledger entry exists.
    - Prevent duplicate ledger creation by reusing existing ledger for same reference.
    - paid_at stores the real timestamp when status transitions to paid.
      An explicit, already-validated `paid_at` (security_paid_at) always wins,
      also for an already-paid record; the ledger date follows it.

    security_amount is always stored exactly as received - no inclusive-tax
    stripping is applied here or upstream.

    check_balance=True (Update Booking Details only) rejects the change when
    money already credited would leave an account without enough balance -
    see _security_deduction_balance_error.
    """
    from decimal import Decimal

    try:
        # Security collection is always positive; abs() guards against callers
        # passing a negative value, which would otherwise trigger deletion.
        security_amount_dec = abs(Decimal(str(security_amount or 0)))
        is_paid = bool(security_amount_is_paid)

        existing_payment = SecurityDepositPayment.objects.filter(booking=booking).first()

        # Remove payment completely when security amount is zero.
        if security_amount_dec <= 0:
            if check_balance:
                balance_error = _security_deduction_balance_error(
                    self, existing_payment, security_amount_dec, is_paid, account,
                )
                if balance_error:
                    return balance_error
            if existing_payment:
                if existing_payment.account_id:
                    from finance.api.v3.utils import (
                        delete_ledger_entries as _delete_ledger_entries,
                        update_daily_balance as _update_daily_balance,
                    )

                    _delete_ledger_entries(
                        reference_ids=[existing_payment.id],
                        reference_type='SecurityDepositPayment',
                    )
                    _update_daily_balance(
                        shop=booking.shop_id,
                        account=existing_payment.account_id,
                        date=existing_payment.created_at.date(),
                    )
                existing_payment.delete()
            return None

        # Account/payment_method resolution.
        #
        # Paid: account is mandatory - no falling back to a previously-stored
        # account or deriving one from payment_method. The caller
        # (create.py / _update_other_fields) is responsible for resolving
        # and validating the Account object from security_account_id and
        # passing it in as `account`; if none was supplied (or the caller
        # explicitly passed None), that's a hard validation error rather
        # than a silent skip. payment_method is NEVER accepted from the
        # request when paid - it is always derived from the resolved
        # account's account_type.
        #
        if is_paid:
            if account is None:
                return self.validation_error_response(
                    errors={"security_account_id": ["security_account_id is required when security_amount_is_paid is true."]},
                    message="account is required when security amount is marked as paid.",
                )
            payment_method = 'cash' if account.account_type == 'cash' else 'upi'
        else:
            # Unpaid: account/payment_method are always NULL, regardless of
            # whatever the caller passed in - create.py/_update_other_fields
            # never even resolve an account when unpaid in the first place,
            # so `account` arrives here already None in practice; this
            # branch enforces it unconditionally either way, as the single
            # source of truth. payment_method must never default to "cash".
            account = None
            payment_method = None

        if check_balance:
            balance_error = _security_deduction_balance_error(
                self, existing_payment, security_amount_dec, is_paid, account,
            )
            if balance_error:
                return balance_error

        if existing_payment:
            old_account_id = existing_payment.account_id
            old_amount = Decimal(str(existing_payment.amount or 0))
            old_is_paid = bool(existing_payment.is_paid)

            amount_changed = old_amount != security_amount_dec
            new_account_id = account.id if account else None
            account_changed = old_account_id != new_account_id
            paid_transition_to_true = (not old_is_paid) and is_paid
            old_paid_at = existing_payment.paid_at

            existing_payment.amount = security_amount_dec
            existing_payment.payment_method = payment_method
            existing_payment.account = account
            existing_payment.is_paid = is_paid
            update_fields = ['amount', 'payment_method', 'account_id', 'is_paid', 'paid_at']

            if is_paid:
                if paid_at:
                    existing_payment.paid_at = paid_at
                elif paid_transition_to_true or not existing_payment.paid_at:
                    existing_payment.paid_at = effective_dt if effective_dt else timezone.now()
            else:
                existing_payment.paid_at = None

            paid_at_changed = old_paid_at != existing_payment.paid_at

            existing_payment.save(update_fields=update_fields)
            record = existing_payment
        else:
            new_paid_at = (paid_at or effective_dt or timezone.now()) if is_paid else None
            record = SecurityDepositPayment.objects.create(
                booking=booking,
                amount=security_amount_dec,
                payment_method=payment_method,
                account=account,
                is_paid=is_paid,
                paid_at=new_paid_at,
                **({"created_at": effective_dt} if effective_dt else {}),
            )
            amount_changed = True
            account_changed = True
            paid_at_changed = False
            old_account_id = None

        from finance.models import LedgerEntry
        existing_ledger = LedgerEntry.objects.filter(
            reference_id=record.id,
            reference_type='SecurityDepositPayment',
            deleted__isnull=True,
        ).first()

        # Unpaid security should never have active ledger rows.
        if not is_paid:
            if existing_ledger:
                from finance.api.v3.utils import (
                    delete_ledger_entries as _delete_ledger_entries,
                    update_daily_balance as _update_daily_balance,
                )

                ledger_account_id = existing_ledger.account_id
                ledger_date = existing_ledger.created_at.date()
                _delete_ledger_entries(
                    reference_ids=[record.id],
                    reference_type='SecurityDepositPayment',
                )
                if ledger_account_id:
                    _update_daily_balance(
                        shop=booking.shop_id,
                        account=ledger_account_id,
                        date=ledger_date,
                    )
            return None

        # Paid security always has an account here - enforced above.
        if existing_ledger:
            update_fields = []
            if amount_changed:
                existing_ledger.amount = security_amount_dec
                update_fields.append('amount')
            # Old (account, date) slot, taken from the ledger row itself.
            # Dates are local (IST): update_daily_balance filters
            # created_at__date in the active timezone, while DB values come
            # back in UTC - a plain .date() picks the wrong day for
            # 00:00-05:29 IST and would leave the amount on the old day.
            old_ledger_account_id = existing_ledger.account_id
            old_ledger_date = timezone.localtime(existing_ledger.created_at).date()
            if account_changed:
                existing_ledger.account = account
                update_fields.append('account')
            if paid_at_changed and record.paid_at:
                # Ledger date follows the security payment's paid_at.
                existing_ledger.created_at = timezone.localtime(record.paid_at)
                update_fields.append('created_at')

            if update_fields:
                # Use a full save so model-level signed amount recalculation (if any) always runs.
                existing_ledger.save()
                from finance.api.v3.utils import update_daily_balance as _update_daily_balance
                ledger_date = timezone.localtime(existing_ledger.created_at).date()
                # Rebalance the old slot first when the account or date moved,
                # then the new one; update_daily_balance carries closing
                # balances forward, so all later days end up consistent.
                if old_ledger_account_id and (
                    old_ledger_account_id != existing_ledger.account_id
                    or old_ledger_date != ledger_date
                ):
                    _update_daily_balance(
                        shop=booking.shop_id,
                        account=old_ledger_account_id,
                        date=old_ledger_date,
                    )
                _update_daily_balance(
                    shop=booking.shop_id,
                    account=existing_ledger.account_id,
                    date=ledger_date,
                )
        else:
            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries

            # Local time so create_ledger_entries' .date() matches the IST
            # day update_daily_balance filters on.
            ledger_time = timezone.localtime(record.paid_at or timezone.now())
            _create_ledger_entries(
                payments=[{
                    'amount': security_amount_dec,
                    'direction': 'in',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': record.id,
                    'created_at': ledger_time,
                }],
                shop=booking.shop,
                transaction_type='security_in',
                reference_type='SecurityDepositPayment',
            )

        return None

    except Exception as e:
        logger.error(f"Failed to sync security deposit payment for booking {booking.id}: {str(e)}")
        if savepoint:
            transaction.savepoint_rollback(savepoint)
        return self.error_response(
            message="Failed to sync security deposit payment record.",
            dev_message=str(e)
        )


def _create_security_deposit_payment(self, booking, security_amount, payment_method, account=None, security_amount_is_paid=True, savepoint=None):
    """
    Create security deposit payment record if security amount > 0.
    If account is provided (new flow), creates a ledger entry (direction='in').
    Returns error_response or None
    """
    return _sync_security_deposit_payment(
        self,
        booking=booking,
        security_amount=security_amount,
        security_amount_is_paid=security_amount_is_paid,
        payment_method=payment_method,
        savepoint=savepoint,
        account=account,
    )


def _create_payment_records(self, booking, validated_payments, shop=None, effective_dt=None):
    """
    Create payment records for the booking.
    New flow: payments with 'account' key create ledger entries (direction='in').
    Old flow: payments without 'account' are bulk-created, no ledger.
    Returns error_response or None
    """
    if not validated_payments:
        return None

    try:
        old_flow_objects = []
        new_flow_ledger_payments = []

        for payment in validated_payments:
            if not isinstance(payment, dict):
                logger.error(f"Invalid payment structure: {payment}")
                continue

            if "amount" not in payment or "payment_method" not in payment:
                logger.error(f"Missing required payment fields: {payment}")
                continue

            account = payment.get("account")

            if account:
                # NEW FLOW: create individually to get ID for ledger entry
                record = PaymentDetailsV2.objects.create(
                    booking=booking,
                    payment_amount=payment["amount"],
                    payment_method=payment["payment_method"],
                    account=account,
                    **({"created_at": effective_dt} if effective_dt else {}),
                )
                new_flow_ledger_payments.append({
                    'amount': payment["amount"],
                    'direction': 'in',
                    'account': account,
                    'payment_method': payment["payment_method"],
                    'reference_id': record.id,
                    'created_at': record.created_at,
                })
            else:
                # OLD FLOW: bulk create, no ledger
                old_flow_objects.append(
                    PaymentDetailsV2(
                        booking=booking,
                        payment_amount=payment["amount"],
                        payment_method=payment["payment_method"],
                        **({"created_at": effective_dt} if effective_dt else {}),
                    )
                )

        if old_flow_objects:
            PaymentDetailsV2.objects.bulk_create(old_flow_objects)

        if new_flow_ledger_payments and shop:
            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
            _create_ledger_entries(
                payments=new_flow_ledger_payments,
                shop=shop,
                transaction_type='booking_payment',
                reference_type='PaymentDetailsV2',
            )

        if not old_flow_objects and not new_flow_ledger_payments:
            return self.validation_error_response(
                errors={"payments": ["No valid payment entries found."]},
                message="Invalid payment data."
            )

        return None

    except Exception as e:
        logger.error(
            f"Failed to create payment records for booking {booking.id}: {str(e)}"
        )
        return self.error_response(
            message="Failed to create payment records.",
            dev_message=str(e)
        )


def _update_security_deposit_payment(self, booking, new_security_amount, payment_method=None, savepoint=None, account=None, security_amount_is_paid=None):
    """
    Update or create security deposit payment record.
    - created_at is NEVER modified on existing records (SecurityDepositPayment or LedgerEntry).
    - Existing ledger entries are updated in-place (amount/account only); never deleted and recreated.
    - A new ledger entry is created only when none exists yet for the payment.
    """
    if security_amount_is_paid is None:
        existing_payment = SecurityDepositPayment.objects.filter(booking=booking).first()
        security_amount_is_paid = existing_payment.is_paid if existing_payment else True

    return _sync_security_deposit_payment(
        self,
        booking=booking,
        security_amount=new_security_amount,
        security_amount_is_paid=security_amount_is_paid,
        payment_method=payment_method,
        savepoint=savepoint,
        account=account,
    )


def _validate_payments(self, request_data, total_amount, discount):
    """
    Validate payments and return validated payment entries.
    New flow: each payment (or top-level) can have account_id instead of payment_method.
    Old flow: payment_method-based (unchanged).
    Returns (validated_payments, error_response)
    """
    advance_amount = request_data.get("advance_amount", 0)
    payment_method = request_data.get("payment_method")  # old flow (single payment)
    account_id = request_data.get("account_id")           # new flow (single payment)
    payments_data = request_data.get("payments")           # split payments (old or new)

    # Validate advance amount with total/discount (existing logic)
    try:
        advance_amount = int(advance_amount)
        if advance_amount < 0:
            return None, self.validation_error_response(
                errors={"advance_amount": ["Advance amount must be positive."]},
                message="Advance amount must be positive."
            )
        final_payable = total_amount - discount
        if advance_amount > final_payable:
            return None, self.validation_error_response(
                errors={"advance_amount": ["Advance cannot be more than final total amount."]},
                message="Advance cannot be more than total amount."
            )
    except (ValueError, TypeError) as e:
        return None, self.validation_error_response(
            errors={"advance_amount": [f"Invalid advance amount: {str(e)}"]},
            message="Invalid advance amount."
        )

    validated_payments = []

    # CASE 1: New split payments flow
    if payments_data is not None:
        if not isinstance(payments_data, list):
            return None, self.validation_error_response(
                errors={"payments": ["Payments must be a list."]},
                message="Invalid payments format."
            )

        if not payments_data:
            if advance_amount == 0:
                return [], None
            return None, self.validation_error_response(
                errors={"payments": ["Payments list cannot be empty when advance_amount is greater than zero."]},
                message="Payments list cannot be empty."
            )

        total_split_amount = 0

        for idx, payment in enumerate(payments_data):
            if not isinstance(payment, dict):
                return None, self.validation_error_response(
                    errors={"payments": [f"Payment at index {idx} must be an object."]},
                    message="Invalid payment format."
                )

            p_account_id = payment.get("account_id")
            method = payment.get("payment_method")
            amount = payment.get("amount")

            if amount is None:
                return None, self.validation_error_response(
                    errors={"payments": [f"Payment at index {idx} missing amount."]},
                    message="Payment amount missing."
                )

            # Resolve account or payment_method
            p_account = None
            if p_account_id:
                # NEW FLOW: account-based
                from finance.models import Account as _Account
                try:
                    p_account = _Account.objects.get(
                        id=p_account_id,
                        shop_id=self.shop_id,
                        deleted__isnull=True,
                        is_active=True,
                    )
                except _Account.DoesNotExist:
                    return None, self.validation_error_response(
                        errors={"payments": [f"Invalid or inactive account at index {idx}."]},
                        message="Invalid account selected."
                    )
                method = 'cash' if p_account.account_type == 'cash' else 'upi'
            elif method:
                # OLD FLOW: payment_method-based
                if method not in self.VALID_PAYMENT_METHODS:
                    return None, self.validation_error_response(
                        errors={"payments": [f"Invalid payment method '{method}' at index {idx}. Must be one of: {', '.join(self.VALID_PAYMENT_METHODS)}"]},
                        message="Invalid payment method."
                    )
            else:
                return None, self.validation_error_response(
                    errors={"payments": [f"Payment at index {idx} must have either account_id or payment_method."]},
                    message="Either payment_method or account_id is required for each payment."
                )

            # Validate amount
            try:
                amount = int(amount)
                if amount <= 0:
                    return None, self.validation_error_response(
                        errors={"payments": [f"Payment amount at index {idx} must be greater than zero."]},
                        message="Invalid payment amount."
                    )
                total_split_amount += amount
            except (ValueError, TypeError):
                return None, self.validation_error_response(
                    errors={"payments": [f"Invalid amount for payment at index {idx}."]},
                    message="Invalid payment amount."
                )

            entry = {"payment_method": method, "amount": amount}
            if p_account:
                entry["account"] = p_account
            validated_payments.append(entry)

        # Validate total split amount equals advance_amount
        if total_split_amount != advance_amount:
            return None, self.validation_error_response(
                errors={"payments": [f"Sum of payments ({total_split_amount}) must equal advance_amount ({advance_amount})."]},
                message="Payment amount mismatch."
            )

    # CASE 2: Single payment (old or new flow)
    elif advance_amount > 0:
        if account_id:
            # NEW FLOW: account-based
            from finance.models import Account as _Account
            try:
                account = _Account.objects.get(
                    id=account_id,
                    shop_id=self.shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except _Account.DoesNotExist:
                return None, self.validation_error_response(
                    errors={"account_id": ["Invalid or inactive account."]},
                    message="Invalid account selected."
                )
            method = 'cash' if account.account_type == 'cash' else 'upi'
            validated_payments.append({"payment_method": method, "amount": advance_amount, "account": account})

        elif payment_method:
            # OLD FLOW: payment_method-based
            if payment_method not in self.VALID_PAYMENT_METHODS:
                return None, self.validation_error_response(
                    errors={"payment_method": [f"Invalid payment method. Must be one of: {', '.join(self.VALID_PAYMENT_METHODS)}"]},
                    message="Invalid payment method."
                )
            validated_payments.append({"payment_method": payment_method, "amount": advance_amount})

        else:
            return None, self.validation_error_response(
                errors={"payment_method": ["Either payment_method or account_id is required for advance amount."]},
                message="Either payment_method or account_id is required."
            )

    # CASE 3: No advance payment
    else:
        # advance_amount is 0, no payments needed
        pass

    return validated_payments, None
