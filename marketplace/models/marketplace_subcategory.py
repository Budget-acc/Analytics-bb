from safedelete.models import SafeDeleteModel,SOFT_DELETE
from django.db import models
from .marketplace_category import MarketplaceCategory

class MarketplaceSubCategory(SafeDeleteModel):

    _safedelete_policy = SOFT_DELETE

    category = models.ForeignKey(MarketplaceCategory,on_delete=models.CASCADE,related_name="subcategories",)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"],
                name="unique_category_subcategory_name",
            )
        ]
        ordering = ["id"]