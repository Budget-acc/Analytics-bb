import logging
from decimal import Decimal
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger("api_requests")


def _resolve_security_paid_flag(self, data, default=True):
    """
    Resolve the security paid flag from request payload.
    Canonical key: security_amount_is_paid
    Returns: (bool_value, error_message_or_None)
    """
    if "security_amount_is_paid" not in data:
        return bool(default), None

    raw = data.get("security_amount_is_paid")

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


def _sync_tailor_security_payment(
    self,
    order,
    security_amount,
    security_amount_is_paid=True,
    payment_method=None,
    account=None,
    effective_dt=None,
):
    """
    Centralized sync for TailorOrderSecurityPayment and corresponding ledger rows.

    security_amount is always stored exactly as received - no inclusive-tax
    stripping is applied here or upstream.

    Behavior:
    - security_amount <= 0: remove security payment record and related ledger entries.
    - security_amount > 0 and security_amount_is_paid=False:
        keep/create payment row as unpaid, delete any existing ledger entry.
    - security_amount > 0 and security_amount_is_paid=True:
        ensure payment row is paid with correct paid_at, create/update ledger entry.
    - Prevents duplicate ledger creation by detecting existing entries.
    - paid_at stores the real timestamp when status transitions to paid.

    Ledger convention (matches existing tailor order pattern):
        reference_id   = order.id
        reference_type = 'TailorOrderSecurity'
    """
    from tailored.models import TailorOrderSecurityPayment
    from finance.api.v3.utils import (
        create_ledger_entries as _create_ledger_entries,
        delete_ledger_entries as _delete_ledger_entries,
        update_daily_balance as _update_daily_balance,
    )
    from finance.models import LedgerEntry

    try:
        security_amount_dec = abs(Decimal(str(security_amount or 0)))
        is_paid = bool(security_amount_is_paid)

        existing_payment = TailorOrderSecurityPayment.objects.filter(order=order).first()

        logger.info(
            f"[SecuritySync] order={order.id} amount={security_amount_dec} "
            f"is_paid={is_paid} existing_record={existing_payment is not None} "
            f"account={'set' if account else 'None'}"
        )

        # ── Case 1: security amount removed ──────────────────────────────────
        if security_amount_dec <= 0:
            logger.info(f"[SecuritySync] amount=0 -> removing payment record if present")
            if existing_payment:
                if existing_payment.account_id:
                    _delete_ledger_entries(
                        reference_ids=[order.id],
                        reference_type='TailorOrderSecurity',
                    )
                    _update_daily_balance(
                        shop=order.shop_id,
                        account=existing_payment.account_id,
                        date=existing_payment.created_at.date(),
                    )
                existing_payment.delete()
            return None

        # ── Resolve payment_method / account fallbacks ───────────────────────
        if payment_method is None:
            payment_method = existing_payment.payment_method if existing_payment else "cash"

        if account is None and existing_payment and existing_payment.account_id:
            from finance.models import Account as _Account
            account = _Account.objects.filter(
                id=existing_payment.account_id,
                deleted__isnull=True,
                is_active=True,
            ).first()
            logger.info(
                f"[SecuritySync] account resolved from existing record: "
                f"{'found' if account else 'not found (deleted or inactive)'}"
            )

        # ── Upsert TailorOrderSecurityPayment ────────────────────────────────
        if existing_payment:
            old_account_id = existing_payment.account_id
            old_amount = Decimal(str(existing_payment.amount or 0))
            old_is_paid = bool(existing_payment.is_paid)

            amount_changed = old_amount != security_amount_dec
            new_account_id = account.id if account else None
            account_changed = old_account_id != new_account_id
            paid_transition_to_true = (not old_is_paid) and is_paid

            logger.info(
                f"[SecuritySync] updating existing record id={existing_payment.id} "
                f"old_is_paid={old_is_paid} -> new_is_paid={is_paid} "
                f"transition_to_paid={paid_transition_to_true} "
                f"amount_changed={amount_changed} account_changed={account_changed}"
            )

            existing_payment.amount = security_amount_dec
            existing_payment.payment_method = payment_method
            existing_payment.account = account
            existing_payment.is_paid = is_paid
            update_fields = ['amount', 'payment_method', 'account_id', 'is_paid', 'paid_at']

            if is_paid:
                if paid_transition_to_true or not existing_payment.paid_at:
                    existing_payment.paid_at = effective_dt if effective_dt else timezone.now()
                    logger.info(f"[SecuritySync] paid_at stamped = {existing_payment.paid_at}")
                else:
                    logger.info(f"[SecuritySync] paid_at already set = {existing_payment.paid_at}, not overwriting")
            else:
                existing_payment.paid_at = None
                logger.info(f"[SecuritySync] paid_at cleared (is_paid=False)")

            existing_payment.save(update_fields=update_fields)
            logger.info(f"[SecuritySync] record saved successfully id={existing_payment.id}")
            record = existing_payment
        else:
            paid_at = (effective_dt if effective_dt else timezone.now()) if is_paid else None
            record = TailorOrderSecurityPayment.objects.create(
                order=order,
                amount=security_amount_dec,
                payment_method=payment_method,
                account=account,
                is_paid=is_paid,
                paid_at=paid_at,
                **({"created_at": effective_dt} if effective_dt else {}),
            )
            logger.info(
                f"[SecuritySync] created new record id={record.id} "
                f"is_paid={is_paid} paid_at={paid_at}"
            )
            amount_changed = True
            account_changed = True
            old_account_id = None

        # ── Ledger management ─────────────────────────────────────────────────
        existing_ledger = LedgerEntry.objects.filter(
            reference_id=order.id,
            reference_type='TailorOrderSecurity',
            deleted__isnull=True,
        ).first()

        logger.info(
            f"[SecuritySync] ledger check: is_paid={is_paid} "
            f"existing_ledger={'found id=' + str(existing_ledger.id) if existing_ledger else 'None'}"
        )

        # Case 2: unpaid — no ledger should exist
        if not is_paid:
            if existing_ledger:
                logger.info(f"[SecuritySync] is_paid=False -> deleting existing ledger id={existing_ledger.id}")
                ledger_account_id = existing_ledger.account_id
                ledger_date = existing_ledger.created_at.date()
                _delete_ledger_entries(
                    reference_ids=[order.id],
                    reference_type='TailorOrderSecurity',
                )
                if ledger_account_id:
                    _update_daily_balance(
                        shop=order.shop_id,
                        account=ledger_account_id,
                        date=ledger_date,
                    )
            else:
                logger.info(f"[SecuritySync] is_paid=False and no ledger exists — nothing to clean up")
            return None

        # Case 3: paid — ensure ledger exists and is current
        if not account:
            logger.warning(
                f"[SecuritySync] is_paid=True but account=None for order {order.id}; "
                f"ledger NOT created — security_account_id is missing or unlinked."
            )
            return None

        if existing_ledger:
            update_fields = []
            if amount_changed:
                existing_ledger.amount = security_amount_dec
                update_fields.append('amount')
            if account_changed:
                existing_ledger.account = account
                update_fields.append('account')

            if update_fields:
                logger.info(f"[SecuritySync] updating existing ledger id={existing_ledger.id} fields={update_fields}")
                existing_ledger.save()
                ledger_date = existing_ledger.created_at.date()
                if account_changed and old_account_id:
                    _update_daily_balance(
                        shop=order.shop_id,
                        account=old_account_id,
                        date=ledger_date,
                    )
                _update_daily_balance(
                    shop=order.shop_id,
                    account=existing_ledger.account_id,
                    date=ledger_date,
                )
            else:
                logger.info(f"[SecuritySync] existing ledger id={existing_ledger.id} is up-to-date — no changes needed")
        else:
            ledger_time = record.paid_at or timezone.now()
            logger.info(f"[SecuritySync] no ledger found -> creating new ledger entry")
            _create_ledger_entries(
                payments=[{
                    'amount': security_amount_dec,
                    'direction': 'in',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': order.id,
                    'created_at': ledger_time,
                }],
                shop=order.shop,
                transaction_type='tailor_order_security_in',
                reference_type='TailorOrderSecurity',
            )
            logger.info(f"[SecuritySync] ledger created successfully")

        return None

    except Exception as e:
        logger.error(
            f"Failed to sync security payment for TailorOrder {order.id}: {str(e)}",
            exc_info=True,
        )
        return self.error_response(
            message="Failed to sync security payment record.",
            dev_message=str(e),
        )
