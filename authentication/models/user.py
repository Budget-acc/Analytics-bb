from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from phonenumber_field.modelfields import PhoneNumberField
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .user_manager import UserManager


class User(SafeDeleteModel, AbstractBaseUser, PermissionsMixin):
    _safedelete_policy = SOFT_DELETE
    ROLE_CHOICES = [
        ("ADMIN", "Admin"),
        ("OWNER", "Owner"),
        ("STAFF", "Staff"),
        ("MANAGER", "Manager"),
        # Admin-panel user restricted to the Marketplace/Vendor section
        # (see authentication/permissions.py: HasMarketplaceAccess).
        ("VENDOR_ADMIN", "Vendor Admin"),
    ]
    phone = PhoneNumberField(region="IN", unique=True)
    email = models.EmailField(null=True, blank=True)
    full_name = models.CharField(max_length=200, null=True, blank=True)
    last_name = models.CharField(max_length=100, null=True, blank=True)
    password = models.CharField(max_length=128)
    secondary_password = models.CharField(max_length=200)

    # Role & hierarchy
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sub_users",
    )

    # Flags
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    password_changed_at = models.DateTimeField(null=True, blank=True, default=None)

    # Redis-backed session management (see authentication/session_manager.py,
    # authentication/redis_scripts.py). max_allowed_sessions is the per-user
    # concurrent-session cap enforced at login (oldest session evicted beyond
    # this); session_version is the durable backing store for the Redis
    # all-devices-invalidation counter (authentication/api/v3/views/change_password.py),
    # written through to Redis on logout-all and reseeded from here if the
    # Redis key is ever missing (e.g. after a Redis restart).
    max_allowed_sessions = models.PositiveSmallIntegerField(default=1)
    session_version = models.PositiveIntegerField(default=0)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.full_name or self.phone} ({self.role})"

    groups = models.ManyToManyField(
        "auth.Group",
        verbose_name="groups",
        blank=True,
        help_text="The groups this user belongs to.",
        related_name="custom_user_set",
        related_query_name="user",
    )
    user_permissions = models.ManyToManyField(
        "auth.Permission",
        verbose_name="user permissions",
        blank=True,
        help_text="Specific permissions for this user.",
        related_name="custom_user_set",
        related_query_name="user",
    )
