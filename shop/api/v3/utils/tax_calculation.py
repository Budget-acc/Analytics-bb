import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

logger = logging.getLogger("api_requests")

TWO_PLACES = Decimal("0.01")


def _to_decimal(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _q(value):
    """Quantize a Decimal to 2 decimal places using ROUND_HALF_UP."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def get_active_tax_config(shop):
    """Returns the shop's ShopTaxConfiguration if it exists and is_enabled, else None."""
    config = getattr(shop, "tax_configuration", None)
    return config if (config and config.is_enabled) else None


APPLY_TAX_ERROR = "apply_tax must be a boolean value."


def resolve_apply_tax(data):
    """
    Per-transaction tax switch (`apply_tax`) from a create request body, shared
    by the Booking, Sale and Tailor Order create APIs. Missing/null -> True
    (the shop's active tax configuration applies as usual); False -> no tax
    for that transaction only. Accepts booleans, 0/1 and their string forms
    (multipart). Returns (bool_value, error_message_or_None).
    """
    raw = data.get("apply_tax")
    if raw is None:
        return True, None
    if isinstance(raw, bool):
        return raw, None
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw), None
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True, None
        if normalized in {"false", "0", "no", "n", "off"}:
            return False, None
    return True, APPLY_TAX_ERROR


def get_transaction_tax_config(shop, apply_tax=True):
    """
    Tax configuration for one new transaction: the shop's active configuration,
    or None when tax is switched off for this transaction (apply_tax=False) -
    the caller then takes its existing no-tax path. The shop's configuration
    itself is never modified.
    """
    return get_active_tax_config(shop) if apply_tax else None


def resolve_taxable_flags(taxable_components):
    """
    Returns (product_taxable, additional_taxable, security_taxable) booleans.
    'entire_invoice' forces all three True, matching ShopTaxConfiguration's
    documented semantics (it takes priority over any other values present).
    """
    components = set(taxable_components or [])
    if "entire_invoice" in components:
        return True, True, True
    return (
        "product_total" in components,
        "additional_charges" in components,
        "security_amount" in components,
    )


def split_inclusive_amount(raw_amount, tax_rate):
    """
    base = raw / (1 + rate/100); tax = raw - base.
    Returns (base, tax) as 2dp Decimals. If rate <= 0, base = raw and tax = 0.
    """
    raw = _to_decimal(raw_amount)
    rate = _to_decimal(tax_rate)
    if rate <= 0:
        return _q(raw), Decimal("0.00")
    base = _q(raw / (Decimal("1") + rate / Decimal("100")))
    tax = _q(raw - base)
    return base, tax


def split_inclusive_amount_int(raw_amount, tax_rate):
    """
    Like split_inclusive_amount, but rounds the resulting base to the nearest
    integer - for fields backed by an IntegerField/BigIntegerField (product
    item amounts, booking security amount, sale item unit prices), which
    cannot store fractional paise.
    """
    base, _ = split_inclusive_amount(raw_amount, tax_rate)
    return int(base.to_integral_value(rounding=ROUND_HALF_UP))


