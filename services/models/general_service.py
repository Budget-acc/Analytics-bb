from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .main_service_category import MainServiceCategory


class GeneralService(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    main_category = models.ForeignKey(
        MainServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subcategories",
    )
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                Lower("name"),
                name="unique_generalservice_name_ci"
            )
        ]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name
