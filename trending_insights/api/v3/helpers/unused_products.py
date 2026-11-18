from django.db.models import Count, F, IntegerField, Max, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce, Greatest, TruncDate

from booking_v2.models import BookedProductVariant
from sales.models import SaleItem
from services.models import Product

from .common import active_booking_items

# Single source of truth for the Unused Products API and its Excel report.
MAX_RANGE_DAYS = 184
MAX_ORDERED_COUNT = 5

# Columns the API and the Excel report read - loaded up front so reading
# them (and the service chain) never issues an extra query per product.
PRODUCT_FIELDS = (
    "id", "name", "color", "category", "thumbnail_image", "image",
    "model", "shop_service_id",
    "shop_service__general_service__main_category__name",
)


def _per_product(queryset, group_field, aggregate):
    """Correlated subquery returning one aggregate value per outer product."""
    return Subquery(
        queryset.order_by()
        .values(group_field)
        .annotate(value=aggregate)
        .values("value")[:1]
    )


def get_product_usage(shop_id, start, end, max_orders=MAX_ORDERED_COUNT):
    """
    Unused (0 orders) and low-usage (1..max_orders orders) products of the
    shop in the [start, end) window, ordered by ordered_count ascending
    (then newest product first). Shared by the Unused Products API and its
    Excel report so both always return the same rows.

    Each product is annotated with:
    - ordered_count: distinct Bookings (by Booking.created_at) + distinct
      Sales (by Sale.sale_date) containing the product. A booking/sale with
      several variants of the product counts once. Soft-deleted and cancelled
      bookings never count (see active_booking_items); deleted sales are
      hard-deleted.
    - last_ordered_date: the latest of the counted bookings' created_at
      (local date) and sales' sale_date; None when ordered_count is 0.

    Only active, non-deleted products are considered. Returns a lazy
    QuerySet computed entirely in the database (correlated subqueries) -
    callers paginate or iterate it, so nothing is loaded into memory in bulk.
    """
    bookings = active_booking_items(
        BookedProductVariant.objects.filter(
            shop_id=shop_id,
            product_variant__product_id=OuterRef("pk"),
            booking__created_at__gte=start,
            booking__created_at__lt=end,
        )
    )
    sales = SaleItem.objects.filter(
        sale__shop_id=shop_id,
        product_variant__product_id=OuterRef("pk"),
        sale__sale_date__gte=start.date(),
        sale__sale_date__lt=end.date(),
    )
    product_field = "product_variant__product_id"

    return (
        Product.objects.filter(shop_service__shop_id=shop_id, is_active=True)
        .annotate(
            booking_orders=Coalesce(
                _per_product(bookings, product_field, Count("booking_id", distinct=True)),
                Value(0), output_field=IntegerField(),
            ),
            sale_orders=Coalesce(
                _per_product(sales, product_field, Count("sale_id", distinct=True)),
                Value(0), output_field=IntegerField(),
            ),
            last_booking_date=_per_product(bookings, product_field, Max(TruncDate("booking__created_at"))),
            last_sale_date=_per_product(sales, product_field, Max("sale__sale_date")),
        )
        .annotate(
            ordered_count=F("booking_orders") + F("sale_orders"),
            # PostgreSQL GREATEST ignores NULLs, so a product with only
            # bookings or only sales still gets its date.
            last_ordered_date=Greatest("last_booking_date", "last_sale_date"),
        )
        .filter(ordered_count__lte=max_orders)
        .select_related("shop_service__general_service__main_category")
        .only(*PRODUCT_FIELDS)
        .order_by("ordered_count", "-id")
    )
