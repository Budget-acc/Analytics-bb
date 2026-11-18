"""
Batch enrichment for LedgerEntry records.

Resolves display labels, main entity IDs (booking/sale/order), invoice IDs,
client names, and product item strings by fetching related objects in bulk —
one query per reference_type that actually appears in the given entries.
"""
from collections import defaultdict


def _build_prefixed_expense_description(prefix, expense_type):
    """Return '<prefix> - <type>' when type exists, else '<prefix>'."""
    normalized_type = (expense_type or "").strip()
    return f"{prefix} - {normalized_type}" if normalized_type else prefix


def batch_enrich(ledger_entries):
    """
    Given a list of LedgerEntry objects, return a dict keyed by
    ledger_entry.id containing enrichment data for each entry:

        {
            "main_id":    <int>        – ID of the primary entity (booking, sale, order, expense)
            "description": <str>       – User-facing label (never raw backend keywords)
            "invoice_id":  <str|None>
            "client_name": <str|None>
            "notes":       <str|None>
            "items":       <str>       – Comma-separated product names, "" when not applicable
        }

    Entries whose reference_type is not handled (e.g. transfer_in/out) are
    omitted from the result; the caller falls back to the raw transaction_type.
    """
    # Group reference_ids by reference_type
    refs_by_type = defaultdict(set)
    # reference_type -> reference_id -> [ledger_entry_id, ...]
    entry_ids_by_ref = defaultdict(lambda: defaultdict(list))

    for entry in ledger_entries:
        if entry.reference_id:
            refs_by_type[entry.reference_type].add(entry.reference_id)
            entry_ids_by_ref[entry.reference_type][entry.reference_id].append(entry.id)

    result = {}

    def _apply(ref_type, ref_id, enrich):
        for eid in entry_ids_by_ref[ref_type].get(ref_id, []):
            result[eid] = enrich

    # ------------------------------------------------------------------
    # Booking payments
    # ------------------------------------------------------------------
    ids = refs_by_type.get("PaymentDetailsV2", set())
    if ids:
        from booking_v2.models import PaymentDetailsV2, BookedProductVariant

        # Build base enrichment per booking_id
        booking_enrich = {}  # booking_id -> partial enrich dict
        for pd in PaymentDetailsV2.objects.filter(
            id__in=ids
        ).select_related("booking__client_identity"):
            booking_enrich[pd.id] = {
                "main_id": pd.booking_id,
                "description": "Booking Payment",
                "invoice_id": pd.booking.shop_booking_id if pd.booking else None,
                "client_name": (
                    pd.booking.client_identity.client_name
                    if pd.booking and pd.booking.client_identity else None
                ),
                "notes": None,
                "_booking_id": pd.booking_id,
            }

        # Batch fetch product names for all involved bookings
        booking_ids = [v["_booking_id"] for v in booking_enrich.values() if v["_booking_id"]]
        items_by_booking = _items_from_booked_variants(booking_ids)

        for pd_id, enrich in booking_enrich.items():
            enrich["items"] = items_by_booking.get(enrich["_booking_id"], "")
            del enrich["_booking_id"]
            _apply("PaymentDetailsV2", pd_id, enrich)

    # ------------------------------------------------------------------
    # Sale payments
    # ------------------------------------------------------------------
    ids = refs_by_type.get("SalesPaymentDetails", set())
    if ids:
        from sales.models import SalesPaymentDetails, SaleItem

        sale_enrich = {}  # sp.id -> partial enrich dict
        for sp in SalesPaymentDetails.objects.filter(
            id__in=ids
        ).select_related("sale__client"):
            sale_enrich[sp.id] = {
                "main_id": sp.sale_id,
                "description": "Sale Payment",
                "invoice_id": sp.sale.shop_sale_id if sp.sale else None,
                "client_name": (
                    sp.sale.client.client_name
                    if sp.sale and sp.sale.client else None
                ),
                "notes": None,
                "_sale_id": sp.sale_id,
            }

        sale_ids = [v["_sale_id"] for v in sale_enrich.values() if v["_sale_id"]]
        items_by_sale = _items_from_sale_items(sale_ids)

        for sp_id, enrich in sale_enrich.items():
            enrich["items"] = items_by_sale.get(enrich["_sale_id"], "")
            del enrich["_sale_id"]
            _apply("SalesPaymentDetails", sp_id, enrich)

    # ------------------------------------------------------------------
    # Customization work payments (tailor orders → "Customization Work")
    # ------------------------------------------------------------------
    ids = refs_by_type.get("CustomerPayment", set())
    if ids:
        from tailored.models import CustomerPayment, TailorOrderItem

        order_enrich = {}  # cp.id -> partial enrich dict
        for cp in CustomerPayment.objects.filter(
            id__in=ids
        ).select_related("order__client"):
            order_enrich[cp.id] = {
                "main_id": cp.order_id,
                "description": "Custom Work Payment",
                "invoice_id": cp.order.shop_tailor_id if cp.order else None,
                "client_name": (
                    cp.order.client.client_name
                    if cp.order and cp.order.client else None
                ),
                "notes": None,
                "_order_id": cp.order_id,
            }

        order_ids = [v["_order_id"] for v in order_enrich.values() if v["_order_id"]]
        items_by_order = _items_from_tailor_order_items(order_ids)

        for cp_id, enrich in order_enrich.items():
            enrich["items"] = items_by_order.get(enrich["_order_id"], "")
            del enrich["_order_id"]
            _apply("CustomerPayment", cp_id, enrich)

    # ------------------------------------------------------------------
    # Expenses — items not applicable
    # ------------------------------------------------------------------
    ids = refs_by_type.get("Expenses", set())
    if ids:
        from expenses_v2.models import Expenses
        for exp in Expenses.objects.filter(id__in=ids):
            _apply("Expenses", exp.id, {
                "main_id": exp.id,
                "description": _build_prefixed_expense_description("Expense", exp.type),
                "invoice_id": None,
                "client_name": None,
                "notes": exp.description,
                "items": "",
            })

    # ------------------------------------------------------------------
    # Product variant expenses — items not applicable
    # ------------------------------------------------------------------
    ids = refs_by_type.get("ProductVariantExpense", set())
    if ids:
        from expenses_v2.models import ProductVariantExpense
        for pv_exp in ProductVariantExpense.objects.filter(
            id__in=ids
        ).select_related("product_variant__product"):
            product = pv_exp.product_variant.product if pv_exp.product_variant_id else None
            product_name = (product.name if product else None) or "Product Expense"
            _apply("ProductVariantExpense", pv_exp.id, {
                "main_id": pv_exp.id,
                "description": _build_prefixed_expense_description("Expense", pv_exp.type),
                "invoice_id": None,
                "client_name": None,
                "notes": pv_exp.description,
                "items": product_name,
                "service_id": product.shop_service_id if product else None,
                "product_id": product.id if product else None,
                "variant_id": pv_exp.product_variant_id,
            })

    # ------------------------------------------------------------------
    # Booking security deposit received — items not applicable
    # ------------------------------------------------------------------
    ids = refs_by_type.get("SecurityDepositPayment", set())
    if ids:
        from booking_v2.models import SecurityDepositPayment
        for sp in SecurityDepositPayment.objects.filter(
            id__in=ids
        ).select_related("booking__client_identity"):
            _apply("SecurityDepositPayment", sp.id, {
                "main_id": sp.booking_id,
                "description": "Booking Security Payment",
                "invoice_id": sp.booking.shop_booking_id if sp.booking else None,
                "client_name": (
                    sp.booking.client_identity.client_name
                    if sp.booking and sp.booking.client_identity else None
                ),
                "notes": None,
                "items": "",
            })

    # ------------------------------------------------------------------
    # Tailor order security deposit received — items not applicable
    # ------------------------------------------------------------------
    ids = refs_by_type.get("TailorOrderSecurity", set())
    if ids:
        from tailored.models import TailorOrder
        for order in TailorOrder.objects.filter(
            id__in=ids
        ).select_related("client"):
            _apply("TailorOrderSecurity", order.id, {
                "main_id": order.id,
                "description": "Custom Work Security Payment",
                "invoice_id": order.shop_tailor_id,
                "client_name": (
                    order.client.client_name
                    if order.client else None
                ),
                "notes": None,
                "items": "",
            })

    # ------------------------------------------------------------------
    # Booking security adjustments (refund or deduction) — items not applicable
    # ------------------------------------------------------------------
    ids = refs_by_type.get("SecurityDepositAdjustment", set())
    if ids:
        from booking_v2.models import SecurityDepositAdjustment
        for sa in SecurityDepositAdjustment.objects.filter(
            id__in=ids
        ).select_related("booking__client_identity"):
            description = (
                "Booking - Security Refund"
                if sa.adjustment_type == "refund"
                else "Booking Security Deduction"
            )
            _apply("SecurityDepositAdjustment", sa.id, {
                "main_id": sa.booking_id,
                "description": description,
                "invoice_id": sa.booking.shop_booking_id if sa.booking else None,
                "client_name": (
                    sa.booking.client_identity.client_name
                    if sa.booking and sa.booking.client_identity else None
                ),
                "notes": None,
                "items": "",
            })

    # ------------------------------------------------------------------
    # Booking-level security refunds (reference_type=BookingV2)
    # ------------------------------------------------------------------
    ids = refs_by_type.get("BookingV2", set())
    if ids:
        from booking_v2.models import BookingV2
        for booking in BookingV2.objects.filter(
            id__in=ids
        ).select_related("client_identity"):
            _apply("BookingV2", booking.id, {
                "main_id": booking.id,
                "description": "Booking - Security Refund",
                "invoice_id": booking.shop_booking_id,
                "client_name": (
                    booking.client_identity.client_name
                    if booking.client_identity else None
                ),
                "notes": None,
                "items": "",
            })

    # ------------------------------------------------------------------
    # Booking refunds — items not applicable
    # ------------------------------------------------------------------
    ids = refs_by_type.get("RefundDetails", set())
    if ids:
        from booking_v2.models import RefundDetails
        for rd in RefundDetails.objects.filter(
            id__in=ids
        ).select_related("booking__client_identity"):
            _apply("RefundDetails", rd.id, {
                "main_id": rd.booking_id,
                "description": "Booking - Cancel Refund",
                "invoice_id": rd.booking.shop_booking_id if rd.booking else None,
                "client_name": (
                    rd.booking.client_identity.client_name
                    if rd.booking and rd.booking.client_identity else None
                ),
                "notes": None,
                "items": "",
            })

    # ------------------------------------------------------------------
    # Account transfers — direction-aware description
    # ------------------------------------------------------------------
    ids = refs_by_type.get("AccountTransfer", set())
    if ids:
        from finance.models import AccountTransfer

        # Build a map: entry_id -> transaction_type for direction lookup
        entry_type_map = {}
        for entry in ledger_entries:
            if entry.reference_type == "AccountTransfer" and entry.reference_id in ids:
                entry_type_map[entry.id] = entry.transaction_type

        transfers = {
            t.id: t
            for t in AccountTransfer.objects.filter(
                id__in=ids
            ).select_related('from_account', 'to_account')
        }

        for entry in ledger_entries:
            if entry.reference_type != "AccountTransfer":
                continue
            transfer = transfers.get(entry.reference_id)
            if not transfer:
                continue
            if entry.transaction_type == 'transfer_out':
                description = f"Transfer to {transfer.to_account.name}"
            else:
                description = f"Transfer from {transfer.from_account.name}"
            result[entry.id] = {
                "main_id": transfer.id,
                "description": description,
                "invoice_id": None,
                "client_name": None,
                "notes": transfer.notes,
                "items": "",
            }

    return result


