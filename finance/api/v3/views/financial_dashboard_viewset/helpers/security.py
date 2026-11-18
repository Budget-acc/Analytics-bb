from collections import defaultdict

from django.db.models import Prefetch
from django.utils.timezone import localtime, localdate
from finance.models import Account


def _security_dates(shop_id, client):
    from booking_v2.models import BookingV2
    from tailored.models import TailorOrder

    today = localdate()

    booking_qs = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            security_amount__gt=0,
            deleted__isnull=True,
            return_date__isnull=False,
            return_date__date__lte=today,
        )
        .exclude(delivery_status="cancelled")
    )
    if client:
        booking_qs = booking_qs.filter(client_identity=client)

    tailor_qs = TailorOrder.objects.filter(
        shop_id=shop_id,
        security_amount__gt=0,
        customer_order_type="rental",
        created_booking__isnull=True,
        deleted__isnull=True,
    )
    if client:
        tailor_qs = tailor_qs.filter(client=client)

    return (
        set(booking_qs.values_list("return_date__date", flat=True).distinct())
        | set(tailor_qs.values_list("created_at__date", flat=True).distinct())
    )


def _fetch_security(shop_id, client, page_dates):
    from booking_v2.models import BookingV2, SecurityDepositPayment, SecurityDepositAdjustment
    from tailored.models import TailorOrder

    today = localdate()
    cash_account = (
        Account.objects.filter(
            shop_id=shop_id,
            account_type="cash",
            is_active=True,
            deleted__isnull=True,
        )
        .order_by("-is_default", "id")
        .first()
    )

    booking_qs = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            security_amount__gt=0,
            deleted__isnull=True,
            return_date__date__in=page_dates,
            return_date__date__lte=today,
        )
        .exclude(delivery_status="cancelled")
        .select_related("client_identity")
        .prefetch_related(
            Prefetch(
                "security_deposit_payments",
                queryset=SecurityDepositPayment.objects.select_related("account").order_by("created_at"),
                to_attr="_sec_payments",
            ),
            Prefetch(
                "security_adjustments",
                queryset=SecurityDepositAdjustment.objects.all(),
                to_attr="_sec_adjustments",
            ),
        )
        .order_by("-return_date")
    )
    if client:
        booking_qs = booking_qs.filter(client_identity=client)

    tailor_qs = (
        TailorOrder.objects.filter(
            shop_id=shop_id,
            security_amount__gt=0,
            customer_order_type="rental",
            created_booking__isnull=True,
            deleted__isnull=True,
            created_at__date__in=page_dates,
        )
        .select_related("client").prefetch_related("security_payments__account")
        .order_by("-created_at")
    )
    if client:
        tailor_qs = tailor_qs.filter(client=client)

    by_date = defaultdict(list)
    acc_by_date = defaultdict(lambda: defaultdict(float))

    for booking in booking_qs:
        adjustments = getattr(booking, "_sec_adjustments", [])
        sec_payments = getattr(booking, "_sec_payments", [])
        security_amount = int(booking.security_amount or 0)

        refunded = sum(float(a.amount) for a in adjustments if a.adjustment_type == "refund")
        deducted = sum(float(a.amount) for a in adjustments if a.adjustment_type == "deduction")

        # Backward-compat: completed + security > 0 + no adjustments → fully refunded
        if booking.booking_status == "completed" and security_amount > 0 and not adjustments:
            refunded = float(security_amount)
            deducted = 0.0

        pending = security_amount - refunded - deducted

        account_name = None
        if sec_payments:
            first = sec_payments[0]
            account_name = first.account.name if first.account else None
        elif security_amount > 0 and cash_account:
            # Legacy/fallback behavior: default to cash when no explicit method/payment exists.
            account_name = cash_account.name

        date_obj = localtime(booking.return_date).date()
        if account_name:
            acc_by_date[date_obj][account_name] += security_amount

        by_date[date_obj].append({
            "type": "booking",
            "reference_id": booking.id,
            "invoice_id": booking.shop_booking_id,
            "client_name": (
                booking.client_identity.client_name
                if booking.client_identity else None
            ),
            "security_amount": security_amount,
            "pending_amount": pending,
            "refunded_amount": refunded,
            "deducted_amount": deducted,
            "account_name": account_name,
        })

    for order in tailor_qs:
        date_obj = localtime(order.created_at).date()
        security_amount = float(order.security_amount or 0)
        _sp = next(iter(order.security_payments.all()), None)
        account_name = _sp.account.name if _sp and _sp.account else None
        if not _sp and security_amount > 0 and cash_account:
            # Match booking fallback for missing payment method in security entries.
            account_name = cash_account.name

        if account_name:
            acc_by_date[date_obj][account_name] += security_amount

        by_date[date_obj].append({
            "type": "tailor_order",
            "reference_id": order.id,
            "invoice_id": order.shop_tailor_id,
            "client_name": order.client.client_name if order.client else None,
            "security_amount": security_amount,
            "pending_amount": security_amount,
            "refunded_amount": 0,
            "deducted_amount": 0,
            "account_name": account_name,
        })

    result = {}
    for date_obj, items in by_date.items():
        result[date_obj] = {
            "total_security": sum(i["security_amount"] for i in items),
            "total_pending": sum(i["pending_amount"] for i in items),
            "total_deducted": sum(i["deducted_amount"] for i in items),
            "total_refunded": sum(i["refunded_amount"] for i in items),
            "accounts": [
                {"name": name, "amount": str(int(amount))}
                for name, amount in acc_by_date.get(date_obj, {}).items()
            ],
            "items": items,
        }
    return result
