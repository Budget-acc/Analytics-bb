from django.db import models


class Role(models.TextChoices):
    OWNER = "OWNER", "Owner"
    MANAGER = "MANAGER", "Manager"
    STAFF = "STAFF", "Staff"


class FeatureKey(models.TextChoices):
    LEDGER_VIEW = "LEDGER_VIEW", "Ledger - View"
    MONTHLY_GROSS_VIEW = "MONTHLY_GROSS_VIEW", "Monthly Gross - View"
    BOOKING_DELETE = "BOOKING_DELETE", "Booking - Delete"
    BOOKING_EDIT = "BOOKING_EDIT", "Booking - Edit"
    BOOKING_PAYMENT = "BOOKING_PAYMENT", "Booking - Payment"
    TRANSFER_PRODUCT = "TRANSFER_PRODUCT", "Transfer - Product"
    PRODUCT_DELETION = "PRODUCT_DELETION", "Product - Deletion"
    PRODUCT_EDIT = "PRODUCT_EDIT", "Product - Edit"
    SHOP_PERFORMANCE = "SHOP_PERFORMANCE", "Shop performance - View"


class PasswordRequirement(models.TextChoices):
    ALL = "ALL", "Everyone"
    NONE = "NONE", "No"
    STAFF = "STAFF", "Only Staff"
    MANAGER_AND_STAFF = "MANAGER_AND_STAFF", "Manager and Staff"
