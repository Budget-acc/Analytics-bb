"""
Share codes: let a shop hand a copy of one of its customized invoices to
another shop (paste the code or scan its QR).

- Codes are 12 characters from an unambiguous alphabet (no 0/O/1/I/L),
  generated with nanoid (OS-level secure randomness): 31^12 ~ 7.9e17 codes.
  Global uniqueness is enforced by the DB unique constraint; a collision is
  retried.
- Name + configuration are snapshotted at generation time: the code always
  imports exactly what was shared and always targets the same template.
- At most one active code per customized invoice (partial unique constraint);
  deleting the customized invoice deletes its codes.
- Codes expire SHARE_CODE_TTL (24h) after creation; legacy codes stored
  without expires_at expire the same time after created_at.
- Importing creates a NEW customized invoice in the receiving shop, so it is
  subject to the per-shop creation limit.
- Lookup/import never reveal the source shop. An expired code gets its own
  error (ShareError.EXPIRED); every other invalid case (unknown, revoked,
  template/shop inactive) gets the same generic error.
- Import re-checks the code under a row lock inside its transaction, so a
  code that expires or is revoked mid-request imports nothing.
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from nanoid import generate

from invoice_templates.models import InvoiceTemplateShare, ShopCustomInvoice
from invoice_templates.v3.registry import get_definition

from .configuration import (
    effective_configuration, migrate_configuration, snapshot, validate_configuration,
)
from .custom_invoices import create_custom_invoice
from .formatting import format_datetime
from .preferences import set_default_custom_invoice

logger = logging.getLogger("api_requests")

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 12
MAX_GENERATION_ATTEMPTS = 5
QR_PAYLOAD_PREFIX = "bookiebuddy://invoice-template/import?code="
SHARE_CODE_TTL = timedelta(hours=24)

_ALPHABET_SET = frozenset(ALPHABET)


class ShareError(Exception):
    INVALID = "invalid"            # unknown / revoked / unavailable
    EXPIRED = "expired"            # past its 24h validity
    OWN_CODE = "own_code"        # importing the shop's own code
    NOT_FOUND = "not_found"        # no active code to revoke

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def normalize_code(raw):
    """'abcd-efgh ijkm' -> 'ABCDEFGHIJKM'; None when it cannot be a valid code."""
    if not isinstance(raw, str):
        return None
    code = "".join(raw.split()).replace("-", "").upper()
    if len(code) != CODE_LENGTH or not set(code) <= _ALPHABET_SET:
        return None
    return code


def display_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def qr_payload(code: str) -> str:
    return f"{QR_PAYLOAD_PREFIX}{code}"


def share_expires_at(share):
    """Expiry of a code; legacy codes without one expire SHARE_CODE_TTL after creation."""
    return share.expires_at or share.created_at + SHARE_CODE_TTL


def _active_filter(now=None):
    now = now or timezone.now()
    return Q(revoked_at__isnull=True) & (
        Q(expires_at__gt=now) | Q(expires_at__isnull=True, created_at__gt=now - SHARE_CODE_TTL)
    )


def get_active_share(invoice):
    return (
        InvoiceTemplateShare.objects.filter(_active_filter(), source_custom_invoice=invoice)
        .order_by("-created_at").first()
    )


def is_outdated(share, invoice_updated_at) -> bool:
    """True when the customized invoice was changed after the code was made."""
    return bool(invoice_updated_at and share and invoice_updated_at > share.created_at)


def share_payload(share, invoice) -> dict:
    return {
        "share_code": share.code,
        "display_code": display_code(share.code),
        "qr_payload": qr_payload(share.code),
        "custom_invoice_id": invoice.id,
        "invoice_name": share.invoice_name,
        "template_code": invoice.template.code,
        "created_at": format_datetime(share.created_at),
        "expires_at": format_datetime(share_expires_at(share)),
        "import_count": share.import_count,
        "is_outdated": is_outdated(share, invoice.updated_at),
    }


def generate_share(invoice, user):
    """
    Return (share, created) for a customized invoice. Reuses the active code
    when it has not expired and still matches the invoice's name and settings;
    otherwise revokes it and issues a new one valid for SHARE_CODE_TTL.
    Serialized per invoice by locking its row.
    """
    definition = get_definition(invoice.template.code)
    with transaction.atomic():
        locked = ShopCustomInvoice.objects.select_for_update().get(pk=invoice.pk)
        config = effective_configuration(definition, locked.configuration, locked.template_version)
        now = timezone.now()
        active = (
            InvoiceTemplateShare.objects.select_for_update()
            .filter(source_custom_invoice=locked, revoked_at__isnull=True).first()
        )
        if active is not None:
            still_valid = share_expires_at(active) > now
            if still_valid and active.template_version == definition.version \
                    and active.configuration_snapshot == config and active.invoice_name == locked.name:
                return active, False
            active.revoked_at = now
            active.save(update_fields=["revoked_at"])

        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            code = generate(ALPHABET, CODE_LENGTH)
            try:
                with transaction.atomic():
                    share = InvoiceTemplateShare.objects.create(
                        code=code,
                        source_shop_id=locked.shop_id,
                        source_custom_invoice=locked,
                        template_id=locked.template_id,
                        invoice_name=locked.name,
                        configuration_snapshot=snapshot(config),
                        template_version=definition.version,
                        expires_at=now + SHARE_CODE_TTL,
                        created_by=user if getattr(user, "pk", None) else None,
                    )
                return share, True
            except IntegrityError:
                if attempt == MAX_GENERATION_ATTEMPTS or not InvoiceTemplateShare.objects.filter(code=code).exists():
                    # Not a code collision (or out of retries) - surface it.
                    raise
                logger.warning(f"Invoice template share code collision on attempt {attempt}; retrying.")
    raise IntegrityError("Could not generate a unique share code.")  # pragma: no cover


def revoke_share(invoice) -> int:
    revoked = InvoiceTemplateShare.objects.filter(
        source_custom_invoice=invoice, revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())
    if not revoked:
        raise ShareError(ShareError.NOT_FOUND)
    return revoked


def _check_share(share, now):
    """
    Raise ShareError unless the share can be imported at `now`.

    Unavailable (template / source shop inactive) stays generic even when the
    code is also expired. An expired code is reported as EXPIRED even when
    generate has since revoked it (it revokes expired codes when it issues a
    new one); a code revoked while still valid is INVALID.
    """
    if (
        share is None
        or not share.template.is_active
        or not share.source_shop.is_active
        or share.source_shop.deleted is not None
        or get_definition(share.template.code) is None
    ):
        raise ShareError(ShareError.INVALID)
    if share_expires_at(share) <= now:
        raise ShareError(ShareError.EXPIRED)
    if share.revoked_at is not None:
        raise ShareError(ShareError.INVALID)


def lookup_share(raw_code):
    """Active, importable share for a code, or ShareError(INVALID / EXPIRED)."""
    code = normalize_code(raw_code)
    if code is None:
        raise ShareError(ShareError.INVALID)
    share = (
        InvoiceTemplateShare.objects.select_related("template", "source_shop")
        .filter(code=code).first()
    )
    _check_share(share, timezone.now())
    return share


def shared_configuration(share) -> dict:
    """The snapshot migrated to the current template version, strictly validated."""
    definition = get_definition(share.template.code)
    migrated = migrate_configuration(definition, share.configuration_snapshot, share.template_version)
    return validate_configuration(definition, migrated)


def import_share(shop_id, raw_code, user, name=None, set_as_default=False):
    """
    Create a NEW customized invoice in this shop from a share code (same
    template, copied settings). `name` defaults to the shared invoice's name,
    made unique. Returns (share, invoice, usage).
    Raises ShareError, ConfigurationError, LimitReached, NameTaken, InvalidName.
    """
    share = lookup_share(raw_code)
    if share.source_shop_id == shop_id:
        raise ShareError(ShareError.OWN_CODE)
    definition = get_definition(share.template.code)
    clean = shared_configuration(share)

    with transaction.atomic():
        # Re-check at write time on the locked row: the code may have expired,
        # or been revoked / replaced by generate (which locks the same row),
        # since the lookup above. Raising here rolls back; nothing is created.
        locked = (
            InvoiceTemplateShare.objects.select_for_update(of=("self",))
            .select_related("template", "source_shop")
            .filter(pk=share.pk).first()
        )
        _check_share(locked, timezone.now())

        invoice, shop_usage = create_custom_invoice(
            shop_id, share.template, definition, clean, user,
            name=name, default_name=share.invoice_name or share.template.name,
        )
        if set_as_default:
            set_default_custom_invoice(shop_id, invoice, user)
        InvoiceTemplateShare.objects.filter(pk=share.pk).update(
            import_count=F("import_count") + 1, last_imported_at=timezone.now(),
        )
    return share, invoice, shop_usage
