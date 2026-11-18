# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now
from datetime import timedelta
from booking_v2.models import BookingV2, ProductVariantAvailability


@receiver(post_save, sender=BookingV2)
def handle_booking_completed(sender, instance, created, **kwargs):
    if not created and instance.booking_status == "completed":
        cutoff_date = now().date() - timedelta(days=7)

        # Get up to 20 old availability record IDs
        old_ids = list(
            ProductVariantAvailability.objects
            .filter(date__lt=cutoff_date)
            .order_by("date")  # optional: to delete the oldest first
            .values_list("id", flat=True)[:20]
        )

        if old_ids:
            ProductVariantAvailability.objects.filter(id__in=old_ids).delete()
            print(f"🧹 Deleted {len(old_ids)} old availability records after booking completion.")
