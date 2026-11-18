import logging
import uuid
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce, Greatest, Lower

from services.models import Product, ProductVariant
from purchase.models import PurchaseItem, StockMovement

logger = logging.getLogger("api_requests")

MAX_PRODUCTS_PER_PURCHASE = 20


def _resolve_existing_product_item(self, purchase, shop, item_data):
    """
    Resolve one "existing product" line item. Two forms:
    - Multi-variant: variants=[{variant_id, quantity}, ...] - one product,
      several variant+quantity lines sharing a single unit_purchase_price/
      increment_stock/update_product_cost.
    - Flat single-variant (unchanged): quantity + optional variant_id
      (required only when the product has more than one active variant).
    Expects: product_id, unit_purchase_price, increment_stock (bool),
    update_product_cost (bool), and either variants[] or quantity/variant_id.
    Returns (list[purchase_item], error_response).
    """
    product_id = item_data.get("product_id")
    unit_purchase_price = item_data.get("unit_purchase_price")
    increment_stock = bool(item_data.get("increment_stock", False))
    # Explicit value wins; otherwise defaults to increment_stock's resolved
    # value (not a hardcoded False) - matches spec: an unspecified
    # update_product_cost mirrors whatever increment_stock resolved to.
    if "update_product_cost" in item_data:
        update_product_cost = bool(item_data.get("update_product_cost"))
    else:
        update_product_cost = increment_stock

    if not product_id:
        return None, self.validation_error_response(
            errors={"product_id": ["product_id is required for existing products."]},
            message="Product is required.",
        )
    try:
        unit_purchase_price = Decimal(str(unit_purchase_price))
        if unit_purchase_price < 0:
            raise ValueError
    except Exception:
        return None, self.validation_error_response(
            errors={"unit_purchase_price": ["unit_purchase_price must be a non-negative number."]},
            message="Invalid purchase price.",
        )

    product = Product.objects.filter(
        id=product_id, shop_service__shop_id=shop.id, deleted__isnull=True
    ).select_related("shop_service").first()
    if not product:
        return None, self.validation_error_response(
            errors={"product_id": ["Product not found for this shop."]},
            message="Product not found.",
        )

    variants_data = item_data.get("variants")
    variant_specs = []  # list of (variant, quantity)

    if variants_data:
        if not isinstance(variants_data, list):
            return None, self.validation_error_response(
                errors={"variants": ["variants must be a list."]},
                message="Invalid variants payload.",
            )
        for entry in variants_data:
            entry_variant_id = entry.get("variant_id")
            if not entry_variant_id:
                return None, self.validation_error_response(
                    errors={"variants": ["variant_id is required for each variant entry."]},
                    message="variant_id is required for each variant entry.",
                )
            try:
                entry_quantity = int(entry.get("quantity"))
                if entry_quantity <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return None, self.validation_error_response(
                    errors={"variants": ["Each variant requires a positive quantity."]},
                    message="Invalid variant quantity.",
                )
            entry_variant = ProductVariant.objects.filter(
                id=entry_variant_id, product_id=product.id, deleted__isnull=True
            ).first()
            if not entry_variant:
                return None, self.validation_error_response(
                    errors={"variants": [f"Variant {entry_variant_id} not found for this product."]},
                    message=f"Variant {entry_variant_id} not found for this product.",
                )
            variant_specs.append((entry_variant, entry_quantity))
    else:
        try:
            quantity = int(item_data.get("quantity"))
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, self.validation_error_response(
                errors={"quantity": ["quantity must be a positive integer."]},
                message="Invalid quantity.",
            )
        variant_id = item_data.get("variant_id")
        if variant_id:
            variant = ProductVariant.objects.filter(
                id=variant_id, product_id=product.id, deleted__isnull=True
            ).first()
            if not variant:
                return None, self.validation_error_response(
                    errors={"variant_id": ["Variant not found for this product."]},
                    message="Variant not found.",
                )
        else:
            variants = list(
                ProductVariant.objects.filter(product_id=product.id, deleted__isnull=True)[:2]
            )
            if len(variants) == 0:
                return None, self.validation_error_response(
                    errors={"product_id": ["Product has no variants to purchase against."]},
                    message="Product has no variants.",
                )
            if len(variants) > 1:
                return None, self.validation_error_response(
                    errors={"variant_id": [
                        "Product has multiple variants - provide variant_id, or use "
                        "variants[] to purchase multiple variants of this product in one entry."
                    ]},
                    message="variant_id is required for this product.",
                )
            variant = variants[0]
        variant_specs.append((variant, quantity))

    line_group = uuid.uuid4()
    purchase_items = []
    for variant, quantity in variant_specs:
        # Lock the variant row before reading/mutating stock (race-condition safe).
        variant = ProductVariant.objects.select_for_update().get(id=variant.id)
        stock_before = variant.stock

        if increment_stock:
            # F()-expression update avoids a read-then-write race between
            # concurrent purchases against the same variant.
            ProductVariant.objects.filter(id=variant.id).update(stock=F("stock") + quantity)
            variant.refresh_from_db(fields=["stock"])
            StockMovement.objects.create(
                shop=shop,
                product_variant=variant,
                purchase=purchase,
                movement_type="purchase",
                previous_stock=stock_before,
                quantity_changed=quantity,
                new_stock=variant.stock,
                reference_number=purchase.purchase_code or None,
            )

        purchase_items.append(PurchaseItem.objects.create(
            purchase=purchase,
            product_variant=variant,
            product_name=product.name,
            variant_other=variant.attribute or "",
            quantity=quantity,
            purchase_amount=unit_purchase_price,
            stock_increased=increment_stock,
            update_product_cost=update_product_cost,
            line_group=line_group,
            item_source="existing",
        ))

    if update_product_cost:
        # Applied once for the whole entry, not once per variant.
        Product.objects.filter(id=product.id).update(purchase_price=unit_purchase_price)

    return purchase_items, None


