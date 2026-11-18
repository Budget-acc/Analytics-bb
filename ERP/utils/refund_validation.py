from decimal import Decimal

from django.db.models import DecimalField, F, Min, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime

_AMOUNT_FIELD = DecimalField(max_digits=14, decimal_places=2)
_ZERO = Value(Decimal("0"), output_field=_AMOUNT_FIELD)


def _sum(queryset, field):
    total = queryset.aggregate(
        total=Coalesce(Sum(field, output_field=_AMOUNT_FIELD), _ZERO)
    )["total"]
    return Decimal(str(total))


def _format_amount(value):
    return f"{value:.0f}" if value == value.to_integral_value() else f"{value:.2f}"


def format_rupees(value):
    """₹ amount with thousands separators, e.g. ₹2,800 or ₹2,800.50."""
    value = Decimal(str(value or 0))
    return f"₹{value:,.0f}" if value == value.to_integral_value() else f"₹{value:,.2f}"


def _display_at(dt):
    return localtime(dt).strftime("%d-%m-%Y %H:%M")


def lowest_running_balance(opening_balance, deltas):
    """
    Lowest balance reached from `opening_balance` onwards, applying the
    {moment: signed_delta} changes in chronological order. Pure function (no DB).
    """
    balance = lowest = opening_balance
    for moment in sorted(deltas):
        balance += deltas[moment]
        lowest = min(lowest, balance)
    return lowest


def is_before_payment(refund_at, paid_at, is_date_only):
    """
    True when a refund dated `refund_at` would predate the payment at
    `paid_at`. Date-only refunds compare by local calendar day, so a same-day
    refund is always allowed. Pure function (no DB).
    """
    local_refund_at, local_paid_at = localtime(refund_at), localtime(paid_at)
    if is_date_only:
        return local_refund_at.date() < local_paid_at.date()
    return local_refund_at < local_paid_at


def refundable_as_of(refund_dt, payment_events, refund_events):
    """
    Amount refundable at the exact moment `refund_dt`, from (aware_datetime, amount)
    events. Payments/refunds at or before `refund_dt` count; later refunds may not
    be pushed below zero, so the result is the lowest running balance from
    `refund_dt` onwards. Pure function (no DB).
    Returns (refundable, paid_as_of, refunded_as_of) as Decimals.
    """
    paid_as_of = refunded_as_of = Decimal("0")
    later_deltas = {}
    for events, sign in ((payment_events, 1), (refund_events, -1)):
        for at, value in events:
            value = Decimal(str(value or 0))
            if at <= refund_dt:
                if sign > 0:
                    paid_as_of += value
                else:
                    refunded_as_of += value
            else:
                later_deltas[at] = later_deltas.get(at, Decimal("0")) + sign * value
    refundable = lowest_running_balance(paid_as_of - refunded_as_of, later_deltas)
    return refundable, paid_as_of, refunded_as_of


def validate_refund_against_paid_as_of(amount, refund_dt, payments_qs, payment_field,
                                       refunds_qs, refund_field):
    """
    Ensure a backdated refund is covered by the payments received at or before the
    exact refund date/time AND keeps every later moment covered - refunds already
    recorded after the refund must not end up exceeding what was paid.
    Returns error kwargs for validation_error_response(), or None.
    """
    payment_events = list(payments_qs.values_list("created_at", payment_field))
    refund_events = list(refunds_qs.values_list("created_at", refund_field))
    refundable, paid_as_of, refunded_as_of = refundable_as_of(
        refund_dt, payment_events, refund_events
    )
    amount = Decimal(str(amount))
    display_at = _display_at(refund_dt)
    available = paid_as_of - refunded_as_of
    dev_message = (
        f"paid_as_of={paid_as_of}, refunded_as_of={refunded_as_of}, available_as_of={available}, "
        f"refundable={refundable}, refund_at={localtime(refund_dt).isoformat()}, requested={amount}"
    )

    def _error(field, message):
        return {"errors": {field: [message]}, "message": message, "dev_message": dev_message}

    # Refund date/time is before any payment was received
    if paid_as_of <= 0:
        first_paid_at = min((at for at, _ in payment_events), default=None)
        if first_paid_at is None:
            return _error("date", "No payment has been received yet, so nothing can be refunded.")
        return _error(
            "date",
            f"No payment was received by {display_at}. "
            f"First payment was on {_display_at(first_paid_at)}.",
        )

    # Everything paid by then had already been refunded by then
    if available <= 0:
        return _error(
            "amount",
            f"Nothing to refund as of {display_at}. "
            f"The {format_rupees(paid_as_of)} paid by then was already refunded.",
        )

    # More than paid minus refunded as of the refund date/time
    if amount > available:
        return _error(
            "amount",
            f"Only {format_rupees(available)} can be refunded as of {display_at} "
            f"({format_rupees(paid_as_of)} paid, {format_rupees(refunded_as_of)} refunded).",
        )

    # Within that, but refunds recorded later would then exceed the payments
    if amount > refundable:
        if refundable <= 0:
            return _error(
                "amount",
                f"Nothing can be refunded as of {display_at}. "
                f"Later refunds already use the {format_rupees(available)} available.",
            )
        return _error(
            "amount",
            f"Only {format_rupees(refundable)} can be refunded as of {display_at}. "
            f"{format_rupees(available)} was available, but later refunds already use "
            f"{format_rupees(available - refundable)}.",
        )

    return None


