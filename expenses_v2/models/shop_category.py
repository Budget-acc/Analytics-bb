from django.db import models
from django.db.models.functions import Lower


class ShopCategory(models.Model):
    TYPE_CHOICES = [
        ("income", "Income"),
        ("expense", "Expense"),
        ("income_and_expense", "Income & Expense"),
    ]

    shop = models.ForeignKey(
        "shop.Shop", on_delete=models.CASCADE, related_name="shop_categories"
    )
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="income_and_expense")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Case-insensitive uniqueness: "Billing" and "billing" are the same category.
            models.UniqueConstraint(
                Lower("name"),
                "shop",
                name="unique_shop_category_name_per_shop",
            )
        ]
        indexes = [
            models.Index(fields=["shop", "is_active"]),
        ]

    def __str__(self):
        return f"{self.shop} - {self.name}"
