from django.db.models import F
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from services.constants.stock_adjustment import StockAdjustmentMessages as M
from services.models import ProductVariant

from .base import decimal_str
from .docs import COMMON_ERROR_RESPONSES, PRODUCT_ID_PARAMETER, TAG

FABRIC_FIELDS = ("fabric_length", "unavailable_fabric_length", "total_fabric_length")


@extend_schema(
    tags=[TAG],
    summary="Get usable, unavailable and total stock per variant",
    description=(
        "Returns, for every non-deleted variant of the product, the usable amount, the amount out of service "
        "and the physical total, for both units and fabric length.\n\n"
        "Purpose: Shows how much of each variant is damaged or under maintenance next to what can still be "
        "booked, without changing the existing product APIs.\n\n"
        "Context: Call it to display stock status, or before a restore to know how much can be restored. "
        "`stock` / `fabric_length` are the same usable values the booking and availability APIs use.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- product_id: the product.\n\n"
        "No query parameters or request body."
    ),
    parameters=[PRODUCT_ID_PARAMETER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {M.SUMMARY_RETRIEVED}\n\n"
            "Response shape:\n"
            "- data.product_id (integer), data.product_name (string).\n"
            "- data.variants (array, ordered by variant id). Each item contains:\n"
            "  - variant_id (integer), attribute (string), is_active (boolean).\n"
            "  - stock (integer): usable units. unavailable_stock (integer): units out of service. "
            "total_stock (integer): stock + unavailable_stock.\n"
            "  - fabric_length (string | null): usable fabric. unavailable_fabric_length (string): fabric out of "
            "service. total_fabric_length (string | null): their sum; null when the variant does not track fabric.\n"
            "- data.totals: stock, unavailable_stock, total_stock (integers) summed over all variants."
        )),
        **COMMON_ERROR_RESPONSES,
    },
    examples=[
        OpenApiExample(
            "Summary",
            value={
                "status": "success",
                "message": M.SUMMARY_RETRIEVED,
                "data": {
                    "product_id": 41,
                    "product_name": "Wedding Gown",
                    "variants": [
                        {"variant_id": 101, "attribute": "M", "is_active": True, "stock": 3, "unavailable_stock": 2,
                         "total_stock": 5, "fabric_length": None, "unavailable_fabric_length": "0.00", "total_fabric_length": None},
                    ],
                    "totals": {"stock": 3, "unavailable_stock": 2, "total_stock": 5},
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@action(detail=False, methods=["get"], url_path="summary")
def summary(self, request, product_id=None):
    product = self._get_product(product_id)
    if product is None:
        return self._product_not_found_response()

    # One query; the totals are column arithmetic in SQL (NULL fabric stays NULL).
    variants = list(
        ProductVariant.objects
        .filter(product_id=product.id, deleted__isnull=True)
        .annotate(
            total_stock=F("stock") + F("unavailable_stock"),
            total_fabric_length=F("fabric_length") + F("unavailable_fabric_length"),
        )
        .order_by("id")
        .values(
            "attribute", "is_active", "stock", "unavailable_stock", "total_stock",
            *FABRIC_FIELDS, variant_id=F("id"),
        )
    )
    for variant in variants:
        for field in FABRIC_FIELDS:
            variant[field] = decimal_str(variant[field])

    totals = {
        field: sum(variant[field] for variant in variants)
        for field in ("stock", "unavailable_stock", "total_stock")
    }
    return self.success_response(
        data={"product_id": product.id, "product_name": product.name, "variants": variants, "totals": totals},
        message=M.SUMMARY_RETRIEVED,
    )
