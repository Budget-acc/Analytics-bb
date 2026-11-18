from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from whatsapp_integration.models import ShopWhatsAppConfiguration
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Delete a shop's WhatsApp configuration",
    description=(
        "Permanently deletes a WhatsApp configuration, including its associated "
        "templates (cascade).\n\n"
        "Purpose: Lets an admin disconnect a shop's WhatsApp Business connection "
        "entirely, e.g. before reconnecting a different account or decommissioning "
        "the feature for that shop.\n\n"
        "Context: Called from the admin dashboard's WhatsApp Configuration page, "
        "using the configuration id obtained from the Retrieve endpoint."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies which configuration to delete. "
                "Location: URL path parameter. "
                "Format/Type: integer (configuration id, as returned by Retrieve/Create). "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Success. Returns a deletion confirmation message."),
        404: OpenApiResponse(description="Exact message: WhatsApp configuration not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "message": "WhatsApp configuration deleted successfully.",
                "data": {"deleted_configuration_id": 3, "shop_id": 45},
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def destroy(self, request, pk=None):
    """
    DELETE: Permanently delete a WhatsApp configuration by its id.

    Path params:
        - pk: WhatsApp configuration ID (required)
    """
    try:
        try:
            configuration = ShopWhatsAppConfiguration.objects.get(id=pk)
        except ShopWhatsAppConfiguration.DoesNotExist:
            return self.not_found_response(
                resource="ShopWhatsAppConfiguration",
                message="WhatsApp configuration not found.",
                dev_message=f"No ShopWhatsAppConfiguration record exists with id={pk}.",
            )

        shop_id = configuration.shop_id
        configuration.delete()

        logger.info(
            "WhatsApp configuration deleted",
            extra={"configuration_id": pk, "shop_id": shop_id, "admin_id": request.user.id},
        )

        return self.success_response(
            data={"deleted_configuration_id": pk, "shop_id": shop_id},
            message="WhatsApp configuration deleted successfully.",
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.destroy")
        return self.error_response(
            message="Something went wrong while deleting the WhatsApp configuration. Please try again.",
            dev_message=str(e),
        )
