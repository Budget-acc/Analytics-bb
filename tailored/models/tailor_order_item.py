from django.db import models
from django.db.models import Index
from django.utils import timezone
from django.core.validators import MinValueValidator
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .tailor import Tailor
from .tailor_order import TailorOrder


class TailorOrderItem(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('stitching', 'Stitching'),
        ('ready', 'Ready'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    order = models.ForeignKey(TailorOrder, related_name='items', on_delete=models.CASCADE)
    product_name = models.CharField(max_length=200, help_text="Name of the tailoring product, e.g., Black Shirt, Jogger Pants")
    product_type = models.CharField(max_length=100, null=True, blank=True, help_text="Type of product, e.g., Shirt, Pant")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    reference_image = models.URLField(blank=True, null=True)

    assigned_tailor = models.ForeignKey(Tailor, null=True, blank=True, on_delete=models.SET_NULL)
    tailor_charge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    customer_tailoring_charge = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Amount collected from the customer for tailoring this item"
    )
    customer_item_charge = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    expected_completion_date = models.DateTimeField(null=True, blank=True)
    actual_completion_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_product_variant = models.ForeignKey('services.ProductVariant', null=True, blank=True, on_delete=models.SET_NULL)
    measurements = models.JSONField(null=True, blank=True, help_text='Store all measurements as JSON key-value pairs')
    size = models.CharField(
        max_length=50, null=True, blank=True,
        help_text="Standard size label, e.g. XS, S, M, L, XL, XXL, Free Size."
    )

    notes = models.TextField(null=True, blank=True)
    variant_description = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            Index(fields=['order', 'status']),
            Index(fields=['assigned_tailor']),
        ]

    def __str__(self):
        return f"TailorOrderItem #{self.id} - {self.product_name} ({self.order.id})"
