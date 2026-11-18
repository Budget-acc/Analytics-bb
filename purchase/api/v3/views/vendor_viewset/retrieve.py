from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

@extend_schema(
    tags=["Purchase Management V3"],
    summary="Retrieve vendor",
    description=(
        "Returns a single vendor's full details.\n\n"
        "Purpose: Lets the UI display/edit one vendor's information.\n\n"
        "Context: Used when opening a vendor's detail/edit screen, or before pre-filling a "
        "purchase form with a known vendor's contact details."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purpose: Identifies the vendor to retrieve. Location: URL path. Format/Type: integer. Required."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Vendor retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Not found. (vendor does not exist, belongs to another shop, or is soft-deleted)"),
    },
)
@skip_subscription_validation
def retrieve(self, request, *args, **kwargs):

    vendor, error = self._get_vendor_or_404(kwargs.get("pk"))
    if error:
        return error

    serializer = self.get_serializer(vendor)

    return self.success_response(
        data=serializer.data,
        message="Vendor retrieved successfully.",
    )


__all__ = ["retrieve"]
