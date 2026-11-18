from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Organization


@extend_schema(
    tags=["Organization Admin Management V1"],
    summary="Delete organization",
    description=(
        "Permanently deletes an organization.\n\n"
        "Purpose: Removes an organization record from the system.\n\n"
        "Context: This is a hard delete. All shops that were linked to this organization "
        "will have their organization field automatically set to null (the FK uses "
        "SET_NULL on_delete). The shops themselves are not deleted — only the "
        "organization link is removed. This is a non-reversible operation.\n\n"
        "Authentication: JWT — admin users only.\n\n"
        "Path parameters — Required:\n"
        "- id (integer): The database ID of the organization to delete."
    ),
    parameters=[
        OpenApiParameter(
            name="id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the organization to delete. "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Organization deleted successfully."),
        404: OpenApiResponse(description="Exact message: Organization not found."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={"status": "success", "message": "Organization deleted successfully."},
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Not found response",
            value={"status": "error", "message": "Organization not found."},
            response_only=True,
            status_codes=["404"],
        ),
    ],
)
def destroy(self, request, pk=None):
    try:
        org = Organization.objects.get(pk=pk)
        org.delete()
        return self.success_response(
            message="Organization deleted successfully.",
        )
    except Organization.DoesNotExist:
        return self.not_found_response(resource="Organization")
