from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE

class DailyBalance(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE)
    account = models.ForeignKey('Account', on_delete=models.CASCADE)

    date = models.DateField()

    opening_balance = models.DecimalField(max_digits=12, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2)

    total_in = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_out = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['shop', 'account', 'date'],
                condition=models.Q(deleted__isnull=True),
                name='unique_daily_balance_per_account_date'
            )
        ]
        indexes = [
            models.Index(fields=['shop', 'date']),
        ]