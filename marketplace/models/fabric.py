from safedelete.models import SafeDeleteModel,SOFT_DELETE
from django.db import models


class Fabric(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name