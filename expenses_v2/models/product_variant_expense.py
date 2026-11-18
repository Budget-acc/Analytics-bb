from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .expenses import Expenses


class ProductVariantExpense(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    PAYMENT_METHOD_CHOICES = [
        ("upi", "UPI"),
        ("cash", "Cash"),
    ]

    product_variant = models.ForeignKey(
        "services.ProductVariant", on_delete=models.CASCADE, related_name="expenses"
    )
    user = models.ForeignKey(
        "authentication.User", on_delete=models.SET_NULL, null=True, blank=True
    )
    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE)
    date = models.DateField()
    expense_date_time = models.DateTimeField(null=True, blank=True)
    expense = models.IntegerField()
    type = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    linked_expense = models.ForeignKey(
        Expenses, on_delete=models.CASCADE, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default="cash")

    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    category = models.ForeignKey(
        'expenses_v2.ShopCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='product_variant_expenses',
    )

    class Meta:
        indexes = [
            models.Index(fields=["shop"]),
            models.Index(fields=["date"]),
            models.Index(fields=["shop", "expense_date_time"]),
            models.Index(fields=["expense_date_time"]),            
        ]

    def __str__(self):
        return f"{self.product_variant} - {self.expense}"
