from django.db import models
from safedelete.models import SOFT_DELETE


class ClientV2(models.Model):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        "shop.Shop", on_delete=models.CASCADE, related_name="clients"
    )
    client_name = models.CharField(max_length=50)
    client_phone_1 = models.BigIntegerField()
    client_phone_2 = models.BigIntegerField(null=True, blank=True)
    client_address = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.client_name
