from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .feature import Feature


class SubscriptionPlan(SafeDeleteModel):
    """Base subscription plans (Basic, Premium, Enterprise, etc.)"""
    _safedelete_policy = SOFT_DELETE

    PLAN_TYPE_CHOICES = [
        ("BASIC", "Basic"),
        ("PREMIUM", "Premium"),
        ("ENTERPRISE", "Enterprise"),
    ]

    name = models.CharField(max_length=100)
    plan_type = models.CharField(max_length=20, choices=PLAN_TYPE_CHOICES)
    description = models.TextField(null=True, blank=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)

    # Plan limits
    max_users = models.PositiveIntegerField(default=1, help_text="Maximum users allowed")
    max_products = models.PositiveIntegerField(default=100, help_text="Maximum products allowed")
    max_bookings_per_month = models.PositiveIntegerField(default=50, help_text="Monthly booking limit")

    # Plan duration
    duration_days = models.PositiveIntegerField(default=30, help_text="Plan validity in days")

    # Features included in base plan
    included_features = models.ManyToManyField(Feature, blank=True, related_name='base_plans')

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - ₹{self.base_price}/{self.duration_days}days"
