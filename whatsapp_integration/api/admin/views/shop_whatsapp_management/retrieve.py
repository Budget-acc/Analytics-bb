from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop
from whatsapp_integration.models import ShopWhatsAppConfiguration
from whatsapp_integration.api.admin.serializers import ShopWhatsAppConfigurationReadSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["WhatsApp Configuration Admin Management"],
    summary="Retrieve a shop's WhatsApp configuration",
    description=(
        "Returns the WhatsApp Business connection configuration for a specific shop, "
        "together with the templates registered against that configuration.\n\n"
        "Purpose: Lets an admin view how a shop's WhatsApp messaging is currently "
        "configured (provider, connection health, associated templates) before "
        "editing or removing it.\n\n"
        "Context: Called from the admin dashboard immediately after an admin selects "
        "a shop and opens its WhatsApp Configuration page. The frontend uses this "
        "endpoint to discover the configuration's own id, which is then used for "
        "subsequent Partial Update / Delete calls."
    ),
    parameters=[
        OpenApiParameter(
            name="shop_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies which shop's WhatsApp configuration to fetch. "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns the configuration object: id, shop, provider, "
                "business_account_id, phone_number_id, phone_number, app_id, "
                "connection_status, verified, token_expiry, created_at, "
                "updated_at, and templates (array of registered templates). "
                "access_token and app_secret are never included."
            )
        ),
        404: OpenApiResponse(
            description=(
                "Exact messages:\n"
                "- Shop not found.\n"
                "- WhatsApp configuration not found for this shop."
            )
        ),
        500: OpenApiResponse(description="Exact message: An error occurred while processing your request."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "message": "WhatsApp configuration retrieved successfully.",
                "data": {
                    "id": 3,
                    "shop": 45,
                    "provider": "meta",
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
            status_codes=["200"],
        ),
    ],
)
def retrieve_by_shop(self, request, shop_id=None):
    """
    GET: Retrieve a shop's WhatsApp configuration by shop id, including its
    registered templates.

    Path params:
        - shop_id: Shop ID (required)
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

        try:
            configuration = (
                ShopWhatsAppConfiguration.objects
                .prefetch_related("templates")
                .get(shop=shop)
            )
        except ShopWhatsAppConfiguration.DoesNotExist:
            return self.not_found_response(
                resource="ShopWhatsAppConfiguration",
                message="WhatsApp configuration not found for this shop.",
                dev_message=(
                    f"Shop id={shop_id} has no related ShopWhatsAppConfiguration row yet. "
                    "Call the Create endpoint first."
                ),
            )

        serializer = ShopWhatsAppConfigurationReadSerializer(configuration)
        return self.success_response(
            data=serializer.data,
            message="WhatsApp configuration retrieved successfully.",
        )

    except Exception as e:
        logger.exception("Error in ShopWhatsAppManagementViewSet.retrieve_by_shop")
        return self.error_response(
            message="Something went wrong while fetching the WhatsApp configuration. Please try again.",
            dev_message=str(e),
        )
