from django.db import models


class DocumentType(models.TextChoices):
    BOOKING = "booking", "Booking"
    SALE = "sale", "Sale"
    TAILOR_ORDER = "tailor_order", "Custom Work / Tailor Order"
