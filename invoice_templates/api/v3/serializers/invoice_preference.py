from rest_framework import serializers

_TERMS_HELP = (
    "Purpose: Terms and Conditions printed on {doc} invoices. "
    "Location: request body. "
    "Format/Type: array of strings (at most 30 items, each 1-300 characters) or null. "
    "null or an empty array clears it, so the invoice uses the default terms for that "
    "document type. "
    "Optional - keys left out are not changed."
)


class SetDefaultInvoiceRequestSerializer(serializers.Serializer):
    custom_invoice_id = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "Purpose: The customized invoice to use for this shop's real invoices. "
            "Location: request body. "
            "Format/Type: integer - an id from GET custom-invoices/, or null to go back to the "
            "standard invoice with default settings. "
            "Required (the key must be present; its value may be null)."
        ),
    )


class ToggleCustomizationRequestSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(
        help_text=(
            "Purpose: Turns customized invoices on or off for this shop. When off, the existing "
            "invoice design is used; saved templates and settings are kept. "
            "Location: request body. "
            "Format/Type: boolean (true/false). "
            "Required."
        ),
    )


class UpdateTermsRequestSerializer(serializers.Serializer):
    booking = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True,
        help_text=_TERMS_HELP.format(doc="booking"),
    )
    sale = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True,
        help_text=_TERMS_HELP.format(doc="sale"),
    )
    tailor_order = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True,
        help_text=_TERMS_HELP.format(doc="custom work / tailor order"),
    )