def calculate_booking_tax(config, product_items, additional_items, security_amount, discount=0):
    """
    product_items / additional_items: lists of dicts with a mutable 'amount' key
    (the same dicts flowing through create code). Each item's 'amount' is
    replaced in place with its own post-discount, tax-exclusive value - so
    Product Total (sum of stored item amounts) + Additional Charges (sum of
    stored charge amounts) + tax_amount always equals total_amount exactly,
    by construction, not just in aggregate.

    Discount is applied in two proportional steps:
      1. Distributed across the two buckets (raw product total vs raw
         additional total).
      2. Within each bucket, distributed across that bucket's individual
         items/charges, proportional to each one's own raw amount.
    Each item's discounted raw amount is then tax-split (inclusive) or has
    tax added on top (exclusive) - i.e. discount first, then tax on the
    discounted amount, per item. Security is never discounted (validated/
    shown separately) and is tax-adjusted from its raw amount as before.

    Returns a dict:
        subtotal, taxable_amount, tax_amount (Decimals, 2dp),
        tax_rate (Decimal), tax_name (str), tax_calculation_type (str),
        total_amount (int) = subtotal + tax_amount, rounded,
        security_amount (Decimal or None) - adjusted base if security is a
            taxable component under inclusive tax, else None (caller keeps
            the original value unchanged),
        is_tax_applied (True).
    """
    product_taxable, additional_taxable, security_taxable = resolve_taxable_flags(
        config.taxable_components
    )
    rate = config.tax_rate
    calc_type = config.tax_calculation_type
    security_dec = _to_decimal(security_amount)
    discount_dec = _to_decimal(discount)
    adjusted_security = None

    # Raw (as-received, unmutated) bucket totals - captured before the
    # per-item mutation loop below, needed to distribute the discount.
    product_raw_total = sum((_to_decimal(i.get("amount", 0)) for i in product_items), Decimal("0"))
    additional_raw_total = sum((_to_decimal(i.get("amount", 0)) for i in additional_items), Decimal("0"))
    combined_raw = product_raw_total + additional_raw_total
    if combined_raw > 0:
        discount_on_product = _q(discount_dec * product_raw_total / combined_raw)
    else:
        discount_on_product = Decimal("0.00")
    discount_on_additional = _q(discount_dec - discount_on_product)

    tax_amount_total = Decimal("0.00")

    if calc_type == "inclusive":
        # Inclusive tax: item/charge amounts are mutated in place - each
        # one's discount share is subtracted from its own raw amount, then
        # that discounted amount is split into (tax-exclusive base, tax).
        # This is what gets persisted, so Product Total (sum of stored item
        # amounts) + Additional Charges (sum of stored charge amounts) +
        # tax_amount always equals total_amount, by construction.
        for item in product_items:
            raw = _to_decimal(item.get("amount", 0))
            item_discount = (
                _q(discount_on_product * raw / product_raw_total) if product_raw_total > 0 else Decimal("0.00")
            )
            discounted_raw = raw - item_discount
            if product_taxable:
                base, _ = split_inclusive_amount(discounted_raw, rate)
                # BookedProductVariant.amount is an IntegerField (pre-existing
                # schema constraint, unrelated to tax) - round the base to
                # the nearest integer, and derive this item's tax from the
                # rounded base so subtotal/tax stay internally consistent
                # with what's actually stored.
                base_int = int(base.to_integral_value(rounding=ROUND_HALF_UP))
                tax = _q(discounted_raw - Decimal(base_int))
            else:
                base_int = int(discounted_raw.to_integral_value(rounding=ROUND_HALF_UP))
                tax = Decimal("0.00")
            item["amount"] = base_int
            tax_amount_total += tax

        # Each additional charge is converted individually, not from a
        # combined sum — matches the confirmed requirement exactly.
        for item in additional_items:
            raw = _to_decimal(item.get("amount", 0))
            item_discount = (
                _q(discount_on_additional * raw / additional_raw_total) if additional_raw_total > 0 else Decimal("0.00")
            )
            discounted_raw = raw - item_discount
            if additional_taxable:
                base, tax = split_inclusive_amount(discounted_raw, rate)
            else:
                base, tax = _q(discounted_raw), Decimal("0.00")
            item["amount"] = base
            tax_amount_total += tax

        if security_taxable:
            base, _ = split_inclusive_amount(security_dec, rate)
            # BookingV2.security_amount is a BigIntegerField (pre-existing
            # schema constraint) - round the exclusive base to the nearest
            # integer.
            adjusted_security = Decimal(int(base.to_integral_value(rounding=ROUND_HALF_UP)))
            exclusive_security = adjusted_security
            tax_amount_total += _q(security_dec - exclusive_security)
        else:
            exclusive_security = security_dec

        # subtotal/taxable_amount are built from the actually-stored (post-
        # discount, tax-exclusive) per-item values, so Product Total +
        # Additional Charges + tax_amount == total_amount holds by
        # construction.
        product_total = sum((_to_decimal(i.get("amount", 0)) for i in product_items), Decimal("0"))
        additional_total = sum((_to_decimal(i.get("amount", 0)) for i in additional_items), Decimal("0"))
        subtotal = _q(product_total + additional_total)

        taxable_amount = Decimal("0.00")
        if product_taxable:
            taxable_amount += product_total
        if additional_taxable:
            taxable_amount += additional_total
        if security_taxable:
            taxable_amount += exclusive_security
        taxable_amount = _q(taxable_amount)

        tax_amount = _q(tax_amount_total)
        total_amount = int((subtotal + tax_amount).to_integral_value(rounding=ROUND_HALF_UP))

    else:
        # Exclusive tax: item/charge amounts are NEVER mutated - they keep
        # exactly the raw value received (tax is added on top separately,
        # discount is a separate line item, not baked into per-item storage).
        # Discount and tax are computed purely at the bucket/aggregate level
        # from the raw totals.
        discounted_product = product_raw_total - discount_on_product
        discounted_additional = additional_raw_total - discount_on_additional

        tax_amount_total = Decimal("0.00")
        if product_taxable:
            tax_amount_total += _q(discounted_product * rate / Decimal("100"))
        if additional_taxable:
            tax_amount_total += _q(discounted_additional * rate / Decimal("100"))
        if security_taxable:
            exclusive_security = security_dec
            tax_amount_total += _q(security_dec * rate / Decimal("100"))
        else:
            exclusive_security = security_dec

        # subtotal is the raw, undiscounted sum - matches what's actually
        # stored per item (unchanged) so it reflects "Product Total +
        # Additional Charges" as entered, before the discount line item.
        subtotal = _q(product_raw_total + additional_raw_total)

        taxable_amount = Decimal("0.00")
        if product_taxable:
            taxable_amount += discounted_product
        if additional_taxable:
            taxable_amount += discounted_additional
        if security_taxable:
            taxable_amount += exclusive_security
        taxable_amount = _q(taxable_amount)

        tax_amount = _q(tax_amount_total)
        total_amount = int(
            (discounted_product + discounted_additional + tax_amount).to_integral_value(rounding=ROUND_HALF_UP)
        )

    return {
        "subtotal": subtotal,
        "taxable_amount": taxable_amount,
        "tax_amount": tax_amount,
        "tax_rate": rate,
        "tax_name": config.tax_name,
        "tax_calculation_type": calc_type,
        "taxable_components": list(config.taxable_components or []),
        "total_amount": total_amount,
        "security_amount": adjusted_security,
        "is_tax_applied": True,
    }


