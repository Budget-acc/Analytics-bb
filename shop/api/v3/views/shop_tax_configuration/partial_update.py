import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from shop.api.v3.serializers.shop_tax_configuration import (
    ShopTaxConfigurationReadSerializer,
    ShopTaxConfigurationWriteSerializer,
)
from shop.models import ShopTaxConfiguration

from .helpers import get_first_validation_error_message

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Partially update the shop's tax configuration",
    description=(
        "Updates one or more fields of the active shop's existing tax configuration.\n\n"
        "Purpose: Lets a shop owner adjust the tax rate, enable/disable tax, or change "
        "which invoice components the tax applies to, without recreating the configuration.\n\n"
        "Context: Used from the shop's tax settings screen's edit action. Any field omitted "
        "from the request body keeps its current value. The is_enabled + taxable_components "
        "cross-field rule (taxable_components must be non-empty when is_enabled is true) is "
        "checked against the resulting merged state, not just the fields sent in this request.\n\n"
        "Accepted inputs:\n"
        "Path parameters — Required:\n"
        "- pk (integer): the tax configuration id to update.\n\n"
        "Request body — all optional (omitted fields keep their current value):\n"
        "- tax_name (string): updated tax label.\n"
        "- tax_rate (decimal): updated tax percentage.\n"
        "- is_enabled (boolean): updated enabled state.\n"
        "- taxable_components (array of strings): updated list of taxable components.\n"
        "- tax_calculation_type (string): updated tax calculation type — 'exclusive' or "
        "'inclusive'."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True,
            description=(
                "Purpose: Identifies which tax configuration record to update. "
                "Location: URL path parameter. "
                "Format/Type: integer (ShopTaxConfiguration id). "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="ShopTaxConfigurationPartialUpdateRequest",
        fields={
            "tax_name": serializers.CharField(
                required=False, allow_blank=False,
                help_text=(
                    "Purpose: Updated human-readable label for this tax. "
                    "Location: request body. "
                    "Format/Type: string, up to 30 characters. "
                    "Optional — omit to keep the existing value."
                ),
            ),
            "tax_rate": serializers.DecimalField(
                max_digits=5, decimal_places=2, required=False,
                help_text=(
                    "Purpose: Updated tax percentage. "
                    "Location: request body. "
                    "Format/Type: decimal, up to 5 digits with 2 decimal places. "
                    "Optional — omit to keep the existing value. Must be zero or a positive value."
                ),
            ),
            "is_enabled": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Updated enabled state for this tax. "
                    "Location: request body. "
                    "Format/Type: boolean. "
                    "Optional — omit to keep the existing value."
                ),
            ),
            "taxable_components": serializers.ListField(
                child=serializers.ChoiceField(
                    choices=[c.value for c in ShopTaxConfiguration.TaxableComponent]
                ),
                required=False,
                help_text=(
                    "Purpose: Updated list of invoice components this tax applies to. "
                    "'entire_invoice' takes priority over any other values also present. "
                    "Location: request body. "
                    "Format/Type: array of strings — allowed values: 'product_total', "
                    "'additional_charges', 'security_amount', 'entire_invoice'. "
                    "Optional — omit to keep the existing value. Must be non-empty if the "
                    "resulting is_enabled state is true."
                ),
            ),
            "tax_calculation_type": serializers.ChoiceField(
                choices=ShopTaxConfiguration.TAX_CALCULATION_TYPE_CHOICES,
                required=False,
                help_text=(
                    "Purpose: Updated tax calculation type. 'exclusive' adds tax on top of "
                    "the taxable amount; 'inclusive' assumes the entered amount already "
                    "includes tax and extracts the tax portion from it. "
                    "Location: request body. "
                    "Format/Type: string — allowed values: 'exclusive', 'inclusive'. "
                    "Optional — omit to keep the existing value."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Tax configuration updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Must be zero or a positive value.\n"
                "- Invalid taxable component(s): <values>.\n"
                "- taxable_components must be non-empty when is_enabled is true.\n"
                "- \"<value>\" is not a valid choice. (invalid tax_calculation_type)\n"
                "- Please check your tax configuration details and try again."
            )
        ),
        404: OpenApiResponse(description="Exact message: Tax configuration not found."),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating the tax configuration."),
    },
)
def partial_update(self, request, pk=None):
    try:
        try:
            config = ShopTaxConfiguration.objects.select_related('shop').get(pk=pk, shop_id=self.shop_id)
        except ShopTaxConfiguration.DoesNotExist:
            return self.not_found_response(
                resource="ShopTaxConfiguration",
                message="Tax configuration not found.",
                dev_message=f"ShopTaxConfiguration with id {pk} not found for shop {self.shop_id}.",
            )

        serializer = ShopTaxConfigurationWriteSerializer(config, data=request.data, partial=True)
        if not serializer.is_valid():
            return self.validation_error_response(
                errors=serializer.errors,
                message=get_first_validation_error_message(
                    serializer.errors, 'Please check your tax configuration details and try again.'
                ),
            )

        updated_config = serializer.save()

        return self.success_response(
            data=ShopTaxConfigurationReadSerializer(updated_config).data,
            message="Tax configuration updated successfully.",
        )

    except Exception as e:
        logger.error(f"ShopTaxConfiguration partial_update error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating the tax configuration.",
            dev_message=str(e),
        )
