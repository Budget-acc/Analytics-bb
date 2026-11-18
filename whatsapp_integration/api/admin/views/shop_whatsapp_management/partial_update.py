from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from whatsapp_integration.models import ShopWhatsAppConfiguration
from whatsapp_integration.api.admin.serializers import (
    ShopWhatsAppConfigurationWriteSerializer,
    ShopWhatsAppConfigurationReadSerializer,
)
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Update a shop's WhatsApp configuration",
    description=(
        "Partially updates an existing WhatsApp configuration.\n\n"
        "Purpose: Lets an admin change any subset of a shop's WhatsApp connection "
        "details — e.g. rotate the access_token, correct the phone_number, or "
        "manually flip connection_status while troubleshooting — without needing "
        "to resend the full configuration.\n\n"
        "Context: Called from the admin dashboard's WhatsApp Configuration page, "
        "using the configuration id obtained from the Retrieve endpoint. `shop` "
        "itself cannot be changed via this endpoint — a configuration is permanently "
        "tied to the shop it was created for. This endpoint only updates the "
        "configuration itself — to edit a template, use the separate Template "
        "Partial Update endpoint (PATCH .../configurations/templates/<template_id>/)."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies which configuration to update. "
                "Location: URL path parameter. "
                "Format/Type: integer (configuration id, as returned by Retrieve/Create). "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="WhatsAppConfigurationPartialUpdateRequest",
        fields={
            "access_token": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Bearer token used to authenticate with the third-party WhatsApp "
                    "provider. Location: request body. Format/Type: string. Optional."
                ),
            ),
            "provider": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "business_account_id": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "phone_number_id": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "phone_number": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "app_id": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "app_secret": serializers.CharField(required=False, help_text="Location: request body. Format/Type: string. Optional."),
            "connection_status": serializers.CharField(
                required=False,
                help_text=(
                    "Location: request body. Format/Type: string — one of PENDING, ACTIVE, "
                    "INVALID_TOKEN, TOKEN_EXPIRED, PHONE_REMOVED, RATE_LIMITED, REVOKED, ERROR. Optional."
                ),
            ),
            "verified": serializers.BooleanField(required=False, help_text="Location: request body. Format/Type: boolean. Optional."),
            "token_expiry": serializers.DateTimeField(required=False, help_text="Location: request body. Format/Type: ISO-8601 datetime string. Optional."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success. Returns the updated configuration object (see Retrieve for shape)."),
        400: OpenApiResponse(description="Error messages:\n- Field-level validation errors returned under data.validation_errors (e.g. invalid connection_status choice)."),
        404: OpenApiResponse(description="Exact message: WhatsApp configuration not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
    examples=[
        OpenApiExample("Request example", value={"connection_status": "ACTIVE", "verified": True}, request_only=True),
        OpenApiExample(
            "Success response",
            value={
                "message": "WhatsApp configuration updated successfully.",
                "data": {
                    "id": 3,
                    "shop": 45,
                    "provider": "wayon",
                    "business_account_id": None,
                    "phone_number_id": None,
                    "phone_number": None,
                    "app_id": None,
                    "connection_status": "ACTIVE",
                    "verified": True,
                    "token_expiry": None,
                    "created_at": "2026-07-20T10:00:00Z",
                    "updated_at": "2026-07-20T10:05:00Z",
                    "templates": [],
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def partial_update(self, request, pk=None):
    """
    PATCH: Partially update a WhatsApp configuration by its id.

    Path params:
        - pk: WhatsApp configuration ID (required)

    Request body:
        - Any subset of: provider, business_account_id, phone_number_id, phone_number,
          access_token, app_id, app_secret, connection_status, verified, token_expiry.
          All optional. Templates are not managed here — see Template Partial Update.
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

            serializer = ShopWhatsAppConfigurationWriteSerializer(
                configuration, data=request.data, partial=True
            )
            if not serializer.is_valid():
                return self.validation_error_response(
                    errors=serializer.errors,
                    message="Please check the submitted details and try again.",
                    dev_message=f"Serializer validation failed: {serializer.errors}",
                )

            configuration = serializer.save()

        read_serializer = ShopWhatsAppConfigurationReadSerializer(configuration)
        return self.success_response(
            data=read_serializer.data,
            message="WhatsApp configuration updated successfully.",
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.partial_update")
        return self.error_response(
            message="Something went wrong while updating the WhatsApp configuration. Please try again.",
            dev_message=str(e),
        )