def calculate_booking_tax_v2(config, product_items, additional_items, security_amount, discount=0):
    """
    Booking-only variant of calculate_booking_tax. Used exclusively by
    booking_v2/api/v5/views/booking_viewset/create.py - kept fully separate
    from the original function (which is left untouched, unused dead code
    kept for rollback safety) so this fix cannot affect Sale or Tailor Order,
    neither of which call calculate_booking_tax/calculate_booking_tax_v2 at
    all (each has its own independent inline tax logic).

    Difference from calculate_booking_tax (Inclusive branch only - Exclusive
    and no-tax-config behavior is identical): item/charge amounts are NEVER
    mutated - they keep exactly the raw value received in the request (same
    as the Exclusive branch). Discount and tax are computed purely at the
    combined-subtotal level: taxable products/charges/security (per the
    configured taxable_components) are summed, the discount is subtracted
    from that taxable subtotal, and GST is extracted (inclusive) from the
    discounted taxable subtotal - the extracted tax is stored on the
    Booking's tax_amount field only. `total_amount` remains the raw,
    pre-discount sum of product + additional-charge amounts.

    Returns the same dict shape as calculate_booking_tax.
    """
    product_taxable, additional_taxable, security_taxable = resolve_taxable_flags(
        config.taxable_components
    )
    rate = config.tax_rate
    calc_type = config.tax_calculation_type
    security_dec = _to_decimal(security_amount)
    discount_dec = _to_decimal(discount)
    adjusted_security = None

    # Raw (as-received, unmutated) bucket totals - captured before the
    # per-item mutation loop below, needed to distribute the discount.
    product_raw_total = sum((_to_decimal(i.get("amount", 0)) for i in product_items), Decimal("0"))
    additional_raw_total = sum((_to_decimal(i.get("amount", 0)) for i in additional_items), Decimal("0"))
    combined_raw = product_raw_total + additional_raw_total
    if combined_raw > 0:
        discount_on_product = _q(discount_dec * product_raw_total / combined_raw)
    else:
        discount_on_product = Decimal("0.00")
    discount_on_additional = _q(discount_dec - discount_on_product)

    tax_amount_total = Decimal("0.00")

    if calc_type == "inclusive":
        # Inclusive tax: item/charge amounts are NEVER mutated - they keep
        # exactly the raw value received (matches the Exclusive branch).
        # Discount and tax are computed purely at the combined-subtotal
        # level: sum the taxable buckets (products/charges/security, per
        # taxable_components), subtract the discount from that taxable
        # subtotal, then extract GST (inclusive) from the result. Only
        # tax_amount/taxable_amount (Booking-level) reflect this - the
        # individual item/charge/security amounts are untouched.
        taxable_subtotal = Decimal("0.00")
        if product_taxable:
            taxable_subtotal += product_raw_total
        if additional_taxable:
            taxable_subtotal += additional_raw_total
        if security_taxable:
            taxable_subtotal += security_dec

        # Clamp at zero: discount_amount is only validated against the raw
        # product+charges total upstream (_validate_amounts), not against
        # this taxable-scoped subtotal - when taxable_components excludes a
        # bucket with a large raw total, an otherwise-valid discount can
        # exceed taxable_subtotal. Without this floor, split_inclusive_amount
        # would return a negative base/tax that gets persisted as-is.
        discounted_taxable = max(Decimal("0.00"), taxable_subtotal - discount_dec)
        taxable_amount, tax_amount_total = split_inclusive_amount(discounted_taxable, rate)

        # subtotal is the raw, undiscounted sum of products + additional
        # charges - matches what's actually stored per item (unchanged).
        subtotal = _q(product_raw_total + additional_raw_total)

        tax_amount = _q(tax_amount_total)
        # total_amount is the raw, PRE-discount payable figure - tax is
        # already embedded in the raw amounts (inclusive), so nothing is
        # added on top. discount_amount is kept entirely separate and must
        # never reduce this stored value (mirrors Sale's total_amount).
        total_amount = int((product_raw_total + additional_raw_total).to_integral_value(rounding=ROUND_HALF_UP))

    else:
        # Exclusive tax: identical to calculate_booking_tax - item/charge
        # amounts are NEVER mutated, discount/tax computed purely at the
        # bucket/aggregate level. Not part of the bug being fixed here.
        discounted_product = product_raw_total - discount_on_product
        discounted_additional = additional_raw_total - discount_on_additional

        tax_amount_total = Decimal("0.00")
        if product_taxable:
            tax_amount_total += _q(discounted_product * rate / Decimal("100"))
        if additional_taxable:
            tax_amount_total += _q(discounted_additional * rate / Decimal("100"))
        if security_taxable:
            exclusive_security = security_dec
            tax_amount_total += _q(security_dec * rate / Decimal("100"))
        else:
            exclusive_security = security_dec

        subtotal = _q(product_raw_total + additional_raw_total)

        taxable_amount = Decimal("0.00")
        if product_taxable:
            taxable_amount += discounted_product
        if additional_taxable:
            taxable_amount += discounted_additional
        if security_taxable:
            taxable_amount += exclusive_security
        taxable_amount = _q(taxable_amount)

        tax_amount = _q(tax_amount_total)
        # total_amount is the raw, PRE-discount payable figure plus tax
        # (tax itself is still correctly computed on the discounted base,
        # via discounted_product/discounted_additional above - only the
        # base it's added to switches from discounted to raw). discount_
        # amount is kept entirely separate and must never reduce this
        # stored value (mirrors Sale's total_amount).
        total_amount = int(
            (product_raw_total + additional_raw_total + tax_amount).to_integral_value(rounding=ROUND_HALF_UP)
        )

    return {
        "subtotal": subtotal,
        "taxable_amount": taxable_amount,
        "tax_amount": tax_amount,
        "tax_rate": rate,
        "tax_name": config.tax_name,
        "tax_calculation_type": calc_type,
        "taxable_components": list(config.taxable_components or []),
        "total_amount": total_amount,
        "security_amount": adjusted_security,
        "is_tax_applied": True,
    }
