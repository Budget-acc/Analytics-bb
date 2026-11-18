from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status

from purchase.models.purchase import Purchase


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Delete vendor",
    description=(
        "Soft-deletes a vendor.\n\n"
        "Purpose: Removes a vendor from the active directory once it's no longer used.\n\n"
        "Context: A vendor referenced by any existing (non-deleted) purchase cannot be deleted - "
        "remove/reassign those purchases first. Used from a vendor management screen."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purpose: Identifies the vendor to delete. Location: URL path. Format/Type: integer. Required."),
    ],
    responses={
        204: OpenApiResponse(description="Exact message: Vendor deleted successfully."),
        400: OpenApiResponse(description="Exact message: Vendor has existing purchases and cannot be deleted."),
        404: OpenApiResponse(description="Exact message: Not found. (vendor does not exist, belongs to another shop, or is soft-deleted)"),
    },
)
def destroy(self, request, *args, **kwargs):

    vendor, error = self._get_vendor_or_404(kwargs.get("pk"))
    if error:
        return error

    purchase_exists = Purchase.objects.filter(
        vendor=vendor,
        deleted__isnull=True,
    ).exists()

    if purchase_exists:
        return self.error_response(
            message="Vendor has existing purchases and cannot be deleted.",
            http_status=status.HTTP_400_BAD_REQUEST,
        )

    vendor.delete()

    return self.success_response(
        message="Vendor deleted successfully.",
        http_status=status.HTTP_204_NO_CONTENT,
    )


__all__ = ["destroy"]
