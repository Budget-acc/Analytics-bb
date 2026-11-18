import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from shop.models import ShopTaxConfiguration

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Delete the shop's tax configuration",
    description=(
        "Soft-deletes the active shop's tax configuration.\n\n"
        "Purpose: Lets a shop owner remove their tax configuration entirely (e.g. the shop "
        "no longer wants to track tax on invoices, or was set up in error).\n\n"
        "Context: Used from the shop's tax settings screen's delete action. After deletion, "
        "the shop behaves as if it never had a tax configuration (no tax applied to future "
        "invoices) until a new one is created.\n\n"
        "Accepted inputs:\n"
        "Path parameters — Required:\n"
        "- pk (integer): the tax configuration id to delete."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True,
            description=(
                "Purpose: Identifies which tax configuration record to delete. "
                "Location: URL path parameter. "
                "Format/Type: integer (ShopTaxConfiguration id). "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Tax configuration deleted successfully."),
        404: OpenApiResponse(description="Exact message: Tax configuration not found."),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the tax configuration."),
    },
)
def destroy(self, request, pk=None):
    try:
        try:
            config = ShopTaxConfiguration.objects.get(pk=pk, shop_id=self.shop_id)
        except ShopTaxConfiguration.DoesNotExist:
            return self.not_found_response(
                resource="ShopTaxConfiguration",
                message="Tax configuration not found.",
                dev_message=f"ShopTaxConfiguration with id {pk} not found for shop {self.shop_id}.",
            )

        with transaction.atomic():
            config.delete()

        return self.success_response(
            data=None,
            message="Tax configuration deleted successfully.",
        )

    except Exception as e:
        logger.error(f"ShopTaxConfiguration destroy error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the tax configuration.",
            dev_message=str(e),
        )