def validate_security_refund_after_payment(refund_dt, is_date_only, amount,
                                           security_payments_qs, adjustments_qs,
                                           paid_date_field, owner_label="booking"):
    """
    Security refund checks, based on when the security amount was actually paid:
      1. a paid security deposit must exist;
      2. the refund cannot predate the (first) security payment;
      3. the refund cannot exceed the security paid as of the refund date/time,
         minus security adjustments (refunds + deductions) recorded by then.

    paid_date_field: the field holding the payment moment - "paid_at" for
      Booking SecurityDepositPayment, "created_at" for Tailor Order security.
      There is NO fallback between fields: a Booking payment marked paid but
      with no paid_at is treated as not paid, so it is not refundable.
    Date-only refunds compare by local calendar day.
    Returns error kwargs for validation_error_response(), or None.
    """
    paid_qs = security_payments_qs.filter(
        is_paid=True, **{f"{paid_date_field}__isnull": False}
    ).annotate(effective_paid_at=F(paid_date_field))

    first_paid_at = paid_qs.aggregate(first=Min("effective_paid_at"))["first"]
    if first_paid_at is None:
        missing_paid_date = security_payments_qs.filter(
            is_paid=True, **{f"{paid_date_field}__isnull": True}
        ).exists()
        return {
            "errors": {"date": [f"No paid security deposit found for this {owner_label}."]},
            "message": "Security amount has not been paid yet.",
            "dev_message": (
                f"Security payment is marked paid but {paid_date_field} is null; "
                "it is not refundable until the payment date is recorded."
                if missing_paid_date
                else "No security payment with is_paid=true exists."
            ),
        }

    local_refund_at = localtime(refund_dt)
    local_paid_at = localtime(first_paid_at)
    if is_before_payment(refund_dt, first_paid_at, is_date_only):
        return {
            "errors": {"date": [f"Security was paid on {local_paid_at.strftime('%d-%m-%Y %H:%M')}."]},
            "message": "Refund date cannot be earlier than the security payment date.",
            "dev_message": (
                f"refund_at={local_refund_at.isoformat()}, "
                f"security_paid_at={local_paid_at.isoformat()}"
            ),
        }

    # Security available as of the refund date/time
    if is_date_only:
        refund_date = local_refund_at.date()
        paid_as_of = _sum(paid_qs.filter(effective_paid_at__date__lte=refund_date), "amount")
        adjusted_as_of = _sum(adjustments_qs.filter(created_at__date__lte=refund_date), "amount")
    else:
        paid_as_of = _sum(paid_qs.filter(effective_paid_at__lte=refund_dt), "amount")
        adjusted_as_of = _sum(adjustments_qs.filter(created_at__lte=refund_dt), "amount")

    available = paid_as_of - adjusted_as_of
    amount = Decimal(str(amount))
    if amount > available:
        display_at = local_refund_at.strftime("%d-%m-%Y" if is_date_only else "%d-%m-%Y %H:%M")
        return {
            "errors": {"amount": [
                f"Only ₹{_format_amount(max(available, Decimal('0')))} of the security deposit "
                f"was available as of {display_at}."
            ]},
            "message": "Security refund exceeds the security amount paid as of the refund date.",
            "dev_message": (
                f"security_paid_as_of={paid_as_of}, adjusted_as_of={adjusted_as_of}, "
                f"refund_at={local_refund_at.isoformat()}, requested={amount}, "
                f"paid_date_field={paid_date_field}"
            ),
        }

    return None
