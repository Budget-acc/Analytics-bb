from rest_framework import serializers

from whatsapp_integration.models import ShopWhatsAppConfiguration, ShopWhatsAppTemplate


class ShopWhatsAppTemplateSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a single WhatsApp template. Used both nested
    (many) inside ShopWhatsAppConfigurationReadSerializer's `templates` field,
    and as the response shape for Create Template.
    """

    class Meta:
        model = ShopWhatsAppTemplate
        fields = [
            "id",
            "template_name",
            "meta_template_id",
            "language",
            "category",
            "status",
        ]
        read_only_fields = fields


class ShopWhatsAppTemplateWriteSerializer(serializers.ModelSerializer):
    """
    Used for both Create Template and the `templates` upsert array on Partial
    Update. `shop_whatsapp_configuration` is intentionally not a field here —
    the view resolves it from the URL and sets it explicitly on save. `id` is
    also excluded — it's popped from the raw entry dict by the caller before
    this serializer ever sees it, to decide create-vs-update.

    `template_name` is required when creating (instance=None) and optional
    when updating (partial=True passed by the caller) — DRF's partial mode
    handles that distinction automatically, no extra logic needed here.
    """

    class Meta:
        model = ShopWhatsAppTemplate
        fields = ["template_name", "meta_template_id", "language", "category", "status"]
        extra_kwargs = {
            "meta_template_id": {"required": False},
            "language": {"required": False},
            "category": {"required": False},
            "status": {"required": False},
        }


class ShopWhatsAppConfigurationWriteSerializer(serializers.ModelSerializer):
    """
    Used for Create and Partial Update. `shop` is intentionally not a field here —
    the view resolves it from the URL's shop_id and sets it explicitly on save, so
    it can never be spoofed via the request body or reassigned on update.
    """

    access_token = serializers.CharField(
        required=True,
        write_only=True,
        help_text="Bearer token used to authenticate with the third-party WhatsApp provider.",
    )
    app_secret = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        write_only=True,
        help_text="Secret for the connected Meta App, if applicable. Optional.",
    )

    class Meta:
        model = ShopWhatsAppConfiguration
        fields = [
            "provider",
            "business_account_id",
            "phone_number_id",
            "phone_number",
            "access_token",
            "app_id",
            "app_secret",
            "connection_status",
            "verified",
            "token_expiry",
        ]
        extra_kwargs = {
            "provider": {"required": False},
            "business_account_id": {"required": False},
            "phone_number_id": {"required": False},
            "phone_number": {"required": False},
            "app_id": {"required": False},
            "connection_status": {"required": False},
            "verified": {"required": False},
            "token_expiry": {"required": False},
        }


class ShopWhatsAppConfigurationReadSerializer(serializers.ModelSerializer):
    """
    Used for the response body of Retrieve/Create/Partial Update. Deliberately
    excludes `access_token` and `app_secret` — those are never returned by any
    API response, matching the model's own field documentation.
    """

    templates = ShopWhatsAppTemplateSerializer(many=True, read_only=True)

    class Meta:
        model = ShopWhatsAppConfiguration
        fields = [
            "id",
            "shop",
            "provider",
            "business_account_id",
            "phone_number_id",
            "phone_number",
            "app_id",
            "connection_status",
            "verified",
            "token_expiry",
            "created_at",
            "updated_at",
            "templates",
        ]
        read_only_fields = fields
