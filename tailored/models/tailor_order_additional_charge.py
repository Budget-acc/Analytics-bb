from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from .tailor_order import TailorOrder


class TailorOrderAdditionalCharge(models.Model):

    order = models.ForeignKey(
        TailorOrder,
        on_delete=models.CASCADE,
        related_name="additional_charges",
        db_index=True,
        help_text="The tailoring order this charge belongs to"
    )
    name = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Description of the additional charge"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
        help_text="Charge amount applied to the tailoring order"
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "name"]),
            models.Index(fields=["order", "amount"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.amount:.2f} (Order #{self.order.id})"
