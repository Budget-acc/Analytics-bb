import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema

from shop.api.v3.serializers.shop_tax_configuration import ShopTaxConfigurationReadSerializer
from shop.models import ShopTaxConfiguration

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="List the shop's tax configuration",
    description=(
        "Returns the active shop's tax configuration, if one has been created.\n\n"
        "Purpose: Lets the shop's tax settings screen display the currently configured "
        "tax rate, enabled state, taxable components, and tax calculation type.\n\n"
        "Context: A shop has at most one tax configuration, so this returns a list with "
        "either exactly one item, or an empty list if the shop has not created a tax "
        "configuration yet — an empty list is expected/normal and means no tax applies, "
        "not an error."
    ),
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a list containing zero or one tax configuration.\n\n"
                "Response shape:\n"
                "- data (array): 0 or 1 item(s), each containing id, shop_id, shop_name, "
                "tax_name, tax_rate, is_enabled, taxable_components, tax_calculation_type, "
                "created_at, updated_at."
            )
        ),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the tax configuration."),
    },
)
def list(self, request):
    try:
        qs = ShopTaxConfiguration.objects.filter(shop_id=self.shop_id).select_related('shop')
        serializer = ShopTaxConfigurationReadSerializer(qs, many=True)
        return self.success_response(
            data=serializer.data,
            message="Tax configuration fetched successfully.",
        )

    except Exception as e:
        logger.error(f"ShopTaxConfiguration list error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the tax configuration.",
            dev_message=str(e),
        )