def _create_new_product_item(self, purchase, shop, item_data):
    """
    Resolve one "new product" line item - creates the Product (and one or
    more ProductVariant rows) inline, then a matching PurchaseItem.
    Expects: product_name, shop_service_id, unit_purchase_price, quantity,
    category, color, model, optional variants=[{variant_name, quantity}].
    Returns (list[purchase_item], error_response).
    """
    product_name = (item_data.get("product_name") or "").strip()
    shop_service_id = item_data.get("shop_service_id")
    unit_purchase_price = item_data.get("unit_purchase_price")
    quantity = item_data.get("quantity")
    variants_data = item_data.get("variants") or []

    if not product_name:
        return None, self.validation_error_response(
            errors={"product_name": ["product_name is required for new products."]},
            message="Product name is required.",
        )
    if not shop_service_id:
        return None, self.validation_error_response(
            errors={"shop_service_id": ["shop_service_id is required to create a new product."]},
            message="Shop service is required for new products.",
        )
    try:
        unit_purchase_price = Decimal(str(unit_purchase_price))
        if unit_purchase_price < 0:
            raise ValueError
    except Exception:
        return None, self.validation_error_response(
            errors={"unit_purchase_price": ["unit_purchase_price must be a non-negative number."]},
            message="Invalid purchase price.",
        )

    from services.models import ShopOwnerService
    shop_service = ShopOwnerService.objects.filter(
        id=shop_service_id, shop_id=shop.id, deleted__isnull=True
    ).first()
    if not shop_service:
        return None, self.validation_error_response(
            errors={"shop_service_id": ["Shop service not found for this shop."]},
            message="Shop service not found.",
        )

    if not variants_data:
        try:
            quantity = int(quantity)
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, self.validation_error_response(
                errors={"quantity": ["quantity must be a positive integer."]},
                message="Invalid quantity.",
            )
        variants_data = [{"variant_name": "", "quantity": quantity}]

    try:
        # Nested atomic() so a DB-level failure here (e.g. a constraint
        # violation) is recoverable without poisoning the outer
        # transaction - a bare try/except around a raw DB error would
        # leave the connection's needs_rollback flag stuck even after the
        # caller's savepoint_rollback, breaking every later query in the
        # request (TransactionManagementError) - same fix as
        # helpers/vendor.py's _get_or_create_vendor.
        with transaction.atomic():
            product = Product.objects.create(
                shop_service=shop_service,
                name=product_name,
                category=item_data.get("category") or None,
                color=item_data.get("color") or None,
                model=item_data.get("model") or None,
                purchase_price=unit_purchase_price,
            )
    except IntegrityError as e:
        # Product has two unique constraints (services/models/product.py)
        # that can both raise IntegrityError from this same create() call:
        # unique_product_ci_per_shop (name/color/model/category duplicate -
        # handled below) and unique_product_code_per_shop_service (a
        # product_code counter-race, auto-generated in Product.save() -
        # same class of race already fixed for Purchase.purchase_code
        # elsewhere). Only the former is a real "duplicate product" the
        # user can act on - anything else must fall through to the generic
        # 500 below instead of being misreported as a duplicate.
        if "unique_product_ci_per_shop" not in str(e):
            logger.error(f"Failed to create new product for purchase: {str(e)}", exc_info=True)
            return None, self.error_response(
                message="Failed to create new product.",
                dev_message=str(e),
            )

        # a product with this name/color/model/category already exists for
        # this shop_service. Distinct from the generic 500 below so the
        # user can identify and rename/reuse the existing product, instead
        # of seeing an opaque "Failed to create" error - look up the actual
        # colliding row using the exact same case-insensitive/null-coalescing
        # logic as the DB constraint, so the reported name is guaranteed to
        # match the real conflict, not a guess.
        existing = Product.objects.annotate(
            _n=Lower(Coalesce("name", Value(""))),
            _c=Lower(Coalesce("color", Value(""))),
            _m=Lower(Coalesce("model", Value(""))),
            _cat=Lower(Coalesce("category", Value(""))),
        ).filter(
            shop_service=shop_service,
            deleted__isnull=True,
            _n=(product_name or "").lower(),
            _c=(item_data.get("color") or "").lower(),
            _m=(item_data.get("model") or "").lower(),
            _cat=(item_data.get("category") or "").lower(),
        ).first()
        existing_name = existing.name if existing else product_name
        message = f"Product '{existing_name}' already exists."
        logger.warning(f"New-product create collision for purchase: {str(e)}")
        return None, self.validation_error_response(
            errors={"product_name": [message]},
            message=message,
        )
    except Exception as e:
        logger.error(f"Failed to create new product for purchase: {str(e)}", exc_info=True)
        return None, self.error_response(
            message="Failed to create new product.",
            dev_message=str(e),
        )

    line_group = uuid.uuid4()
    purchase_items = []
    for variant_entry in variants_data:
        variant_name = (variant_entry.get("variant_name") or "").strip()
        try:
            variant_quantity = int(variant_entry.get("quantity"))
            if variant_quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, self.validation_error_response(
                errors={"variants": ["Each variant requires a positive quantity."]},
                message="Invalid variant quantity.",
            )

        variant = ProductVariant.objects.create(
            product=product,
            attribute=variant_name,
            stock=variant_quantity,
        )
        StockMovement.objects.create(
            shop=shop,
            product_variant=variant,
            purchase=purchase,
            movement_type="purchase",
            previous_stock=0,
            quantity_changed=variant_quantity,
            new_stock=variant_quantity,
            reference_number=purchase.purchase_code or None,
        )
        purchase_items.append(
            PurchaseItem.objects.create(
                purchase=purchase,
                product_variant=variant,
                product_name=product.name,
                variant_other=variant_name,
                quantity=variant_quantity,
                purchase_amount=unit_purchase_price,
                stock_increased=True,
                line_group=line_group,
                item_source="new",
            )
        )

    return purchase_items, None


