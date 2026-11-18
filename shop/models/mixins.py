from django.db import models
from django.utils.text import slugify
from nanoid import generate


class AutoSlugPublicTokenMixin(models.Model):
    """
    Auto-generates `slug` (from a configurable source field) and a unique
    `public_token` on first save, if not already set. Used by Shop and
    Organization so both follow identical generation logic.
    """

    class Meta:
        abstract = True

    slug_source_field = "name"     # override per model if ever needed
    public_token_length = 12       # Shop=12, Organization=10 (see subclasses)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(getattr(self, self.slug_source_field))
        if not self.public_token:
            self.public_token = self.generate_unique_public_token()
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_public_token(cls):
        while True:
            token = generate(size=cls.public_token_length)
            if not cls.objects.filter(public_token=token).exists():
                return token
