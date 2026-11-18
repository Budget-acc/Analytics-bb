from django.db import models
from django.core.validators import URLValidator, ValidationError


class BugReport(models.Model):
    """
    Minimal + scalable bug report model for shop's users.
    - images: list of S3 URLs (JSON)
    - more_details: flexible JSON
    """
    STATUS_CHOICES = [
        ("OPEN", "Open"),
        ("IN_PROGRESS", "In Progress"),
        ("RESOLVED", "Resolved"),
    ]

    shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="bug_reports",
        help_text="Shop for which the bug was reported",
    )

    reporter = models.ForeignKey(
        "authentication.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_bugs",
        help_text="User who reported the bug",
    )

    title = models.CharField(max_length=200)
    description = models.TextField(
        help_text="Short description of the bug"
    )

    more_details = models.JSONField(
        null=True,
        blank=True,
        help_text="Extra structured info (steps, logs, device info, etc.)"
    )

    images = models.JSONField(
        default=list,
        blank=True,
        help_text="List of S3 image URLs"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="OPEN",
        db_index=True,
    )

    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop", "status"]),
            models.Index(fields=["reporter"]),
        ]
        verbose_name = "Bug Report"
        verbose_name_plural = "Bug Reports"

    def __str__(self):
        return f"{self.title} ({self.status})"

    def clean(self):
        super().clean()
        validator = URLValidator()

        if not isinstance(self.images, (list, tuple)):
            raise ValidationError({"images": "Images must be a list of URL strings."})

        for i, url in enumerate(self.images):
            if not isinstance(url, str) or not url.strip():
                raise ValidationError({"images": f"Image at index {i} must be a valid string URL."})
            validator(url)
