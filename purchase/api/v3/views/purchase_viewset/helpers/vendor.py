import logging

from django.db import IntegrityError, transaction

from purchase.models import Vendor

logger = logging.getLogger("api_requests")


def _apply_vendor_updates(self, vendor, vendor_data, user=None):
    """
    Apply any provided vendor{} fields that differ from the current
    record. No-op (no save, no updated_at bump) if nothing actually
    changed - only touches fields explicitly present in vendor_data.
    """
    changed_fields = []
    for field in ("name", "contact_number", "email", "location", "gst_number"):
        if field not in vendor_data:
            continue
        new_value = vendor_data.get(field)
        if field == "name":
            new_value = (new_value or "").strip() or vendor.name  # never blank out a name
        if getattr(vendor, field) != new_value:
            setattr(vendor, field, new_value)
            changed_fields.append(field)
    if changed_fields:
        if user:
            vendor.updated_by = user
            changed_fields.append("updated_by")
        vendor.save(update_fields=changed_fields + ["updated_at"])


def _get_or_create_vendor(self, request_data, shop, user=None):
    """
    Resolve the vendor for a purchase.
    - If vendor_id is given, reuse that vendor (must exist, belong to this
      shop, not soft-deleted) - used as-is, no field updates.
    - Otherwise, expect a `vendor` dict with at least `name`. An existing
      vendor with the same name (case-insensitive, shop-scoped, not
      soft-deleted - same uniqueness rule VendorSerializer enforces) is
      reused - and updated in place with any differing provided fields;
      only if none exists is a new Vendor created.
    Returns (vendor, error_response).
    """
    vendor_id = request_data.get("vendor_id")

    if vendor_id:
        vendor = Vendor.objects.filter(
            id=vendor_id, shop_id=shop.id, deleted__isnull=True
        ).first()
        if not vendor:
            return None, self.validation_error_response(
                errors={"vendor_id": ["Vendor not found for this shop."]},
                message="Vendor not found.",
            )
        return vendor, None

    vendor_data = request_data.get("vendor") or {}
    name = (vendor_data.get("name") or "").strip()
    if not name:
        return None, self.validation_error_response(
            errors={"vendor": ["vendor_id or vendor.name is required."]},
            message="Vendor is required.",
        )

    # Reuse an existing vendor with this name for this shop, if any - avoids
    # hitting the unique_vendor_name_per_shop constraint whenever a caller
    # repeats a known vendor's name via `vendor` instead of `vendor_id`.
    existing = Vendor.objects.filter(
        shop_id=shop.id, name__iexact=name, deleted__isnull=True
    ).first()
    if existing:
        self._apply_vendor_updates(existing, vendor_data, user)
        return existing, None

    try:
        # Nested atomic() so a race against a concurrent request creating
        # the same vendor name is recoverable without poisoning the outer
        # transaction - a bare try/except around a raw IntegrityError here
        # would leave the connection's needs_rollback flag stuck even after
        # the caller's savepoint_rollback, breaking every later query in
        # the request (TransactionManagementError).
        with transaction.atomic():
            vendor = Vendor.objects.create(
                shop=shop,
                name=name,
                contact_number=vendor_data.get("contact_number") or None,
                email=vendor_data.get("email") or None,
                location=vendor_data.get("location") or None,
                gst_number=vendor_data.get("gst_number") or None,
                created_by=user,
            )
    except IntegrityError:
        # Lost a race against a concurrent request creating the same
        # vendor name - reuse the one that won.
        vendor = Vendor.objects.filter(
            shop_id=shop.id, name__iexact=name, deleted__isnull=True
        ).first()
        if not vendor:
            logger.error(f"Vendor create race for '{name}' but no matching vendor found afterward.")
            return None, self.error_response(message="Failed to create vendor.")
    except Exception as e:
        logger.error(f"Failed to create vendor: {str(e)}", exc_info=True)
        return None, self.error_response(
            message="Failed to create vendor.",
            dev_message=str(e),
        )

    return vendor, None


__all__ = ["_get_or_create_vendor", "_apply_vendor_updates"]
