from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status

from .helpers import _first_validation_error_message


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Create vendor",
    description=(
        "Registers a new vendor for the active shop.\n\n"
        "Purpose: Vendors are the source a shop buys stock/products from - creating one here "
        "makes it available for selection on future purchases.\n\n"
        "Context: Used from a standalone vendor management screen. Purchase Create "
        "(purchase/api/v3/views/purchase_viewset/create.py) also supports an inline vendor{} "
        "get-or-create-or-update shortcut so a purchase can be entered without visiting this "
        "endpoint first, but this endpoint remains the direct way to manage vendors."
    ),
    request=inline_serializer(
        name="VendorCreateV3Request",
        fields={
            "name": serializers.CharField(
                help_text=(
                    "Purpose: The vendor's display name. Location: request body. Format/Type: "
                    "string, non-empty (whitespace-only rejected). Required. Must be unique "
                    "(case-insensitive) among this shop's active vendors."
                ),
            ),
            "contact_number": serializers.CharField(
                required=False,
                help_text="Purpose: Vendor's phone number. Location: request body. Format/Type: string, India (region=IN) phone number. Optional.",
            ),
            "email": serializers.CharField(
                required=False,
                help_text="Purpose: Vendor's email address. Location: request body. Format/Type: string, valid email. Optional.",
            ),
            "location": serializers.CharField(
                required=False,
                help_text="Purpose: Vendor's address/location text. Location: request body. Format/Type: string, up to 255 characters. Optional.",
            ),
            "gst_number": serializers.CharField(
                required=False,
                help_text="Purpose: Vendor's GST registration number. Location: request body. Format/Type: string, up to 50 characters. Optional.",
            ),
            "notes": serializers.CharField(
                required=False,
                help_text="Purpose: Free-text internal notes about the vendor. Location: request body. Format/Type: string. Optional.",
            ),
            "is_active": serializers.BooleanField(
                required=False,
                help_text="Purpose: Whether the vendor is currently active/selectable. Location: request body. Format/Type: boolean. Optional - defaults to true.",
            ),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Vendor created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Vendor name cannot be empty.\n"
                "- A vendor with this name already exists."
            )
        ),
    },
    examples=[
        OpenApiExample(
            "Create vendor request",
            value={
                "name": "Rainbow Textiles",
                "contact_number": "+919876543210",
                "email": "vendor@example.com",
                "location": "Kollam, Kerala",
                "gst_number": "29ABCDE1234F1Z5",
            },
            request_only=True,
        ),
    ],
)
def create(self, request, *args, **kwargs):

    serializer = self.get_serializer(
        data=request.data,
    )

    if not serializer.is_valid():
        return self.validation_error_response(
            errors=serializer.errors,
            message=_first_validation_error_message(serializer.errors),
        )

    self.perform_create(serializer)

    return self.success_response(
        data=serializer.data,
        message="Vendor created successfully.",
        http_status=status.HTTP_201_CREATED,
    )


__all__ = ["create"]
