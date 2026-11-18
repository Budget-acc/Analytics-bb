"""Move product variant stock / fabric out of service and back.

`ProductVariant.stock` and `fabric_length` remain the usable amounts that every
existing booking, availability and tailor-order check reads. This module only
moves amounts between them and `unavailable_stock` / `unavailable_fabric_length`
and records each movement in ProductVariantStockAdjustment. It never reads or
changes bookings.
"""
import logging
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from services.constants.stock_adjustment import (
    MAX_FABRIC_LENGTH_VALUE,
    MAX_STOCK_VALUE,
    StockAdjustmentMessages as M,
)
from services.models import ProductVariant, ProductVariantStockAdjustment

logger = logging.getLogger("api_requests")

Action = ProductVariantStockAdjustment.Action

AMOUNT_FIELDS = ("stock", "unavailable_stock", "fabric_length", "unavailable_fabric_length")


class StockAdjustmentError(Exception):
    """A business-rule failure; nothing has been written."""

    def __init__(self, message, dev_message, errors):
        super().__init__(dev_message)
        self.message = message
        self.dev_message = dev_message
        self.errors = errors


def _item_error(field, msg, variant, product, requested=None, available=None):
    """Return (field, message, dev_message) for one failed item."""
    fmt = {
        "name": product.name,
        "attribute": variant.attribute or "default",
        "variant_id": variant.id,
        "requested": requested,
        "available": available,
    }
    return field, msg.message.format(**fmt), msg.dev_message.format(**fmt)


def _apply_to_variant(variant, product, action, quantity, fabric_length):
    """Return (new values dict, error tuple or None) for one item; does not write."""
    stock, unavailable_stock = variant.stock, variant.unavailable_stock
    fabric, unavailable_fabric = variant.fabric_length, variant.unavailable_fabric_length

    if fabric_length is not None and fabric is None:
        return None, _item_error("fabric_length", M.FABRIC_NOT_TRACKED, variant, product)

    marking = action == Action.MARK_UNAVAILABLE
    if quantity is not None:
        source = stock if marking else unavailable_stock
        if quantity > source:
            msg = M.STOCK_NOT_AVAILABLE if marking else M.STOCK_NOT_UNAVAILABLE
            return None, _item_error("quantity", msg, variant, product, quantity, source)
        delta = quantity if marking else -quantity
        stock, unavailable_stock = stock - delta, unavailable_stock + delta

    if fabric_length is not None:
        source = fabric if marking else unavailable_fabric
        if fabric_length > source:
            msg = M.FABRIC_NOT_AVAILABLE if marking else M.FABRIC_NOT_UNAVAILABLE
            return None, _item_error("fabric_length", msg, variant, product, fabric_length, source)
        delta = fabric_length if marking else -fabric_length
        fabric, unavailable_fabric = fabric - delta, unavailable_fabric + delta

    if max(stock, unavailable_stock) > MAX_STOCK_VALUE or max(fabric or 0, unavailable_fabric) > MAX_FABRIC_LENGTH_VALUE:
        return None, _item_error("non_field_errors", M.AMOUNT_TOO_LARGE, variant, product)

    # The checks above keep every value >= 0; the column types and check
    # constraints reject a negative value at the DB if that ever changes.
    return {
        "stock": stock,
        "unavailable_stock": unavailable_stock,
        "fabric_length": fabric,
        "unavailable_fabric_length": unavailable_fabric,
    }, None


