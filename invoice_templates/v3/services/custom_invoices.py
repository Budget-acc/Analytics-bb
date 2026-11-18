"""
Customized invoices: a shop-named copy of a predefined template with the
shop's settings.

Rules
- Each shop can CREATE at most settings.INVOICE_CUSTOM_INVOICE_LIMIT (4)
  customized invoices; the count is per shop. Deleting one frees a slot.
- Updating (template / name / settings) is unlimited and never counts as a creation.
- Names are optional (default: the template name, made unique), trimmed,
  1-60 characters and unique per shop, case-insensitive.

Concurrency: creation locks the shop row (SELECT ... FOR UPDATE) before
counting, so two simultaneous requests can never both take the last slot.
The DB unique constraint on (shop, lower(name)) is the backstop for names.
"""
import re

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from invoice_templates.models import InvoiceTemplateShare, ShopCustomInvoice
from shop.models import Shop

from .configuration import snapshot

NAME_MAX_LENGTH = 60
_FORBIDDEN_NAME_RE = re.compile(r"[\x00-\x1f\x7f<>{}]")
_UNIQUENESS_CONSTRAINT = "uniq_shop_custom_invoice_name_ci"


class CustomInvoiceError(Exception):
    pass


class LimitReached(CustomInvoiceError):
    def __init__(self, limit):
        super().__init__(f"Customized invoice limit of {limit} reached.")
        self.limit = limit


class NameTaken(CustomInvoiceError):
    pass


class InvalidName(CustomInvoiceError):
    pass


def creation_limit() -> int:
    return int(getattr(settings, "INVOICE_CUSTOM_INVOICE_LIMIT", 4))


def usage(shop_id, used=None) -> dict:
    """{used, limit, remaining} for the shop (1 COUNT query unless `used` is given)."""
    limit = creation_limit()
    if used is None:
        used = ShopCustomInvoice.objects.filter(shop_id=shop_id).count()
    return {"used": used, "limit": limit, "remaining": max(limit - used, 0)}


def normalize_name(raw):
    """Trimmed, single-spaced name; raises InvalidName when not acceptable."""
    if not isinstance(raw, str):
        raise InvalidName()
    name = " ".join(raw.split())
    if not 1 <= len(name) <= NAME_MAX_LENGTH or _FORBIDDEN_NAME_RE.search(raw.strip()):
        raise InvalidName()
    return name


def name_exists(shop_id, name, exclude_id=None) -> bool:
    qs = ShopCustomInvoice.objects.annotate(lname=Lower("name")).filter(shop_id=shop_id, lname=name.lower())
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()


def unique_default_name(shop_id, base) -> str:
    """'Classic' -> 'Classic', 'Classic 2', 'Classic 3', ... (one query)."""
    base = " ".join(str(base).split())[: NAME_MAX_LENGTH - 4] or "Invoice"
    taken = {
        n.lower() for n in ShopCustomInvoice.objects.filter(shop_id=shop_id, name__istartswith=base)
        .values_list("name", flat=True)
    }
    name, n = base, 2
    while name.lower() in taken:
        name = f"{base} {n}"
        n += 1
    return name


def _is_name_conflict(error) -> bool:
    return _UNIQUENESS_CONSTRAINT in str(error)


def _user_ref(user):
    return user if getattr(user, "pk", None) else None


def create_custom_invoice(shop_id, template, definition, configuration, user, name=None, default_name=None):
    """
    Create a customized invoice, enforcing the per-shop creation limit.
    `name` (explicit) must be unique; when omitted, `default_name` (or the
    template name) is made unique automatically. `configuration` must already
    be strictly validated.
    Raises LimitReached, NameTaken or InvalidName.
    """
    explicit_name = normalize_name(name) if name is not None else None
    limit = creation_limit()
    with transaction.atomic():
        # Serializes creations for this shop: the count below cannot change
        # until this transaction ends.
        Shop.objects.select_for_update().only("id").get(id=shop_id)
        used = ShopCustomInvoice.objects.filter(shop_id=shop_id).count()
        if used >= limit:
            raise LimitReached(limit)

        if explicit_name is not None:
            if name_exists(shop_id, explicit_name):
                raise NameTaken()
            final_name = explicit_name
        else:
            final_name = unique_default_name(shop_id, default_name or template.name)

        user_ref = _user_ref(user)
        try:
            with transaction.atomic():
                invoice = ShopCustomInvoice.objects.create(
                    shop_id=shop_id,
                    template=template,
                    name=final_name,
                    configuration=snapshot(configuration),
                    template_version=definition.version,
                    created_by=user_ref,
                    updated_by=user_ref,
                )
        except IntegrityError as e:
            if _is_name_conflict(e):
                raise NameTaken()
            raise
    invoice.template = template
    return invoice, usage(shop_id, used=used + 1)


def update_custom_invoice(invoice, definition, user, name=None, configuration=None, template=None):
    """
    Rename, replace the settings of and/or switch the template of an existing
    customized invoice. Unlimited - never touches the creation count. When
    `template` is given, `definition` and `configuration` must belong to it;
    the invoice's active share code is revoked (codes are bound to a template).
    Raises NameTaken or InvalidName.
    """
    update_fields = ["updated_by", "updated_at"]
    switch_template = template is not None and template.id != invoice.template_id
    if switch_template:
        invoice.template = template
        update_fields.append("template")
    if name is not None:
        new_name = normalize_name(name)
        if new_name != invoice.name:
            if name_exists(invoice.shop_id, new_name, exclude_id=invoice.id):
                raise NameTaken()
            invoice.name = new_name
            update_fields.append("name")
    if configuration is not None:
        invoice.configuration = snapshot(configuration)
        invoice.template_version = definition.version
        update_fields += ["configuration", "template_version"]
    invoice.updated_by = _user_ref(user)
    try:
        with transaction.atomic():
            invoice.save(update_fields=update_fields)
            if switch_template:
                InvoiceTemplateShare.objects.filter(
                    source_custom_invoice=invoice, revoked_at__isnull=True,
                ).update(revoked_at=timezone.now())
    except IntegrityError as e:
        if _is_name_conflict(e):
            raise NameTaken()
        raise
    return invoice


def delete_custom_invoice(invoice):
    """
    Delete a customized invoice (frees a slot). The shop's default falls back
    to Standard if it pointed here (FK SET_NULL) and its share codes are
    removed (FK CASCADE).
    """
    invoice.delete()


def get_shop_invoice(shop_id, invoice_id, for_update=False):
    """The shop's customized invoice by id, or None (another shop's id -> None)."""
    try:
        invoice_id = int(invoice_id)
    except (TypeError, ValueError):
        return None
    qs = ShopCustomInvoice.objects.select_related("template")
    if for_update:
        qs = qs.select_for_update()
    return qs.filter(id=invoice_id, shop_id=shop_id).first()
