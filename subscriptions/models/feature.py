from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class Feature(SafeDeleteModel):
    """Individual features that can be added as add-ons or part of bundles"""
    _safedelete_policy = SOFT_DELETE

    FEATURE_TYPE_CHOICES = [
        ("CORE", "Core Feature"),
        ("ADDON", "Add-on Feature"),
        ("PREMIUM", "Premium Feature"),
    ]

    USAGE_TYPE_CHOICES = [
        ("NONE", "No Usage Limit"),
        ("COUNT", "Count Based"),        # WhatsApp, SMS, Email
        ("STORAGE", "Storage Based"),    # Future
        ("TIME", "Time Based"),          # API minutes
    ]

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, help_text="Unique feature code")
    description = models.TextField(null=True, blank=True)
    feature_type = models.CharField(max_length=20, choices=FEATURE_TYPE_CHOICES)

    # Base pricing (default/suggested price)
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Base/suggested price for this feature"
    )

    # Feature permissions/capabilities
    permissions = models.JSONField(
        default=dict,
        help_text="JSON object defining what this feature allows"
    )

    # Dependencies
    requires_features = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        help_text="Features that must be active for this feature to work"
    )

    usage_type = models.CharField(
        max_length=20,
        choices=USAGE_TYPE_CHOICES,
        default="NONE",
        help_text="Defines how this feature is metered"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code}) - Base: ₹{self.base_price}"
