import logging
from django.db import models, transaction
from django.db.models import Index, Max
from django.utils import timezone
from django.db.models.functions import Cast, Substr
from safedelete.models import SafeDeleteModel, SOFT_DELETE

logger = logging.getLogger(__name__)


class TailorOrder(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    CUSTOMER_ORDER_TYPE = [
        ("shop", "Shop Inventory"),
        ("rental", "Rental"),
        ("sale", "Sale"),
    ]

    STATUS_CHOICES = [
        ("initiated", "Initiated"),
        ("in_progress", "In Progress"),
        ("ready_to_deliver", "Ready to deliver"),
        ("delivered", "Delivered"),
        ("returned", "Returned"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    TAX_CALCULATION_TYPE_CHOICES = (
        ("exclusive", "Exclusive"),
        ("inclusive", "Inclusive"),
    )

    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE, related_name='tailor_orders')
    created_by = models.ForeignKey('authentication.User', null=True, blank=True, on_delete=models.SET_NULL)
    client = models.ForeignKey('booking_v2.Client', null=True, blank=True, on_delete=models.SET_NULL)
    staff = models.ForeignKey('shop.Staff', null=True, blank=True, on_delete=models.SET_NULL)

    customer_order_type = models.CharField(max_length=10, choices=CUSTOMER_ORDER_TYPE, null=True, blank=True, default='shop')
    client_phone_number = models.CharField(max_length=15, null=True, blank=True)

    total_tailor_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_additional_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customer_total_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0) #instead of total_amount
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    security_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    pickup_date = models.DateTimeField(null=True, blank=True)
    return_date = models.DateTimeField(null=True, blank=True)
    client_address = models.TextField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='initiated')
    notes = models.TextField(null=True, blank=True)
    processed_to_booking_sale = models.BooleanField(default=False)

    created_booking = models.ForeignKey('booking_v2.BookingV2', null=True, blank=True, on_delete=models.SET_NULL)

    shop_tailor_id = models.CharField(
        max_length=10, db_index=True, null=True, blank=True,
        help_text="Shop-specific Tailor Order ID (incremental per shop)"
    )

    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    recorded_at = models.DateTimeField(auto_now_add=True)

    # --- Tax snapshot fields ---
    # Copied from ShopTaxConfiguration at the time tax is calculated (not a
    # live FK) so historical orders are unaffected by later changes to the
    # shop's tax configuration. All default to zero/blank/False, so existing
    # rows and any code path that doesn't populate them behave exactly as
    # before (no tax applied).
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_name = models.CharField(max_length=30, blank=True, default="")
    tax_calculation_type = models.CharField(
        max_length=15,
        choices=TAX_CALCULATION_TYPE_CHOICES,
        blank=True,
        default="",
    )
    is_tax_applied = models.BooleanField(default=False)
    # Snapshot of ShopTaxConfiguration.taxable_components at creation time, so
    # later changes to the shop's live tax config never retroactively affect
    # which parts of this order are taxed on subsequent updates.
    taxable_components = models.JSONField(default=list, blank=True)

    documents = models.JSONField(default=list, blank=True, help_text="List of S3 document image URLs")
    documents_details = models.JSONField(default=dict, blank=True, null=True)

    class Meta:
        indexes = [
            Index(fields=['shop', 'status']),
            Index(fields=['shop', 'created_at']),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new and not self.shop_tailor_id and self.shop_id:
            try:
                with transaction.atomic():
                    from shop.models import Shop, ShopNumberingSequence
                    sequence = (
                        ShopNumberingSequence.objects.select_for_update()
                        .filter(shop_id=self.shop_id).first()
                    )
                    if sequence is None:
                        # Normally already created by Shop.save()'s
                        # _ensure_product_code_prefix() hook - this is just a
                        # defensive fallback for a shop that predates that hook
                        # and hasn't been re-saved since.
                        sequence = ShopNumberingSequence.objects.create(shop_id=self.shop_id)
                        sequence = ShopNumberingSequence.objects.select_for_update().get(pk=sequence.pk)

                    if sequence.last_tailor_id == 0:
                        max_id = (
                            TailorOrder.objects.filter(shop_id=self.shop_id, shop_tailor_id__regex=r"^TO\d{6}$")
                            .annotate(shop_tailor_id_int=Cast(Substr("shop_tailor_id", 3), models.IntegerField()))
                            .aggregate(max_id=Max("shop_tailor_id_int"))
                        )["max_id"] or 0

                        if max_id > 0:
                            sequence.last_tailor_id = max_id
                        else:
                            sequence.last_tailor_id = sequence.tailor_start_id or 0

                    next_id = sequence.last_tailor_id + 1
                    sequence.last_tailor_id = next_id
                    sequence.save(update_fields=["last_tailor_id"])

                    self.shop_tailor_id = f"TO{next_id:06d}"

            except Shop.DoesNotExist:
                logger.error(f"Shop {self.shop_id} not found during TailorOrder ID generation")
                raise
            except Exception as e:
                logger.error(f"Error generating TailorOrder ID: {str(e)}", exc_info=True)
                raise

        super().save(*args, **kwargs)

    def __str__(self):
        return f"TailorOrder #{self.id} - {self.shop.name} - {self.customer_order_type}"
