from rest_framework import serializers

from .template_settings import CONFIGURATION_HELP

NAME_HELP = (
    "Purpose: A name the shop gives this customized invoice so it can tell its invoices apart "
    "in the app (e.g. \"Wedding Season\"). It is NOT printed on the invoice. "
    "Location: request body. "
    "Format/Type: string, 1-60 characters after trimming, without < > { } or line breaks; "
    "unique within the shop (case-insensitive). "
)


class CreateCustomInvoiceRequestSerializer(serializers.Serializer):
    template_code = serializers.CharField(
        required=False,
        help_text=(
            "Purpose: The predefined template this customized invoice is built from. "
            "Location: request body. "
            "Format/Type: string - a template code from GET templates/ (e.g. \"standard\", \"classic\", \"simple\"). "
            "Required unless template_id is sent. Can be changed later via the update API."
        ),
    )
    template_id = serializers.IntegerField(
        required=False,
        help_text=(
            "Purpose: Alternative to template_code - the predefined template's numeric id. "
            "Location: request body. "
            "Format/Type: integer id from GET templates/ (e.g. 2). "
            "Required unless template_code is sent (if both are sent, template_id is used)."
        ),
    )

    def validate(self, attrs):
        if not attrs.get("template_id") and not (attrs.get("template_code") or "").strip():
            raise serializers.ValidationError({"template_code": "template_code or template_id is required."})
        return attrs
    name = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False,
        help_text=NAME_HELP + "Optional. Default: the template name, made unique (e.g. \"Classic\", \"Classic 2\").",
    )
    configuration = serializers.JSONField(
        required=False,
        help_text=CONFIGURATION_HELP + " Optional. Default: {} (all template defaults).",
    )


class UpdateCustomInvoiceRequestSerializer(serializers.Serializer):
    template_code = serializers.CharField(
        required=False, allow_blank=True,
        help_text=(
            "Purpose: Switch this customized invoice to another predefined template "
            "(e.g. classic -> simple). The saved colors and font carry over. "
            "Location: request body. "
            "Format/Type: string - a template code from GET templates/ (e.g. \"standard\", \"classic\", \"simple\"). "
            "Optional - omit to keep the current template."
        ),
    )
    template_id = serializers.IntegerField(
        required=False,
        help_text=(
            "Purpose: Alternative to template_code - the new template's numeric id. "
            "Location: request body. Format/Type: integer id from GET templates/. "
            "Optional (if both are sent, template_id is used)."
        ),
    )
    name = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False,
        help_text=NAME_HELP + "Optional - omit to keep the current name.",
    )
    configuration = serializers.JSONField(
        required=False,
        help_text=CONFIGURATION_HELP + " Optional - send only the values to change; every value "
        "left out keeps its saved setting.",
    )
