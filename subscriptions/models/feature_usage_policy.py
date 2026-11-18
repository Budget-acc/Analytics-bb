from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .feature import Feature
from .subscription_plan import SubscriptionPlan


class FeatureUsagePolicy(SafeDeleteModel):
    """
    Default usage limits per subscription plan
    """
    _safedelete_policy = SOFT_DELETE

    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.CASCADE,
        related_name="feature_usage_policies"
    )
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        limit_choices_to={"usage_type": "COUNT"}
    )

    limit = models.PositiveIntegerField(
        help_text="Eg: 5000 messages"
    )

    reset_cycle = models.CharField(
        max_length=20,
        choices=[
            ("MONTHLY", "Monthly"),
            ("DAILY", "Daily"),
            ("LIFETIME", "Lifetime"),
        ],
        default="MONTHLY"
    )

    class Meta:
        unique_together = ("plan", "feature")

    def __str__(self):
        return f"{self.plan.name} - {self.feature.code} : {self.limit}"