def _update_existing_purchase_item(self, purchase, shop, item_data):
    """
    True in-place edit of an already-added PurchaseItem (used by update.py's
    update_existing_items[] - distinct from _resolve_existing_product_item,
    which only ever creates new rows). Expects {item_id, quantity?,
    unit_purchase_price?, increment_stock?, update_product_cost?} - all
    fields but item_id are optional; only provided fields change.

    product_variant/line_group/item_source are immutable here - this never
    repoints which product/variant a line refers to (adding a new line +
    deleting the old one is the way to change *what* was purchased; this
    is only for correcting quantity/price/flags on what's already there).

    Returns (purchase_item, error_response).
    """
    item_id = item_data.get("item_id")
    item = purchase.items.filter(id=item_id).select_related("product_variant").first()
    if not item:
        return None, self.validation_error_response(
            errors={"item_id": ["Purchase item not found for this purchase."]},
            message="Purchase item not found for this purchase.",
        )

    new_quantity = item.quantity
    if "quantity" in item_data:
        try:
            new_quantity = int(item_data.get("quantity"))
            if new_quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, self.validation_error_response(
                errors={"quantity": ["quantity must be a positive integer."]},
                message="Invalid quantity.",
            )

    new_purchase_amount = item.purchase_amount
    if "unit_purchase_price" in item_data:
        new_purchase_amount, error = self._to_decimal(item_data.get("unit_purchase_price"), "unit_purchase_price")
        if error:
            return None, self.validation_error_response(
                errors={"unit_purchase_price": ["unit_purchase_price must be a non-negative number."]},
                message="Invalid purchase price.",
            )

    new_increment_stock = bool(item_data.get("increment_stock")) if "increment_stock" in item_data else item.stock_increased
    new_update_product_cost = bool(item_data.get("update_product_cost")) if "update_product_cost" in item_data else item.update_product_cost

    # Single delta formula correctly covers every on/off x quantity-change
    # combination: both-on with a changed quantity -> partial delta;
    # on->off -> full reversal; off->on -> full application; both-off ->
    # delta 0, no stock touched at all.
    effective_old = item.quantity if item.stock_increased else 0
    effective_new = new_quantity if new_increment_stock else 0
    delta = effective_new - effective_old

    if delta != 0 and item.product_variant_id:
        variant = ProductVariant.objects.select_for_update().get(id=item.product_variant_id)
        stock_before = variant.stock
        # Greatest() floors at 0 regardless of delta's sign - safe for
        # both increases and decreases, no separate branch needed.
        ProductVariant.objects.filter(id=variant.id).update(stock=Greatest(F("stock") + delta, 0))
        variant.refresh_from_db(fields=["stock"])
        StockMovement.objects.create(
            shop=shop,
            product_variant=variant,
            purchase=purchase,
            movement_type="adjustment",
            previous_stock=stock_before,
            quantity_changed=variant.stock - stock_before,
            new_stock=variant.stock,
            reference_number=purchase.purchase_code or None,
            notes=f"Stock adjusted - purchase item {item.id} edited.",
        )

    if new_update_product_cost and item.product_variant_id:
        # Applied once, same "raw update" pattern _resolve_existing_product_item
        # uses. Turning the flag off is deliberately never an undo - there's
        # no reliable prior price to restore (a later manual change could
        # have already superseded it) - so False is a one-way "stop
        # tracking this as the cost source," not a revert.
        Product.objects.filter(id=item.product_variant.product_id).update(purchase_price=new_purchase_amount)

    item.quantity = new_quantity
    item.purchase_amount = new_purchase_amount
    item.stock_increased = new_increment_stock
    item.update_product_cost = new_update_product_cost
    item.save()  # recalculates line_total automatically

    return item, None


