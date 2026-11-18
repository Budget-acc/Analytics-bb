from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .general_service import GeneralService


class ShopOwnerService(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        "shop.Shop", on_delete=models.CASCADE, related_name="services"
    )
    general_service = models.ForeignKey(GeneralService, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("shop", "general_service")
        indexes = [
            models.Index(fields=["shop"]),
            models.Index(fields=["general_service"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["shop", "display_order"]),
        ]

    def __str__(self):
        return f"{self.shop.name}({self.shop.id}) - {self.general_service.name}"
