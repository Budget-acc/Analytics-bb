import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.models import UniqueConstraint, Q, Value, Max, F, Func, IntegerField
from django.db.models.functions import Lower, Coalesce, Cast
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .shop_service import ShopOwnerService

logger = logging.getLogger("api_requests")

PRODUCT_CODE_PREFIX = "PRD"  # fallback prefix, used only when a shop has no
                              # ShopNumberingSequence.product_code_prefix yet
PRODUCT_CODE_REGEX = r"^\d{5}$"  # exact format for newly GENERATED codes


def annotate_max_product_code_number(queryset):
    """Given a Product queryset (already filtered to one shop or grouped by
    shop), annotates each row with `code_int`: the numeric value found in
    `product_code`, with all non-digit characters stripped first.

    This tolerates legacy-format values (a leftover prefix like "PRD", or a
    different digit count from before the 5-digit-only format) instead of
    requiring an exact PRODUCT_CODE_REGEX match - a strict match would
    silently exclude those rows from a Max() lookup and make the numbering
    logic think a shop has no existing codes (or a lower max than reality),
    causing it to restart/collide instead of continuing from the true last
    number. Rows with no digits at all (null, blank, non-numeric) are
    excluded by the `\\d` filter before the cast.
    """
    digits = Func(F("product_code"), Value(r"\D"), Value(""), Value("g"), function="regexp_replace")
    return queryset.filter(product_code__regex=r"\d").annotate(
        code_int=Cast(digits, IntegerField())
    )


class Product(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop_service = models.ForeignKey(
        ShopOwnerService, on_delete=models.CASCADE, related_name="products"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    category = models.CharField(max_length=50, blank=True, null=True)
    model = models.CharField(max_length=50, blank=True, null=True)
    image = models.URLField(max_length=500, blank=True, null=True)
    thumbnail_image = models.URLField(max_length=500, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    product_code = models.CharField(
        max_length=5, blank=True, null=True, editable=False, db_index=True,
        help_text=(
            "Shop-unique, auto-generated, immutable numeric product code "
            "(zero-padded, e.g. '00001'). Holds ONLY the number - no prefix. "
            "Combine with the shop's ShopNumberingSequence.product_code_prefix "
            "for display (e.g. 'PRD00001') via the `full_product_code` property."
        )
    )

    purchase_price = models.PositiveIntegerField(
        null=True, blank=True, help_text="Actual cost or purchase price of the product"
    )
    price = models.PositiveIntegerField(default=0, null=True, blank=True, help_text="Daily rental price of the product")
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Discounted selling price (if any)")

    hourly_rental_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Hourly rental price"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["shop_service"]),
            models.Index(fields=["name"]),
            models.Index(fields=["color"]),
            models.Index(fields=["category"]),
            models.Index(fields=["model"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["updated_at"]),
            models.Index(fields=['shop_service', 'is_active', '-id']),
            models.Index(fields=['shop_service', 'updated_at']),
            models.Index(fields=['is_active', 'shop_service_id']),
            models.Index(fields=['created_at']),
            models.Index(fields=['shop_service', 'deleted']),
            models.Index(fields=['shop_service', 'product_code']),
        ]
        constraints = [
            UniqueConstraint(
                Lower(Coalesce("name", Value(""))),
                Lower(Coalesce("color", Value(""))),
                Lower(Coalesce("model", Value(""))),
                Lower(Coalesce("category", Value(""))),
                "shop_service",
                condition=Q(deleted__isnull=True),
                name="unique_product_ci_per_shop"
            ),
            UniqueConstraint(
                fields=["shop_service", "product_code"],
                condition=Q(deleted__isnull=True) & Q(product_code__isnull=False),
                name="unique_product_code_per_shop_service",
            ),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new and not self.product_code and self.shop_service_id:
            shop_id = self.shop_service.shop_id
            with transaction.atomic():
                from django.db import IntegrityError
                from shop.models import ShopNumberingSequence
                sequence = (
                    ShopNumberingSequence.objects.select_for_update()
                    .filter(shop_id=shop_id).first()
                )
                if sequence is None:
                    # Normally already created by Shop.save()'s
                    # _ensure_product_code_prefix() hook - this is just a
                    # defensive fallback for a shop that predates that hook
                    # and hasn't been re-saved since.
                    #
                    # get_or_create() (not a plain create()) so a concurrent
                    # Product.save() for the same shop racing to this same
                    # branch can't both insert a row for the shop's unique
                    # `shop` OneToOneField - the loser of that race would
                    # otherwise hit an uncaught IntegrityError instead of
                    # just picking up the winner's row. The select_for_update()
                    # refetch after still applies the lock either way.
                    try:
                        sequence, _ = ShopNumberingSequence.objects.get_or_create(shop_id=shop_id)
                    except IntegrityError:
                        # Lost the create race entirely between get_or_create's
                        # own check-then-insert - the winner's row is there now.
                        pass
                    sequence = ShopNumberingSequence.objects.select_for_update().get(shop_id=shop_id)

                if not sequence.last_product_number:
                    # Lazily seed the counter from existing data - handles shops
                    # with products created before this counter existed
                    # (including anything already assigned by
                    # backfill_product_unique_code), tolerating legacy-format
                    # codes (see annotate_max_product_code_number). Paid at
                    # most once per shop.
                    sequence.last_product_number = (
                        annotate_max_product_code_number(
                            Product.objects.filter(shop_service__shop_id=shop_id)
                        )
                        .aggregate(max_code=Max("code_int"))["max_code"]
                    ) or 0

                next_number = sequence.last_product_number + 1
                if next_number > 99999:
                    # product_code is a CharField(max_length=5); f"{next_number:05d}"
                    # would silently produce a 6+ digit string once a shop crosses
                    # 99999 products, which the DB (varchar(5)) would reject at
                    # INSERT time with an opaque IntegrityError. Fail clearly here
                    # instead, before the counter is even persisted.
                    raise ValueError(
                        f"Shop {shop_id} has reached the maximum of 99999 product codes; "
                        "product_code cannot be generated for a new product."
                    )
                sequence.last_product_number = next_number
                sequence.save(update_fields=["last_product_number"])

                self.product_code = f"{next_number:05d}"

        super().save(*args, **kwargs)

    @property
    def full_product_code(self):
        """Read-only, computed display value: shop prefix + product_code.
        Never persisted - the prefix lives solely on
        ShopNumberingSequence.product_code_prefix and is combined here."""
        if not self.product_code:
            return None

        prefix = PRODUCT_CODE_PREFIX
        try:
            numbering_sequence = self.shop_service.shop.numbering_sequence
            if numbering_sequence.product_code_prefix:
                prefix = numbering_sequence.product_code_prefix
        except (ObjectDoesNotExist, AttributeError):
            # Every shop should get a ShopNumberingSequence row via
            # Shop.save()'s _ensure_product_code_prefix() hook, so landing
            # here means that row is missing/corrupted (data integrity issue,
            # not a normal "no prefix configured yet" case - that's handled
            # above by product_code_prefix simply being falsy). Falling back
            # to the generic "PRD" prefix is still the right *behavior* (this
            # property is used directly in serializers/PDFs and must not
            # raise), but silently doing so would hide the underlying problem
            # from whoever's debugging a wrong-looking code later.
            logger.warning(
                "Product %s: could not resolve shop's numbering_sequence "
                "for full_product_code - falling back to default prefix %r. "
                "This shop may be missing its ShopNumberingSequence row.",
                self.pk, PRODUCT_CODE_PREFIX,
            )

        return f"{prefix}{self.product_code}"

    def __str__(self):
        return f"{self.name}({self.id})"
