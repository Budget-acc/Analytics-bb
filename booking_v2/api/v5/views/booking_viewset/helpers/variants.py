import logging
from datetime import datetime, date
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.dateparse import parse_datetime
from decimal import Decimal
from services.models import ProductVariant
from booking_v2.models import BookedProductVariant
from booking_v2.utils.measurements import to_stored_measurements
from booking_v2.api.v5.utils.availability import (
    check_product_variant_availability,
    add_product_variant_availability,
    remove_product_variant_availability,
)

logger = logging.getLogger("api_requests")


def _validate_delivery_status(self, delivery_status, pickup_date):
    """
    Validate delivery status based on pickup date.
    Returns error_response or None
    """
    if delivery_status == "returned":
        return self.validation_error_response(
            errors={"delivery_status": ["Cannot set delivery status to 'returned' for a new booking."]},
            message="New bookings can't be marked as returned."
        )

    if delivery_status == "delivered" and pickup_date.date() > date.today():
        return self.validation_error_response(
            errors={"delivery_status": ["Items can only be marked as delivered on the pickup date."]},
            message="Cannot set to 'delivered' before the pickup day."
        )

    return None


DELETED_PRODUCT_MESSAGE = "The selected product has been deleted and cannot be used for a booking."


def _validate_variant_products(self, variant_ids, shop_id):
    """
    Ensure every variant ID exists in this shop and that neither the variant
    nor its parent product is soft-deleted.
    Returns error_response or None
    """
    try:
        ids = {int(vid) for vid in variant_ids}
    except (TypeError, ValueError):
        return self.validation_error_response(
            errors={"variants": ["Each variant must have a valid integer id."]},
            message="Invalid product selected.",
            dev_message="variants[].id must be an integer ProductVariant ID.",
        )

    try:
        # all_objects includes soft-deleted rows so deleted variants/products
        # can be reported clearly instead of as "invalid".
        rows = list(
            ProductVariant.all_objects.filter(
                id__in=ids, product__shop_service__shop_id=shop_id
            ).values("id", "deleted", "product__deleted")
        )
    except Exception as e:
        logger.error(f"Product status validation failed for shop {shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to validate product variants.",
            dev_message="Product validation query failed.",
        )

    invalid_ids = ids - {row["id"] for row in rows}
    if invalid_ids:
        return self.validation_error_response(
            errors={"invalid_variant_ids": sorted(invalid_ids)},
            message="Some product variants are invalid.",
        )

    deleted_ids = sorted(
        row["id"] for row in rows if row["deleted"] or row["product__deleted"]
    )
    if deleted_ids:
        return self.validation_error_response(
            errors={
                "variants": [DELETED_PRODUCT_MESSAGE],
                "deleted_variant_ids": deleted_ids,
            },
            message=DELETED_PRODUCT_MESSAGE,
            dev_message=f"Variant IDs {deleted_ids} reference a soft-deleted ProductVariant or Product.",
        )

    return None


def _validate_variants(self, variants, shop, pickup_date, stock_end_date, cooling_period_end=None, cooling_period_type="after"):
    """
    Validate product variants and check stock availability.
    Returns error_response or None
    """
    if not variants:
        return self.validation_error_response(
            errors={"variants": ["Product variants are required."]},
            message="Product variants are required."
        )

    variant_ids = [v["id"] for v in variants]

    try:
        valid_variants = (
            ProductVariant.objects.filter(
                id__in=variant_ids, product__shop_service__shop=shop
            )
            .select_related("product__shop_service__shop")
            .values_list("id", flat=True)
        )

        invalid_ids = set(variant_ids) - set(valid_variants)
        if invalid_ids:
            return self.validation_error_response(
                errors={"invalid_variant_ids": list(invalid_ids)},
                message="Some product variants are invalid."
            )


        today = date.today()

        # check_start_date based on cooling_period_type
        if cooling_period_end and cooling_period_type == "before":
            # Cooling period is before pickup date
            check_start_date = cooling_period_end
            if isinstance(check_start_date, datetime) and isinstance(pickup_date, datetime):

                # If same date -> use pickup time
                if check_start_date.date() == pickup_date.date():
                    check_start_date = make_aware(datetime.combine(
                        check_start_date.date(),
                        pickup_date.time()
                    ))

            # If cooling date is past -> shift to today
            check_date = (
                check_start_date.date()
                if isinstance(check_start_date, datetime)
                else check_start_date
            )

            # If cooling period is in the past, use today
            if check_date < today:
                if isinstance(check_start_date, datetime):
                    check_start_date = make_aware(datetime.combine(today, check_start_date.time()))
                else:
                    check_start_date = today
        else:
            # Default behavior: use pickup_date
            check_start_date = pickup_date
            if hasattr(check_start_date, 'date'):
                check_date = check_start_date.date()
            else:
                check_date = check_start_date

            if check_date < today:
                if isinstance(check_start_date, datetime):
                    check_start_date = make_aware(datetime.combine(today, check_start_date.time()))
                else:
                    check_start_date = today

        stock_check = check_product_variant_availability(
            variants, check_start_date, stock_end_date
        )
        if stock_check:
            return stock_check

    except Exception as e:
        logger.error(f"Product variant validation failed: {str(e)}")
        return self.error_response(
            message="Failed to validate product variants.",
            dev_message=str(e)
        )

    return None