# ---------------------------------------------------------------------------
# Item-name helpers — one query each, return {entity_id: "Name A, Name B"}
# ---------------------------------------------------------------------------

def _items_from_booked_variants(booking_ids):
    """Return {booking_id: "Product A, Product B"} for the given booking IDs."""
    if not booking_ids:
        return {}
    from booking_v2.models import BookedProductVariant
    rows = (
        BookedProductVariant.objects
        .filter(booking_id__in=booking_ids)
        .values("booking_id", "product_name")
    )
    grouped = defaultdict(list)
    for row in rows:
        if row["product_name"]:
            grouped[row["booking_id"]].append(row["product_name"])
    return {bid: ", ".join(names) for bid, names in grouped.items()}


def _items_from_sale_items(sale_ids):
    """Return {sale_id: "Product A, Product B"} for the given sale IDs."""
    if not sale_ids:
        return {}
    from sales.models import SaleItem
    rows = (
        SaleItem.objects
        .filter(sale_id__in=sale_ids)
        .select_related("product_variant__product")
        .values("sale_id", "product_variant__product__name")
    )
    grouped = defaultdict(list)
    for row in rows:
        name = row["product_variant__product__name"]
        if name:
            grouped[row["sale_id"]].append(name)
    return {sid: ", ".join(names) for sid, names in grouped.items()}


def _items_from_tailor_order_items(order_ids):
    """Return {order_id: "Product A, Product B"} for the given tailor order IDs."""
    if not order_ids:
        return {}
    from tailored.models import TailorOrderItem
    rows = (
        TailorOrderItem.objects
        .filter(order_id__in=order_ids)
        .values("order_id", "product_name")
    )
    grouped = defaultdict(list)
    for row in rows:
        if row["product_name"]:
            grouped[row["order_id"]].append(row["product_name"])
    return {oid: ", ".join(names) for oid, names in grouped.items()}
