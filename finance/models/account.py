from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class Account(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    ACCOUNT_TYPE_CHOICES = [
        ('cash', 'Cash'),
        ('bank', 'Bank'),
    ]

    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE, related_name='accounts')

    name = models.CharField(max_length=255)  # "Cash in Hand", "HDFC UPI", etc.

    account_type = models.CharField(
        max_length=10,
        choices=ACCOUNT_TYPE_CHOICES
    )

    description = models.TextField(null=True, blank=True)
    account_number = models.CharField(max_length=50, null=True, blank=True)

    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['shop', 'name'],
                condition=models.Q(deleted__isnull=True),
                name='unique_account_name_per_shop'
            )
        ]
        indexes = [
            models.Index(fields=['shop', 'account_type']),
        ]

    def __str__(self):
        return f"{self.name} ({self.account_type})"