from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from whatsapp_integration.models import ShopWhatsAppTemplate
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Delete a WhatsApp template",
    description=(
        "Permanently deletes a single WhatsApp template.\n\n"
        "Purpose: Lets an admin remove a template that's no longer used or was "
        "registered by mistake.\n\n"
        "Context: Called from the admin dashboard's WhatsApp Configuration page, "
        "using a template id obtained from the Retrieve endpoint's nested "
        "templates array.\n\n"
        "Note on the URL: this endpoint and Create Template share the identical "
        "URL pattern admin/configurations/templates/<int:pk>/ — for this "
        "endpoint (DELETE), pk is the **template id**; for Create Template "
        "(POST) on the same path, pk is the **configuration id**. They are "
        "distinguished by HTTP method, not by URL shape."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies which template to delete. For this DELETE endpoint, "
                "pk is the template id (contrast with POST on this same path, where pk is "
                "a configuration id). "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Success. Returns a deletion confirmation message."),
        404: OpenApiResponse(description="Exact message: WhatsApp template not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
)
def destroy_template(self, request, pk=None):
    """
    DELETE: Permanently delete a WhatsApp template by its id.

    Path params:
        - pk: WhatsApp template ID (required)
    """
    try:
        try:
            template = ShopWhatsAppTemplate.objects.get(id=pk)
        except ShopWhatsAppTemplate.DoesNotExist:
            return self.not_found_response(
                resource="ShopWhatsAppTemplate",
                message="WhatsApp template not found.",
                dev_message=f"No ShopWhatsAppTemplate record exists with id={pk}.",
            )

        configuration_id = template.shop_whatsapp_configuration_id
        template.delete()

        logger.info(
            "WhatsApp template deleted",
            extra={"template_id": pk, "configuration_id": configuration_id, "admin_id": request.user.id},
        )

        return self.success_response(
            data={"deleted_template_id": pk, "shop_whatsapp_configuration": configuration_id},
            message="WhatsApp template deleted successfully.",
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.destroy_template")
        return self.error_response(
            message="Something went wrong while deleting the WhatsApp template. Please try again.",
            dev_message=str(e),
        )