@transaction.atomic
def apply_stock_adjustments(*, product, action, items, user):
    """Apply every item or none.

    `product` must already be verified as belonging to the caller's shop.
    `items` come from StockAdjustmentRequestSerializer: unique variant_ids,
    with effective `reason` / `note` resolved.

    Returns (batch_id, results) with one result per item, in request order.
    Raises StockAdjustmentError on a business-rule failure. Database errors
    (lock timeout, constraint violation) propagate to the caller.
    """
    variant_ids = [item["variant_id"] for item in items]

    # One query, locked in id order so overlapping batches can't deadlock.
    # Product ownership was checked by the caller, so filtering by product
    # also scopes the variants to the shop. Validation below runs on these
    # locked values, so a concurrent request can't slip in between.
    variants = {
        variant.id: variant
        for variant in ProductVariant.objects.select_for_update()
        .filter(id__in=variant_ids, product_id=product.id, deleted__isnull=True)
        .only("id", "attribute", *AMOUNT_FIELDS)
        .order_by("id")
    }

    missing = [variant_id for variant_id in variant_ids if variant_id not in variants]
    if missing:
        raise StockAdjustmentError(
            M.INVALID_VARIANTS.message, M.INVALID_VARIANTS.dev_message.format(ids=", ".join(map(str, missing))),
            {"invalid_variant_ids": missing},
        )

    # Validate every item before writing anything, and report all failures at once.
    errors, messages, dev_messages, planned = {}, [], [], []
    for index, item in enumerate(items):
        variant = variants[item["variant_id"]]
        new_values, error = _apply_to_variant(
            variant, product, action, item.get("quantity"), item.get("fabric_length"),
        )
        if error:
            field, message, dev_message = error
            errors[str(index)] = {field: [message]}
            messages.append(message)
            dev_messages.append(dev_message)
            continue
        planned.append((item, variant, new_values))

    if errors:
        raise StockAdjustmentError(messages[0], " ".join(dev_messages), {"variants": errors})

    batch_id = uuid.uuid4()
    now = timezone.now()
    history, results = [], []
    for item, variant, new_values in planned:
        history.append(ProductVariantStockAdjustment(
            batch_id=batch_id,
            shop_id=product.shop_service.shop_id,
            product_id=product.id,
            variant_id=variant.id,
            action=action,
            reason=item["reason"],
            note=item["note"],
            quantity=item.get("quantity") or 0,
            fabric_length=item.get("fabric_length") or Decimal("0.00"),
            previous_stock=variant.stock,
            updated_stock=new_values["stock"],
            previous_unavailable_stock=variant.unavailable_stock,
            updated_unavailable_stock=new_values["unavailable_stock"],
            previous_fabric_length=variant.fabric_length,
            updated_fabric_length=new_values["fabric_length"],
            previous_unavailable_fabric_length=variant.unavailable_fabric_length,
            updated_unavailable_fabric_length=new_values["unavailable_fabric_length"],
            created_by=user if getattr(user, "is_authenticated", False) else None,
        ))
        for field, value in new_values.items():
            setattr(variant, field, value)
        # Same effect ProductVariant.save() has for a quantity-only update:
        # bump quantity_updated_at, leave structure_updated_at alone.
        variant.quantity_updated_at = now
        results.append(_result(item, variant))

    # bulk_update deliberately bypasses ProductVariant.save(), which is
    # left untouched. Usable and unavailable amounts are written together
    # on the rows locked above.
    ProductVariant.objects.bulk_update(
        [variant for _, variant, _ in planned], [*AMOUNT_FIELDS, "quantity_updated_at"],
    )
    ProductVariantStockAdjustment.objects.bulk_create(history)

    logger.info(
        "Stock adjustment %s applied: product=%s action=%s variants=%s",
        batch_id, product.id, action, variant_ids,
    )
    return batch_id, results


def _result(item, variant):
    fabric = variant.fabric_length
    return {
        "variant_id": variant.id,
        "attribute": variant.attribute,
        "reason": item["reason"],
        "note": item["note"],
        "quantity": item.get("quantity") or 0,
        "fabric_length": item.get("fabric_length") or Decimal("0.00"),
        "stock": variant.stock,
        "unavailable_stock": variant.unavailable_stock,
        "total_stock": variant.stock + variant.unavailable_stock,
        "available_fabric_length": fabric,
        "unavailable_fabric_length": variant.unavailable_fabric_length,
        "total_fabric_length": None if fabric is None else fabric + variant.unavailable_fabric_length,
    }
