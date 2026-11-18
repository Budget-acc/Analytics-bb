from django.db import transaction, IntegrityError
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers, status
from shop.models import Shop
from whatsapp_integration.models import ShopWhatsAppConfiguration
from whatsapp_integration.api.admin.serializers import (
    ShopWhatsAppConfigurationWriteSerializer,
    ShopWhatsAppConfigurationReadSerializer,
)
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Create a shop's WhatsApp configuration",
    description=(
        "Creates the WhatsApp Business connection configuration for a specific shop.\n\n"
        "Purpose: Lets an admin register the credentials (access token and, optionally, "
        "Meta-specific identifiers) that will be used to send WhatsApp messages on "
        "behalf of this shop.\n\n"
        "Context: Called from the admin dashboard after an admin selects a shop that "
        "does not yet have a WhatsApp configuration. A shop can have at most one "
        "configuration — calling this again for a shop that already has one returns "
        "an error rather than replacing it; use Partial Update instead."
    ),
    parameters=[
        OpenApiParameter(
            name="shop_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies which shop the new configuration belongs to. "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="WhatsAppConfigurationCreateRequest",
        fields={
            "access_token": serializers.CharField(
                required=True,
                help_text=(
                    "Purpose: Bearer token used to authenticate with the third-party WhatsApp "
                    "provider when sending messages for this shop. "
                    "Location: request body. Format/Type: string. Required."
                ),
            ),
            "provider": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Which messaging provider this configuration talks to. "
                    "Location: request body. Format/Type: string (e.g. 'meta'). "
                    "Optional. Defaults to 'meta' if omitted."
                ),
            ),
            "business_account_id": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Meta WhatsApp Business Account (WABA) id, if applicable to the "
                    "connected provider. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "phone_number_id": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Meta's internal phone-number id, if applicable to the connected "
                    "provider. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "phone_number": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Human-readable display phone number, for showing in the dashboard. "
                    "Location: request body. Format/Type: string (e.g. +91XXXXXXXXXX). Optional."
                ),
            ),
            "app_id": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Meta App id this connection was established through, if applicable. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "app_secret": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Secret for the Meta App identified by app_id, if applicable. "
                    "Location: request body. Format/Type: string. Optional."
                ),
            ),
            "connection_status": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Current health of the connection. "
                    "Location: request body. Format/Type: string — one of PENDING, ACTIVE, "
                    "INVALID_TOKEN, TOKEN_EXPIRED, PHONE_REMOVED, RATE_LIMITED, REVOKED, ERROR. "
                    "Optional. Defaults to PENDING if omitted."
                ),
            ),
            "verified": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Whether a real outbound test message has successfully been sent "
                    "through this connection. "
                    "Location: request body. Format/Type: boolean. Optional. Defaults to false."
                ),
            ),
            "token_expiry": serializers.DateTimeField(
                required=False,
                help_text=(
                    "Purpose: When access_token expires, if it expires at all. "
                    "Location: request body. Format/Type: ISO-8601 datetime string. Optional."
                ),
            ),
        },
    ),
    responses={
        201: OpenApiResponse(description="Success. Returns the created configuration object (see Retrieve for shape)."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- access_token is required.\n"
                "- A WhatsApp configuration already exists for this shop.\n"
                "- Field-level validation errors returned under data.validation_errors (e.g. invalid connection_status choice)."
            )
        ),
        404: OpenApiResponse(description="Exact message: Shop not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
    examples=[
        OpenApiExample(
            "Request example",
            value={"access_token": "sk_live_50c6aa9bb5858a32_example", "provider": "wayon"},
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "message": "WhatsApp configuration created successfully.",
                "data": {
                    "id": 3,
                    "shop": 45,
                    "provider": "wayon",
                    "business_account_id": None,
                    "phone_number_id": None,
                    "phone_number": None,
                    "app_id": None,
                    "connection_status": "PENDING",
                    "verified": False,
                    "token_expiry": None,
                    "created_at": "2026-07-20T10:00:00Z",
                    "updated_at": "2026-07-20T10:00:00Z",
                    "templates": [],
                },
            },
            response_only=True,
            status_codes=["201"],
        ),
    ],
)
def create(self, request, shop_id=None):
    """
    POST: Create a shop's WhatsApp configuration.

    Path params:
        - shop_id: Shop ID (required)

    Request body:
        - access_token: required
        - provider, business_account_id, phone_number_id, phone_number, app_id,
          app_secret, connection_status, verified, token_expiry
    """
    try:
        try:
            shop = Shop.objects.get(id=shop_id)
        except Shop.DoesNotExist:
            return self.not_found_response(
                resource="Shop",
                message="Shop not found.",
                dev_message=f"No Shop record exists with id={shop_id} (or it is soft-deleted).",
            )

        if ShopWhatsAppConfiguration.objects.filter(shop=shop).exists():
            return self.error_response(
                message="This shop already has a WhatsApp configuration.",
                dev_message=(
                    f"Shop id={shop_id} already has an associated ShopWhatsAppConfiguration "
                    "(OneToOneField constraint). Use the Partial Update (PATCH) endpoint instead."
                ),
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ShopWhatsAppConfigurationWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return self.validation_error_response(
                errors=serializer.errors,
                message="Please check the submitted details and try again.",
                dev_message=f"Serializer validation failed: {serializer.errors}",
            )

        try:
            with transaction.atomic():
                configuration = serializer.save(shop=shop)
        except IntegrityError as e:
            # A concurrent request created the configuration between the
            # exists() check above and this save() — the OneToOneField's
            # unique constraint on `shop` catches it here instead of letting
            # it fall through to the generic 500 handler below.
            return self.error_response(
                message="This shop already has a WhatsApp configuration.",
                dev_message=f"IntegrityError on concurrent create for shop id={shop_id}: {e}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        read_serializer = ShopWhatsAppConfigurationReadSerializer(configuration)
        return self.success_response(
            data=read_serializer.data,
            message="WhatsApp configuration created successfully.",
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.create")
        return self.error_response(
            message="Something went wrong while creating the WhatsApp configuration. Please try again.",
            dev_message=str(e),
        )
