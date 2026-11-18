from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from whatsapp_integration.models import ShopWhatsAppTemplate
from whatsapp_integration.api.admin.serializers import ShopWhatsAppTemplateSerializer, ShopWhatsAppTemplateWriteSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Update a WhatsApp template",
    description=(
        "Partially updates a single WhatsApp template.\n\n"
        "Purpose: Lets an admin edit a template's details — e.g. mark it "
        "APPROVED once the provider confirms it, correct its language/category, "
        "or record the provider-assigned meta_template_id — without touching "
        "the parent configuration or any other template.\n\n"
        "Context: Called from the admin dashboard's WhatsApp Configuration page, "
        "using a template id obtained from the Retrieve endpoint's nested "
        "templates array. This endpoint only updates the template itself — to "
        "edit the parent configuration, use the separate Configuration Partial "
        "Update endpoint (PATCH .../configurations/<configuration_id>/).\n\n"
        "Note on the URL: this endpoint shares the identical URL pattern "
        "admin/configurations/templates/<int:pk>/ with Create Template (POST) "
        "and Delete Template (DELETE). For this endpoint (PATCH) and for Delete "
        "Template (DELETE), pk is the **template id**; for Create Template "
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
                "Purpose: Identifies which template to update. For this PATCH endpoint, "
                "pk is the template id (contrast with POST on this same path, where pk is "
                "a configuration id). "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="WhatsAppTemplatePartialUpdateRequest",
        fields={
            "template_name": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: The canonical template name. If changed, must not collide with "
                    "another template already on the same configuration. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "meta_template_id": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "language": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "category": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "status": serializers.CharField(
                required=False,
                help_text=(
                    "Location: request body. Format/Type: string — one of PENDING, APPROVED, "
                    "REJECTED, PAUSED. Optional."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success. Returns the updated template object."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- A template named '<name>' already exists for this configuration.\n"
                "- Field-level validation errors returned under data.validation_errors."
            )
        ),
        404: OpenApiResponse(description="Exact message: WhatsApp template not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
    examples=[
        OpenApiExample("Request example", value={"status": "APPROVED"}, request_only=True),
        OpenApiExample(
            "Success response",
            value={
                "message": "WhatsApp template updated successfully.",
                "data": {
                    "id": 7,
                    "template_name": "sale_invoice_2",
                    "meta_template_id": None,
                    "language": "en",
                    "category": None,
                    "status": "APPROVED",
                    "last_synced": None,
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def update_template(self, request, pk=None):
    """
    PATCH: Partially update a WhatsApp template by its id.

    Path params:
        - pk: WhatsApp template ID (required)

    Request body:
        - Any subset of: template_name, meta_template_id, language, category, status.
          All optional.
    """
    try:
        with transaction.atomic():
            try:
                template = ShopWhatsAppTemplate.objects.select_for_update().get(id=pk)
            except ShopWhatsAppTemplate.DoesNotExist:
                return self.not_found_response(
                    resource="ShopWhatsAppTemplate",
                    message="WhatsApp template not found.",
                    dev_message=f"No ShopWhatsAppTemplate record exists with id={pk}.",
                )

            new_name = request.data.get("template_name") if isinstance(request.data, dict) else None
            if new_name and new_name != template.template_name:
                if ShopWhatsAppTemplate.objects.filter(
                    shop_whatsapp_configuration_id=template.shop_whatsapp_configuration_id,
                    template_name=new_name,
                ).exclude(id=template.id).exists():
                    return self.error_response(
                        message=f"A template named '{new_name}' already exists for this configuration.",
                        dev_message=(
                            f"Duplicate template_name '{new_name}' for "
                            f"shop_whatsapp_configuration_id={template.shop_whatsapp_configuration_id}."
                        ),
                    )

            serializer = ShopWhatsAppTemplateWriteSerializer(template, data=request.data, partial=True)
            if not serializer.is_valid():
                return self.validation_error_response(
                    errors=serializer.errors,
                    message="Please check the submitted details and try again.",
                    dev_message=f"Serializer validation failed: {serializer.errors}",
                )

            template = serializer.save()

        read_serializer = ShopWhatsAppTemplateSerializer(template)
        return self.success_response(
            data=read_serializer.data,
            message="WhatsApp template updated successfully.",
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.update_template")
        return self.error_response(
            message="Something went wrong while updating the WhatsApp template. Please try again.",
            dev_message=str(e),
        )
