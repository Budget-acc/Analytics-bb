import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
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
    summary="Create a shop tax configuration",
    description=(
        "Creates the tax (e.g. GST) configuration for the active shop.\n\n"
        "Purpose: Lets a shop owner define a tax rate and which parts of an invoice "
        "(product total, additional charges, security amount, or the entire invoice) it "
        "applies to, so future invoices generated for this shop can calculate and display "
        "tax consistently.\n\n"
        "Context: Called once per shop, from the shop's tax settings screen, before tax can "
        "be enabled on invoices. A shop may have at most one tax configuration — attempting "
        "to create a second one fails; use partial update instead to change an existing "
        "configuration.\n\n"
        "Accepted inputs:\n"
        "Request body — Required:\n"
        "- tax_rate (decimal): the tax percentage.\n\n"
        "Request body — Optional:\n"
        "- tax_name (string): label for the tax, e.g. 'GST'. Defaults to 'GST'.\n"
        "- is_enabled (boolean): whether this tax is actively applied. Defaults to false.\n"
        "- taxable_components (array of strings): which invoice components the tax applies "
        "to. Defaults to ['product_total', 'additional_charges'].\n"
        "- tax_calculation_type (string): whether tax is added on top of the taxable amount "
        "('exclusive') or extracted from an amount that already includes tax ('inclusive'). "
        "Defaults to 'exclusive'."
    ),
    request=inline_serializer(
        name="ShopTaxConfigurationCreateRequest",
        fields={
            "tax_name": serializers.CharField(
                required=False, allow_blank=False,
                help_text=(
                    "Purpose: Human-readable label for this tax, shown on invoices "
                    "(e.g. 'GST'). "
                    "Location: request body. "
                    "Format/Type: string, up to 30 characters. "
                    "Optional. Default: 'GST'."
                ),
            ),
            "tax_rate": serializers.DecimalField(
                max_digits=5, decimal_places=2, required=True,
                help_text=(
                    "Purpose: The tax percentage to apply, e.g. 18 for 18%. "
                    "Location: request body. "
                    "Format/Type: decimal, up to 5 digits with 2 decimal places (e.g. 18.00). "
                    "Required. Must be zero or a positive value."
                ),
            ),
            "is_enabled": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Turns this tax configuration on or off. When false, no tax is "
                    "applied even though a configuration exists. "
                    "Location: request body. "
                    "Format/Type: boolean. "
                    "Optional. Default: false."
                ),
            ),
            "taxable_components": serializers.ListField(
                child=serializers.ChoiceField(
                    choices=[c.value for c in ShopTaxConfiguration.TaxableComponent]
                ),
                required=False,
                help_text=(
                    "Purpose: Selects which invoice components this tax applies to. If the "
                    "list contains 'entire_invoice', tax is calculated on Product Total + "
                    "Additional Charges + Security Amount combined, regardless of any other "
                    "values also present. Otherwise, tax is calculated only on the sum of the "
                    "selected components. "
                    "Location: request body. "
                    "Format/Type: array of strings — allowed values: 'product_total', "
                    "'additional_charges', 'security_amount', 'entire_invoice'. "
                    "Optional. Default: ['product_total', 'additional_charges']. Required to "
                    "be non-empty if is_enabled is true."
                ),
            ),
            "tax_calculation_type": serializers.ChoiceField(
                choices=ShopTaxConfiguration.TAX_CALCULATION_TYPE_CHOICES,
                required=False,
                help_text=(
                    "Purpose: Determines how tax is applied. 'exclusive' adds tax on top of "
                    "the taxable amount; 'inclusive' assumes the entered amount already "
                    "includes tax and extracts the tax portion from it. "
                    "Location: request body. "
                    "Format/Type: string — allowed values: 'exclusive', 'inclusive'. "
                    "Optional. Default: 'exclusive'."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Tax configuration created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Tax configuration already exists for this shop.\n"
                "- Must be zero or a positive value.\n"
                "- Invalid taxable component(s): <values>.\n"
                "- taxable_components must be non-empty when is_enabled is true.\n"
                "- \"<value>\" is not a valid choice. (invalid tax_calculation_type)\n"
                "- Please check your tax configuration details and try again."
            )
        ),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the tax configuration."),
    },
    examples=[
        OpenApiExample(
            "Create tax configuration",
            value={
                "tax_name": "GST",
                "tax_rate": 18,
                "is_enabled": True,
                "taxable_components": ["product_total", "additional_charges"],
                "tax_calculation_type": "exclusive",
            },
            request_only=True,
        ),
    ],
)
def create(self, request):
    try:
        # Look up including soft-deleted rows: the OneToOneField's DB-level
        # unique constraint on shop_id still applies to a soft-deleted row
        # (safedelete only sets a `deleted` timestamp, it doesn't free the
        # unique slot), so a plain `objects.filter(...).exists()` check would
        # miss a previously-deleted config and let this insert fail with a
        # raw IntegrityError instead of a clean, user-friendly response.
        existing = ShopTaxConfiguration.all_objects.filter(shop_id=self.shop_id).first()
        if existing is not None and existing.deleted is None:
            return self.validation_error_response(
                errors={'shop': ['Tax configuration already exists for this shop.']},
                message='Tax configuration already exists for this shop.',
            )

        data = request.data.copy()
        data['shop'] = self.shop_id

        if existing is not None:
            # A soft-deleted config already occupies this shop's unique slot -
            # restore and overwrite it instead of inserting a new row.
            existing.undelete()
            serializer = ShopTaxConfigurationWriteSerializer(existing, data=data)
        else:
            serializer = ShopTaxConfigurationWriteSerializer(data=data)

        if not serializer.is_valid():
            return self.validation_error_response(
                errors=serializer.errors,
                message=get_first_validation_error_message(
                    serializer.errors, 'Please check your tax configuration details and try again.'
                ),
            )

        config = serializer.save()

        return self.success_response(
            data=ShopTaxConfigurationReadSerializer(config).data,
            message="Tax configuration created successfully.",
        )

    except Exception as e:
        logger.error(f"ShopTaxConfiguration create error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the tax configuration.",
            dev_message=str(e),
        )