def _get_variant(self, identifier, identifier_type=None):
    qs = (
        ProductVariant.objects
        .select_related("product", "product__shop_service__shop")
        .filter(
            is_active=True,
            deleted__isnull=True,
            product__shop_service__shop_id=self.shop_id,
        )
    )

    if identifier_type == "external_qr":
        return qs.filter(external_qr_code=identifier).first()

    # default -> variant_id
    return qs.filter(id=identifier).first()


def _process_variants(self, booking, data, old_variants, booked_items_cache=None):
    """Process variant data. Uses a cached list of booked items when provided to avoid extra queries."""
    if "variants" not in data:
        return old_variants

    new_items = data["variants"]
    # Use provided cache when available to avoid extra queries
    if booked_items_cache is not None:
        existing_items_map = {item.product_variant_id: item for item in booked_items_cache}
    else:
        existing_items_map = {item.product_variant_id: item for item in booking.booked_items.all()}

    # Tax + discount are no longer applied here. `amount` is set to the raw,
    # as-received value for every item - the caller
    # (_calculate_and_validate_totals) is the single place that applies
    # discount/tax across ALL of a booking's items (not just the ones in
    # this request), without mutating any individual item's stored amount.
    # This means every sibling item is always recomputed consistently
    # whenever discount or any item changes.
    new_variants = []
    for item in new_items:
        vid = item["id"]
        existing = existing_items_map.get(vid)
        # Dict measurements are stored as an ordered list, as in the create method
        measurements = to_stored_measurements(
            item.get("measurements", existing.measurements if existing else {})
        )

        amount = item.get("amount", existing.amount if existing else 0)

        new_variants.append({
            "id": vid,
            "amount": amount,
            "quantity": item.get("quantity", existing.quantity if existing else 1),
            "measurements": measurements,
            "return_status": item.get(
                "return_status",
                existing.return_status if existing else "not_returned"
            ),
            "returned_at": item.get(
                "returned_at",
                existing.returned_at if existing else None
            ),
            "extended_until": item.get(
                "extended_until",
                existing.extended_until if existing else None
            ),
        })

    return new_variants


def _need_availability_check(self, old_variants, new_variants, old_pickup_date, new_pickup_date,
                        old_return_date, new_return_date, old_cooling_end, new_cooling_end, data):
    """
    Determine if availability check is needed based on changes
    """
    def variants_and_quantities_equal(old, new):
        def extract_data(v):
            return {"id": v["id"], "quantity": v.get("quantity", 1)}
        return sorted(map(extract_data, old), key=lambda x: x["id"]) == \
            sorted(map(extract_data, new), key=lambda x: x["id"])

    # Check if variants or quantities changed
    variants_quantities_same = variants_and_quantities_equal(old_variants, new_variants)

    # Check if dates changed
    dates_same = (
        new_pickup_date == old_pickup_date
        and new_return_date == old_return_date
        and new_cooling_end == old_cooling_end
    )

    # If only amounts changed (same variants, same quantities, same dates)
    if variants_quantities_same and dates_same:
        return False  # No availability check needed

    # If variants/quantities changed OR dates changed
    return True


