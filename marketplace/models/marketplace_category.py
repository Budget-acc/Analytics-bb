from django.db import models
from safedelete.models import SafeDeleteModel,SOFT_DELETE




class MarketplaceCategory(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name