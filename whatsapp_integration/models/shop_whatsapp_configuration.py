from django.db import models
from django_cryptography.fields import encrypt

from shop.models import Shop, ShopUser


class ShopWhatsAppConfiguration(models.Model):
    """
    Phase 1 (models only): this table stores each shop's own WhatsApp Business
    Account (WABA) connection to Meta's Graph API, replacing the single shared
    Wacto-backed number used today. Nothing yet reads or writes this table —
    see whatsapp_integration/v3/tasks.py and services.py for the still-active
    Wacto-based sending path this will eventually replace (future phase).
    """

    shop = models.OneToOneField(
        Shop,
        on_delete=models.CASCADE,
        related_name="whatsapp_configuration",
        help_text="The shop this WhatsApp connection belongs to. One shop = one active connection today.",
    )

    provider = models.CharField(
        max_length=20,
        default="meta",
        help_text=(
            "Which messaging provider this configuration talks to. Always 'meta' for now "
            "(direct Graph API integration). Exists so a second provider could be added later "
            "without a schema change; not used for any branching logic yet."
        ),
    )

    business_account_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Meta WhatsApp Business Account (WABA) ID that owns the phone number below. Not secret.",
    )
    phone_number_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Meta's internal ID for the connected phone number — this is the value used in the "
            "Graph API send URL (.../{phone_number_id}/messages), not the phone number itself. Not secret."
        ),
    )
    phone_number = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text=(
            "Human-readable display phone number (e.g. +91XXXXXXXXXX), for showing in the dashboard "
            "only. Not used to address API calls."
        ),
    )

    access_token = encrypt(models.TextField(
        help_text=(
            "Meta Graph API access token used to authenticate send requests for this shop's number. "
            "Encrypted at rest (django-cryptography) and never returned by any API response."
        ),
    ))
    app_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Meta App ID this connection was established through. Expected to be the platform's single "
            "shared BookieBuddy Meta App for all shops; stored per-row so an individual shop could be "
            "repointed to a different app later without a schema change."
        ),
    )
    app_secret = encrypt(models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Secret for the Meta App identified by app_id. Needed to verify inbound webhook signatures "
            "(X-Hub-Signature-256). Encrypted at rest (django-cryptography)."
        ),
    ))

    CONNECTION_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("ACTIVE", "Active"),
        ("INVALID_TOKEN", "Invalid Token"),
        ("TOKEN_EXPIRED", "Token Expired"),
        ("PHONE_REMOVED", "Phone Number Removed"),
        ("RATE_LIMITED", "Rate Limited"),
        ("REVOKED", "Revoked"),
        ("ERROR", "Unknown Error"),
    ]
    connection_status = models.CharField(
        max_length=20,
        choices=CONNECTION_STATUS_CHOICES,
        default="PENDING",
        help_text=(
            "Current health of this shop's WhatsApp connection. Set to PENDING on creation, ACTIVE once "
            "a real test send succeeds (verified=True), and to one of the specific failure states when a "
            "send fails so support can tell what's wrong without reading logs. The logic that transitions "
            "this field is future-phase work — the column exists now so it doesn't require a later "
            "migration."
        ),
    )
    verified = models.BooleanField(
        default=False,
        help_text=(
            "True only after a real outbound test message was successfully sent through this connection. "
            "A token/phone_number_id can be present and 'look' valid while the number still isn't actually "
            "message-capable — this flag distinguishes 'credentials saved' from 'confirmed working'."
        ),
    )
    token_expiry = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When access_token expires, if it expires at all. FLAGGED AS UNCERTAIN: whether Embedded "
            "Signup yields a token that expires depends on the platform's Meta Tech Provider / Solution "
            "Partner enrollment, which has not been confirmed against current Meta documentation. Left "
            "nullable so both a non-expiring token (null) and an expiring one (populated) are supported "
            "without a schema change; the logic that populates or acts on it is future-phase work."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True, help_text="When this configuration row was first created.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When this configuration row was last modified.")

    class Meta:
        db_table = "whatsapp_shop_configuration"

    def __str__(self):
        return f"WhatsApp configuration for {self.shop.name} ({self.connection_status})"
