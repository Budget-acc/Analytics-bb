from django.db import models
from django.db.models.functions import TruncDate
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class Investment(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('reversed', 'Reversed'),
    ]

    INVESTMENT_TYPE_CHOICES = [
        ('capital_contribution', 'Capital Contribution'),
        ('owner_loan', 'Owner Loan'),
        ('partner_contribution', 'Partner Contribution'),
        ('external_investment', 'External Investment'),
        ('other', 'Other'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('upi', 'UPI'),
        ('bank_transfer', 'Bank Transfer'),
    ]

    shop = models.ForeignKey(
        'shop.Shop',
        on_delete=models.CASCADE,
        related_name='investments'
    )

    account = models.ForeignKey(
        'finance.Account',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='investments'
    )

    # Party attribution - kept as structured display fields (not a full
    # Party/Person model yet) to avoid free-text fragmentation in reports
    # (e.g. "Anil" / "ANIL" / "anil" being treated as separate investors).
    investor_display_name = models.CharField(max_length=200)
    investor_phone = models.CharField(max_length=20, null=True, blank=True)
    investor_email = models.EmailField(null=True, blank=True)

    investment_type = models.CharField(
        max_length=30,
        choices=INVESTMENT_TYPE_CHOICES,
        default='capital_contribution'
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES
    )

    category = models.ForeignKey(
        'expenses_v2.ShopCategory',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='investments'
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)

    transaction_date = models.DateTimeField(
        help_text=(
            "The actual real-world date and time the investment was made - NOT when it was "
            "entered into the system (created_at). The app has no bank-feed integration, so this "
            "is user-supplied and may be backdated by several days. When the request supplies "
            "only a date, the time defaults to the server time at creation/update. Drives "
            "LedgerEntry.created_at."
        )
    )

    reference_number = models.CharField(max_length=100, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    is_opening_entry = models.BooleanField(default=False)

    # Required when a ledger-affecting field (amount/account/date/status) is
    # edited after creation.
    edit_reason = models.TextField(null=True, blank=True)

    created_by = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['shop', 'transaction_date']),
            models.Index(fields=['shop', 'account']),
            models.Index(fields=['shop', 'status']),
            models.Index(fields=['shop', 'deleted']),
            # Serves transaction_date__date__gte/lte range filters (used by
            # from_date/to_date/start_date/end_date query params) - a plain
            # index on transaction_date cannot be used by the DATE(...) cast
            # those lookups generate on a DateTimeField.
            models.Index(TruncDate('transaction_date'), 'shop', name='investment_shop_txndate_idx'),
        ]

    def __str__(self):
        return f"Investment {self.amount} from {self.investor_display_name}"
