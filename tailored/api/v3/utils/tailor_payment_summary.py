from django.db.models import Sum

from tailored.models import TailorOrderItem, TailorOrderItemAssignment, TailorPayment


def get_tailor_completed_items(tailor_id, shop_id, year=None, month=None, require_date=True):
    """
    Returns a list of normalized dicts for tailor-charge items of any order
    status, combining both sources:
    - TailorOrderItemAssignment (new multi-tailor path)
    - legacy TailorOrderItem (items with no assignment rows)

    This is the single source of truth for "what has this tailor been charged
    for" - used both by the monthly breakdown view (tailor_monthly_summary.py,
    optionally scoped to a specific year/month) and the lifetime
    remaining-payable calculation below (always called with year=None, month=None
    so it reflects the tailor's true all-time balance, unaffected by any
    year/month filter applied elsewhere). No filtering is applied on the
    order's status - items from orders of every status are included.

    require_date: when True (the monthly-breakdown use case), items without an
    expected_completion_date are excluded since they can't be grouped by month.
    When False (the remaining-payable use case), all items count toward the
    total regardless of whether a completion date was ever set.

    Each dict contains: tailor_order_id, shop_tailor_id, product_name,
    tailor_charge, expected_completion_date.
    """
    assignment_qs = TailorOrderItemAssignment.objects.select_related(
        'order_item__order'
    ).filter(
        tailor_id=tailor_id,
        order_item__order__shop_id=shop_id,
        order_item__order__deleted__isnull=True,
        order_item__deleted__isnull=True,
    )
    if require_date:
        assignment_qs = assignment_qs.filter(expected_completion_date__isnull=False)
    if year is not None:
        assignment_qs = assignment_qs.filter(expected_completion_date__year=year)
    if month is not None:
        assignment_qs = assignment_qs.filter(expected_completion_date__month=month)
    assignment_qs = assignment_qs.values(
        'order_item__order__id',
        'order_item__order__shop_tailor_id',
        'order_item__product_name',
        'tailor_charge',
        'expected_completion_date',
    )

    legacy_qs = TailorOrderItem.objects.select_related('order').filter(
        assigned_tailor_id=tailor_id,
        order__shop_id=shop_id,
        order__deleted__isnull=True,
        deleted__isnull=True,
    ).exclude(
        assignments__isnull=False  # skip items already managed by the assignment table
    )
    if require_date:
        legacy_qs = legacy_qs.filter(expected_completion_date__isnull=False)
    if year is not None:
        legacy_qs = legacy_qs.filter(expected_completion_date__year=year)
    if month is not None:
        legacy_qs = legacy_qs.filter(expected_completion_date__month=month)
    legacy_qs = legacy_qs.values(
        'order__id',
        'order__shop_tailor_id',
        'product_name',
        'tailor_charge',
        'expected_completion_date',
    )

    items = []
    for row in assignment_qs:
        items.append({
            'tailor_order_id': row['order_item__order__id'],
            'shop_tailor_id':  row['order_item__order__shop_tailor_id'],
            'product_name':    row['order_item__product_name'],
            'tailor_charge':   row['tailor_charge'],
            'expected_completion_date': row['expected_completion_date'],
        })
    for row in legacy_qs:
        items.append({
            'tailor_order_id': row['order__id'],
            'shop_tailor_id':  row['order__shop_tailor_id'],
            'product_name':    row['product_name'],
            'tailor_charge':   row['tailor_charge'],
            'expected_completion_date': row['expected_completion_date'],
        })

    return items


def get_tailor_total_charged(tailor_id, shop_id):
    """
    All-time sum of tailor_charge across both sources, for orders of any
    status belonging to this tailor/shop. Always unfiltered by year/month, and
    does not require expected_completion_date to be set (require_date=False) -
    this is the tailor's true lifetime charge total.
    """
    items = get_tailor_completed_items(tailor_id, shop_id, require_date=False)
    return sum((item['tailor_charge'] for item in items), 0)


def get_tailor_total_paid(tailor_id, shop_id, exclude_payment_id=None):
    """Sum of active (non-deleted) TailorPayment.amount for this tailor/shop."""
    qs = TailorPayment.objects.filter(
        tailor_id=tailor_id,
        shop_id=shop_id,
        deleted__isnull=True,
    )
    if exclude_payment_id:
        qs = qs.exclude(id=exclude_payment_id)

    return qs.aggregate(total=Sum('amount'))['total'] or 0


def get_tailor_remaining_payable(tailor_id, shop_id, exclude_payment_id=None):
    """
    Remaining amount the shop can still pay this tailor:
    total charged (completed orders) - total already paid.

    Pass exclude_payment_id when re-validating an existing payment during an
    update, so the payment being edited doesn't count against itself.
    """
    total_charged = get_tailor_total_charged(tailor_id, shop_id)
    total_paid = get_tailor_total_paid(tailor_id, shop_id, exclude_payment_id=exclude_payment_id)
    return total_charged - total_paid
