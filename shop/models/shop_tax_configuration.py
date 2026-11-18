from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


def default_taxable_components():
    # Most common real-world case: GST on Product Total + Additional
    # Charges, excluding Security Amount unless explicitly configured.
    return ["product_total", "additional_charges"]


class ShopTaxConfiguration(SafeDeleteModel):
    """
    Stores a shop's tax (e.g. GST) configuration: rate, whether tax is
    enabled, and which invoice components the tax applies to.

    This is a configuration model only — no calculation logic lives here.
    Absence of a row for a shop, or `is_enabled=False`, both mean "no tax"
    for any consumer of this model.
    """
    _safedelete_policy = SOFT_DELETE

    class TaxableComponent(models.TextChoices):
        PRODUCT_TOTAL = "product_total", "Product Total"
        ADDITIONAL_CHARGES = "additional_charges", "Additional Charges"
        SECURITY_AMOUNT = "security_amount", "Security Amount"
        ENTIRE_INVOICE = "entire_invoice", "Entire Invoice"

    TAX_CALCULATION_TYPE_CHOICES = (
        ("exclusive", "Exclusive"),
        ("inclusive", "Inclusive"),
    )

    shop = models.OneToOneField(
        "Shop", on_delete=models.CASCADE, related_name="tax_configuration"
    )
    tax_name = models.CharField(max_length=30, default="GST")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_enabled = models.BooleanField(default=False)
    taxable_components = models.JSONField(
        default=default_taxable_components,
        blank=True,
        help_text=(
            "List of TaxableComponent values this tax applies to, e.g. "
            "['product_total', 'additional_charges']. If the list contains "
            "'entire_invoice', tax is calculated on Product Total + "
            "Additional Charges + Security Amount combined, regardless of "
            "any other values also present in the list. Otherwise, tax is "
            "calculated only on the sum of the selected components — most "
            "commonly ['product_total', 'additional_charges'], explicitly "
            "excluding Security Amount unless it is selected too."
        ),
    )

    tax_calculation_type = models.CharField(
        max_length=15,
        choices=TAX_CALCULATION_TYPE_CHOICES,
        default="exclusive",
        help_text=(
            "Determines how tax is applied. "
            "'Exclusive' adds tax on top of the taxable amount, while "
            "'Inclusive' assumes the entered amount already includes tax and "
            "extracts the tax portion from it."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["shop", "is_enabled"]),
        ]

    def __str__(self):
        return f"{self.shop.name} - {self.tax_name} ({self.tax_rate}%)"
