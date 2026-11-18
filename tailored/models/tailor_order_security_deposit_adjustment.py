from django.db import models
from django.utils import timezone
from .tailor_order import TailorOrder


class TailorOrderSecurityDepositAdjustment(models.Model):

    ADJUSTMENT_TYPE_CHOICES = [
        ("refunded", "Refunded"),
        ("deducted", "Deducted"),
    ]

    order = models.ForeignKey(
        TailorOrder,
        on_delete=models.CASCADE,
        related_name="security_adjustments",
        db_index=True,
    )
    adjustment_type = models.CharField(
        max_length=20,
        choices=ADJUSTMENT_TYPE_CHOICES,
        db_index=True,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=[("cash", "Cash"), ("upi", "UPI")],
        null=True,
        blank=True,
        db_index=True,
    )
    account = models.ForeignKey(
        "finance.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_migration_generated = models.BooleanField(default=False)
    recorded_at = models.DateTimeField(auto_now_add=True)    

    class Meta:
        indexes = [
            models.Index(fields=["order", "adjustment_type"]),
            models.Index(fields=["order", "created_at"]),
        ]

    def __str__(self):
        return (
            f"{self.get_adjustment_type_display()} ₹{self.amount} "
            f"- TailorOrder #{self.order_id}"
        )