def _product_has_bookings(product_id):
    """Whether any variant of this product has ever been booked - active
    or historical (a soft-deleted/completed booking's BookedProductVariant
    row still exists and still counts)."""
    from booking_v2.models import BookedProductVariant
    return BookedProductVariant.objects.filter(product_variant__product_id=product_id).exists()


def _reverse_purchase_item_stock(self, purchase_item, shop, reason="reversal"):
    """
    Reverse the stock this item added, floored at 0 (Greatest() DB
    expression avoids a negative-stock race with concurrent stock changes).
    Logs a compensating negative StockMovement.

    item_source == "new": the product was created inline for this purchase
    - deleting the item also removes its ProductVariant (and the parent
    Product too, if that was its last remaining variant), soft-delete,
    guarded by a bookings check that runs before any mutation. Blocked
    entirely (no stock/variant/product change at all) if the product has
    ever been booked.

    item_source == "existing" (or a variant already hard-deleted): only
    reverses ProductVariant.stock, unchanged from before - never touches
    Product/ProductVariant rows themselves.

    Returns (success, error_response) - error_response is not None only
    when a "new" item's product is booking-blocked.
    """
    if purchase_item.item_source == "new" and purchase_item.product_variant_id:
        # Locked re-fetch (not the already-in-memory purchase_item.product_variant)
        # so stock_before/the StockMovement audit row can't go stale against a
        # concurrent mutation of the same variant (e.g. _update_existing_purchase_item
        # running against the same purchase's items in a parallel request).
        variant = ProductVariant.objects.select_for_update().get(id=purchase_item.product_variant_id)
        product = variant.product
        if _product_has_bookings(product.id):
            return None, self.validation_error_response(
                errors={"item_id": [
                    "Cannot delete purchase item because the associated newly "
                    "created product has active or historical bookings."
                ]},
                message="Cannot delete purchase item because the associated newly created product has active or historical bookings.",
            )

        stock_before = variant.stock
        if stock_before:
            ProductVariant.objects.filter(id=variant.id).update(stock=0)
            StockMovement.objects.create(
                shop=shop,
                product_variant=variant,
                purchase=purchase_item.purchase,
                movement_type="adjustment",
                previous_stock=stock_before,
                quantity_changed=-stock_before,
                new_stock=0,
                notes=f"Stock cleared - purchase item {purchase_item.id} deleted, associated new product removed.",
            )
        variant.delete()  # soft delete
        # Locked re-fetch of the product row too - guards the sibling-variant
        # check against two concurrent deletions (different variants of the
        # same inline-created product) racing on the same "any active
        # siblings left?" read.
        product = Product.objects.select_for_update().get(id=product.id)
        if not product.variants.filter(deleted__isnull=True).exists():
            product.delete()  # soft delete - only when no sibling variants remain
        return True, None

    if not purchase_item.stock_increased or not purchase_item.product_variant_id:
        return True, None

    variant = ProductVariant.objects.select_for_update().filter(
        id=purchase_item.product_variant_id
    ).first()
    if not variant:
        return True, None

    stock_before = variant.stock
    ProductVariant.objects.filter(id=variant.id).update(
        stock=Greatest(F("stock") - purchase_item.quantity, 0)
    )
    variant.refresh_from_db(fields=["stock"])

    StockMovement.objects.create(
        shop=shop,
        product_variant=variant,
        purchase=purchase_item.purchase,
        movement_type="adjustment",
        previous_stock=stock_before,
        quantity_changed=-(stock_before - variant.stock),
        new_stock=variant.stock,
        notes=f"Stock reversed - purchase item {purchase_item.id} {reason}.",
    )
    return True, None


__all__ = [
    "MAX_PRODUCTS_PER_PURCHASE",
    "_resolve_existing_product_item",
    "_create_new_product_item",
    "_update_existing_purchase_item",
    "_reverse_purchase_item_stock",
]
