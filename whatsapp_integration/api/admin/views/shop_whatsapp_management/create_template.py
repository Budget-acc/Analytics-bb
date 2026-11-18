from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers, status
from whatsapp_integration.models import ShopWhatsAppConfiguration
from whatsapp_integration.api.admin.serializers import ShopWhatsAppTemplateSerializer
from ._template_helpers import validate_template_entries, save_prepared_templates
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Create one or more WhatsApp templates for a configuration",
    description=(
        "Creates one or more WhatsApp templates against an existing, verified "
        "WhatsApp configuration.\n\n"
        "Purpose: Lets an admin register the message templates (e.g. "
        "'sale_invoice_2', 'pickup_reminder_2') that will be used when sending "
        "WhatsApp messages for this shop.\n\n"
        "Context: Called from the admin dashboard's WhatsApp Configuration page "
        "after the configuration has been created and verified "
        "(ShopWhatsAppConfiguration.verified == true) — templates cannot be "
        "added to an unverified configuration. Accepts either a single template "
        "object or a JSON array of template objects in one call, so several "
        "templates can be added at once.\n\n"
        "Note on the URL: this endpoint and Delete Template share the identical "
        "URL pattern admin/configurations/templates/<int:pk>/ — for this "
        "endpoint (POST), pk is the **configuration id**; for Delete Template "
        "(DELETE) on the same path, pk is the **template id**. They are "
        "distinguished by HTTP method, not by URL shape."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies which configuration the new template(s) belong to. "
                "For this POST endpoint, pk is the configuration id (contrast with DELETE "
                "on this same path, where pk is a template id). "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="WhatsAppTemplateCreateRequest",
        fields={
            "template_name": serializers.CharField(
                required=True,
                help_text=(
                    "Purpose: The canonical template name as registered with the WhatsApp "
                    "provider, e.g. 'sale_invoice_2'. "
                    "Location: request body. Format/Type: string. Required."
                ),
            ),
            "meta_template_id": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Provider-assigned template id, once known. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "language": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Template language code. "
                    "Location: request body. Format/Type: string (e.g. 'en'). "
                    "Optional. Defaults to 'en' if omitted."
                ),
            ),
            "category": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Template category (e.g. UTILITY, MARKETING), if applicable. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "status": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Approval status of this template. "
                    "Location: request body. Format/Type: string — one of PENDING, APPROVED, "
                    "REJECTED, PAUSED. Optional. Defaults to PENDING if omitted."
                ),
            ),
        },
    ),
    responses={
        201: OpenApiResponse(
            description=(
                "Success. Returns the created template object if a single object was "
                "submitted, or an array of created template objects if a list was submitted."
            )
        ),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Verify the WhatsApp configuration before adding templates.\n"
                "- A template named '<name>' already exists for this configuration.\n"
                "- Duplicate template_name '<name>' in this request.\n"
                "- Field-level validation errors returned under data.validation_errors."
            )
        ),
        404: OpenApiResponse(description="Exact message: WhatsApp configuration not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
    examples=[
        OpenApiExample("Single template request", value={"template_name": "sale_invoice_2"}, request_only=True),
        OpenApiExample(
            "Multiple templates request",
            value=[{"template_name": "sale_invoice_2"}, {"template_name": "rental_invoice_5"}],
            request_only=True,
        ),
        OpenApiExample(
            "Success response (single)",
            value={
                "message": "WhatsApp template(s) created successfully.",
                "data": {
                    "id": 9,
                    "template_name": "sale_invoice_2",
                    "meta_template_id": None,
                    "language": "en",
                    "category": None,
                    "status": "PENDING",
                },
            },
            response_only=True,
            status_codes=["201"],
        ),
    ],
)
def create_template(self, request, pk=None):
    """
    POST: Create one or more WhatsApp templates for a configuration.

    Path params:
        - pk: WhatsApp configuration ID (required)

    Request body:
        - A single template object, or a list of template objects.
        - template_name: required per entry.
        - meta_template_id, language, category, status: optional per entry.
    """
    try:
        with transaction.atomic():
            try:
                configuration = ShopWhatsAppConfiguration.objects.select_for_update().get(id=pk)
            except ShopWhatsAppConfiguration.DoesNotExist:
                return self.not_found_response(
                    resource="ShopWhatsAppConfiguration",
                    message="WhatsApp configuration not found.",
                    dev_message=f"No ShopWhatsAppConfiguration record exists with id={pk}.",
                )

            was_list = isinstance(request.data, list)
            entries = request.data if was_list else [request.data]

            has_error, errors, prepared = validate_template_entries(
                configuration, entries, allow_update=False
            )
            if has_error:
                return self.validation_error_response(
                    errors=errors,
                    message="Please check the submitted template(s) and try again.",
                    dev_message=f"Template validation failed: {errors}",
                )

            saved = save_prepared_templates(configuration, prepared)

        serializer = ShopWhatsAppTemplateSerializer(saved, many=True)
        response_data = serializer.data if was_list else serializer.data[0]

        return self.success_response(
            data=response_data,
            message="WhatsApp template(s) created successfully.",
            http_status=status.HTTP_201_CREATED,
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.create_template")
        return self.error_response(
            message="Something went wrong while creating the WhatsApp template(s). Please try again.",
            dev_message=str(e),
        )