def _handle_availability_check(
    self, booking, old_variants, new_variants,
    old_pickup_date, new_pickup_date,
    old_return_date, new_return_date,
    old_cooling_end, new_cooling_end,
    savepoint, user,
    cooling_period_type="after"
):
    try:
        # Get old cooling period type from booking details
        old_cooling_type = "after"
        if booking.details and isinstance(booking.details, dict):
            old_cooling_type = booking.details.get("cooling_period_type", "after")

        # OLD effective dates for removal
        old_effective_start, old_effective_end = self._get_effective_dates(
            old_pickup_date, old_return_date, old_cooling_end, old_cooling_type
        )

        remove_product_variant_availability(
            old_variants,
            old_effective_start.date(),
            old_effective_end.date()
        )

        # NEW effective dates for checking/adding
        new_effective_start, new_effective_end = self._get_effective_dates(
            new_pickup_date, new_return_date, new_cooling_end, cooling_period_type
        )

        # Determine check_start_date
        current_datetime = timezone.now()

        check_start_datetime = max(current_datetime, new_effective_start)

        variants_to_extend = [
            v for v in new_variants
            if v.get("return_status") != "returned"
        ]

        availability_resp = check_product_variant_availability(
            variants_to_extend,
            check_start_datetime,
            new_effective_end,
            exclude_booking_id=booking.id
        )

        if availability_resp:
            # Restore old availability
            add_product_variant_availability(
                old_variants,
                old_pickup_date.date(),
                old_effective_end.date()
            )
            transaction.savepoint_rollback(savepoint)
            return availability_resp

        # Add new availability using effective end only
        add_product_variant_availability(
            variants_to_extend,
            new_effective_start.date(),
            new_effective_end.date()
        )

        return self._update_variants(booking, new_variants, user, savepoint)

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Availability check failed for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to check product availability.",
            dev_message=str(e)
        )


def _update_variants(self, booking, new_variants, user, savepoint):
    """Update booking variants completely"""
    try:
        # Delete old booked items (use queryset delete for efficiency)
        booking.booked_items.all().delete()

        # Fetch all needed variants in a single query (avoids N+1 on ProductVariant.objects.get())
        variant_ids = [item["id"] for item in new_variants]
        variants_by_id = {
            variant.id: variant
            for variant in ProductVariant.objects.select_related("product").filter(id__in=variant_ids)
        }

        # Build new booked items in memory, then create them in a single bulk_create()
        new_booked_items = []
        for item in new_variants:
            variant = variants_by_id[item["id"]]
            amt = item.get("amount", variant.price or 0)

            returned_at = item.get("returned_at")
            extended_until = item.get("extended_until")

            if isinstance(returned_at, str):
                returned_at = parse_datetime(returned_at)
                if returned_at and timezone.is_naive(returned_at):
                    returned_at = timezone.make_aware(returned_at)

            if isinstance(extended_until, str):
                extended_until = parse_datetime(extended_until)
                if extended_until and timezone.is_naive(extended_until):
                    extended_until = timezone.make_aware(extended_until)

            new_booked_items.append(BookedProductVariant(
                booking=booking,
                product_variant_id=item["id"],
                user=user,
                shop=booking.shop,
                amount=amt,
                quantity=item.get("quantity", 1),
                product_name=variant.product.name[:500],
                variant_attribute=variant.attribute[:50] if variant.attribute else "",
                color=variant.product.color[:50] if variant.product.color else "",
                category=variant.product.category[:50] if variant.product.category else "",
                model=variant.product.model[:50] if variant.product.model else "",
                measurements=item.get("measurements", []),
                return_status=item.get("return_status", "not_returned"),
                returned_at=returned_at,
                extended_until=extended_until,
            ))

        if new_booked_items:
            BookedProductVariant.objects.bulk_create(new_booked_items)

        return None

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Failed to update variants for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to update booking variants.",
            dev_message=str(e)
        )


def _update_variant_amounts_only(self, booking, new_variants, savepoint):
    """Update only variant amounts and measurements without changing variants"""
    try:
        existing_items = {item.product_variant_id: item for item in booking.booked_items.all()}

        touched_items = []
        for item in new_variants:
            variant_id = item["id"]
            existing = existing_items.get(variant_id)
            if not existing:
                continue

            # Update amount if provided and positive
            if "amount" in item and item["amount"] > 0:
                existing.amount = item["amount"]

            # Update measurements if provided
            if "measurements" in item:
                # Dict measurements are stored as an ordered list, as in the create method
                existing.measurements = to_stored_measurements(item["measurements"])

            touched_items.append(existing)

        if touched_items:
            BookedProductVariant.objects.bulk_update(touched_items, ["amount", "measurements"])

        return None

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Failed to update variant amounts for booking {booking.id}: {str(e)}")
        return self.error_response(
            message="Failed to update variant details.",
            dev_message=str(e)
        )
