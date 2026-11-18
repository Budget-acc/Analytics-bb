from rest_framework import serializers

from invoice_templates.models import DocumentType

CONFIGURATION_HELP = (
    "Purpose: The invoice customization - five colors and one font; everything else is fixed "
    "by the template design. "
    "Location: request body. "
    "Format/Type: JSON object {\"colors\": {\"background\", \"primary\", \"text\", \"border\", "
    "\"cancelled\"}, \"font\"}. Each color is a hex code \"#RRGGBB\"; font is one of the "
    "`fonts` values from GET templates/{code}/ (e.g. inter, noto_sans_malayalam, dejavu_sans, "
    "cormorant_garamond, roboto_mono). Every key is optional; unknown keys are rejected."
)


class PreviewRequestSerializer(serializers.Serializer):
    document_type = serializers.ChoiceField(
        choices=DocumentType.choices,
        help_text=(
            "Purpose: Which invoice layout the preview shows (booking, sale or custom work). "
            "Location: request body. "
            "Format/Type: string - one of booking, sale, tailor_order. "
            "Required."
        ),
    )
    configuration = serializers.JSONField(
        required=False,
        help_text=CONFIGURATION_HELP + " Optional. Template preview: if omitted, the template "
        "defaults are previewed. Customized invoice preview: values sent are previewed on top of "
        "its saved settings; if omitted, the saved settings are previewed.",
    )
