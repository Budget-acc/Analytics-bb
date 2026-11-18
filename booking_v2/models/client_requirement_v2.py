from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .booking_v2 import BookingV2


class ClientRequirementV2(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE)
    booking = models.ForeignKey(
        BookingV2, on_delete=models.CASCADE, related_name="requirements"
    )
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Requirement for Booking #{self.booking.id}"
