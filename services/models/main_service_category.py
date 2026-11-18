from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class MainServiceCategory(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                Lower("name"),
                name="unique_mainservicecategory_name_ci"
            )
        ]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name
