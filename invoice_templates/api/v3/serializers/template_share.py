from rest_framework import serializers


class ImportShareRequestSerializer(serializers.Serializer):
    share_code = serializers.CharField(
        help_text=(
            "Purpose: The share code received from another shop (typed or scanned from its QR). "
            "Location: request body. "
            "Format/Type: string - 12 characters from 23456789ABCDEFGHJKMNPQRSTUVWXYZ; "
            "case-insensitive; hyphens and spaces are ignored (e.g. \"K7QM-2XRP-9HTD\"). "
            "Required."
        ),
    )
    name = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False,
        help_text=(
            "Purpose: Name for the new customized invoice created by the import (only for "
            "identifying it in the app; not printed). "
            "Location: request body. "
            "Format/Type: string, 1-60 characters without < > { } or line breaks, unique within "
            "the shop (case-insensitive). "
            "Optional. Default: the shared invoice's name, made unique."
        ),
    )
    set_as_default = serializers.BooleanField(
        required=False, default=False,
        help_text=(
            "Purpose: Also make the newly created customized invoice this shop's default "
            "(and switch customized invoices on). "
            "Location: request body. "
            "Format/Type: boolean. "
            "Optional. Default: false - the current default is not changed."
        ),
    )
