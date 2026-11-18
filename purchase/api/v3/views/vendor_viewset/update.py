from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from .helpers import _first_validation_error_message


_UPDATE_REQUEST_FIELDS = {
    "name": serializers.CharField(
        required=False,
        help_text=(
            "Purpose: The vendor's display name. Location: request body. Format/Type: string, "
            "non-empty if provided. Optional on PATCH (partial_update), required on PUT (update) "
            "since the underlying serializer treats it as a required field unless partial=True. "
            "Must remain unique (case-insensitive) among this shop's active vendors."
        ),
    ),
    "contact_number": serializers.CharField(required=False, help_text="Purpose: Vendor's phone number. Location: request body. Format/Type: string, India (region=IN) phone number. Optional."),
    "email": serializers.CharField(required=False, help_text="Purpose: Vendor's email address. Location: request body. Format/Type: string, valid email. Optional."),
    "location": serializers.CharField(required=False, help_text="Purpose: Vendor's address/location text. Location: request body. Format/Type: string, up to 255 characters. Optional."),
    "gst_number": serializers.CharField(required=False, help_text="Purpose: Vendor's GST registration number. Location: request body. Format/Type: string, up to 50 characters. Optional."),
    "notes": serializers.CharField(required=False, help_text="Purpose: Free-text internal notes about the vendor. Location: request body. Format/Type: string. Optional."),
    "is_active": serializers.BooleanField(required=False, help_text="Purpose: Whether the vendor is currently active/selectable. Location: request body. Format/Type: boolean. Optional."),
}


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Update vendor (full)",
    description=(
        "Replaces a vendor's editable fields.\n\n"
        "Purpose: Corrects/updates a vendor's stored details (e.g. a changed phone number).\n\n"
        "Context: Used from a vendor edit screen. PUT semantics - fields omitted from the "
        "request body are treated as required by the underlying serializer unless the request "
        "goes through partial_update (PATCH) instead."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purpose: Identifies the vendor to update. Location: URL path. Format/Type: integer. Required."),
    ],
    request=inline_serializer(name="VendorUpdateV3Request", fields=_UPDATE_REQUEST_FIELDS),
    responses={
        200: OpenApiResponse(description="Exact message: Vendor updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Vendor name cannot be empty.\n"
                "- A vendor with this name already exists."
            )
        ),
        404: OpenApiResponse(description="Exact message: Not found. (vendor does not exist, belongs to another shop, or is soft-deleted)"),
    },
)
def update(self, request, *args, **kwargs):

    partial = kwargs.pop("partial", False)

    vendor, error = self._get_vendor_or_404(kwargs.get("pk"))
    if error:
        return error

    serializer = self.get_serializer(
        vendor,
        data=request.data,
        partial=partial,
    )

    if not serializer.is_valid():
        return self.validation_error_response(
            errors=serializer.errors,
            message=_first_validation_error_message(serializer.errors),
        )

    self.perform_update(serializer)

    return self.success_response(
        data=serializer.data,
        message="Vendor updated successfully.",
    )


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Update vendor (partial)",
    description=(
        "Partially updates a vendor's editable fields - only the fields present in the request "
        "body are changed.\n\n"
        "Purpose: Corrects/updates specific vendor details without needing to resend the whole "
        "record.\n\n"
        "Context: Used from a vendor edit screen for single-field edits (e.g. just toggling "
        "is_active)."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purpose: Identifies the vendor to update. Location: URL path. Format/Type: integer. Required."),
    ],
    request=inline_serializer(name="VendorPartialUpdateV3Request", fields=_UPDATE_REQUEST_FIELDS),
    responses={
        200: OpenApiResponse(description="Exact message: Vendor updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Vendor name cannot be empty.\n"
                "- A vendor with this name already exists."
            )
        ),
        404: OpenApiResponse(description="Exact message: Not found. (vendor does not exist, belongs to another shop, or is soft-deleted)"),
    },
)
def partial_update(self, request, *args, **kwargs):
    kwargs["partial"] = True
    return self.update(request, *args, **kwargs)


__all__ = ["update", "partial_update"]
