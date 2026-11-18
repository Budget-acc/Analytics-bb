from django.db import models
from django.utils.timezone import now


class TailorOrderItemAssignment(models.Model):
    order_item = models.ForeignKey(
        'TailorOrderItem',
        on_delete=models.CASCADE,
        related_name='assignments'
    )
    tailor = models.ForeignKey(
        'Tailor',
        on_delete=models.PROTECT,
        related_name='assignments'
    )
    tailor_charge = models.DecimalField(max_digits=8, decimal_places=2)
    tailoring_charge_from_customer = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Amount collected from the customer for tailoring this item"
    )    
    expected_completion_date = models.DateTimeField(null=True, blank=True)
    actual_completion_date = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tailor_order_item_assignment'
        unique_together = ('order_item', 'tailor')
        indexes = [
            models.Index(fields=['order_item']),
            models.Index(fields=['tailor']),
        ]

    def __str__(self):
        return f"Assignment: {self.tailor} → Item #{self.order_item_id} (₹{self.tailor_charge})"
