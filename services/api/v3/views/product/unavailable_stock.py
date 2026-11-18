import logging

from django.db.models import Exists, F, OuterRef, Prefetch, Q, Subquery, TextField
from django.db.models.functions import Cast
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.decorators import action

from ERP.utils.custom_pagination import StandardPageNumberPagination
from services.api.v3.serializers import UnavailableStockQuerySerializer
from services.api.v3.views.stock_adjustment.base import decimal_str
from services.constants.stock_adjustment import StockAdjustmentMessages as M
from services.models import (
    Product,
    ProductVariant,
    ProductVariantStockAdjustment,
    ServiceAttributeValue,
    ShopOwnerService,
)
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")

# A variant is listed when any of its units or fabric is out of service.
UNAVAILABLE = Q(unavailable_stock__gt=0) | Q(unavailable_fabric_length__gt=0)

VARIANT_FIELDS = (
    "id", "product_id", "attribute", "unique_code",
    "stock", "unavailable_stock", "fabric_length", "unavailable_fabric_length",
)

ADJUSTMENT_FIELDS = ("id", "variant_id", "reason", "note", "quantity", "fabric_length", "created_at")

# Newest adjustment of the product's listed variants: decides the product's position.
LATEST_ADJUSTMENT_AT = Subquery(
    ProductVariantStockAdjustment.objects
    .filter(
        Q(variant__unavailable_stock__gt=0) | Q(variant__unavailable_fabric_length__gt=0),
        product=OuterRef("pk"), variant__is_active=True, variant__deleted__isnull=True,
    )
    .order_by("-created_at", "-id")
    .values("created_at")[:1]
)

# Same whitelist as the product list API; any other search_by is a service attribute key.
STANDARD_SEARCH_FIELDS = ("name", "color", "category", "model")

# Same rule as the product list's is_gadget_service, evaluated per product.
GADGET_SERVICE = (
    Q(shop_service__general_service__name__icontains="gadget")
    | Q(shop_service__general_service__main_category__name__icontains="gadget")
)

SUCCESS_EXAMPLE = {
    "status": "success",
    "message": "Data retrieved successfully",
    "data": {
        "next": None, "previous": None, "count": 1, "page_size": 15, "current_page": 1,
        "total_pages": 1, "start_index": 1, "end_index": 1,
        "data": [{
            "id": 41, "name": "Wedding Gown", "product_code": "PRD00041",
            "color": "Red", "category": "Bridal", "model": None,
            "thumbnail_image": "https://cdn.example.com/products/41/thumb.jpg",
            "shop_service_id": 8, "service_name": "Dress Rental",
            "totals": {"unavailable_stock": 2, "unavailable_fabric_length": "4.50"},
            "variants": [
                {"id": 101, "attribute": "M", "unique_code": "S8-P41-V101",
                 "stock": 3, "unavailable_stock": 2, "total_stock": 5,
                 "fabric_length": None, "unavailable_fabric_length": "0.00", "total_fabric_length": None,
                 "latest_adjustment": {"reason": "damage", "note": "Torn sleeve",
                                       "unavailable_stock": 2, "unavailable_fabric_length": None}},
                {"id": 103, "attribute": "Lining", "unique_code": "S8-P41-V103",
                 "stock": 1, "unavailable_stock": 0, "total_stock": 1,
                 "fabric_length": "15.50", "unavailable_fabric_length": "4.50", "total_fabric_length": "20.00",
                 "latest_adjustment": {"reason": None, "note": None,
                                       "unavailable_stock": None, "unavailable_fabric_length": "4.50"}},
            ],
        }],
    },
}


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="List products with unavailable (damaged / maintenance) stock",
    description=(
        "Lists the active shop's products that currently have units or fabric length out of service, each with "
        "only the variants that have something out of service.\n\n"
        "Purpose: Gives the shop one place to see everything marked unavailable because of damage, maintenance "
        "or another reason, so it can follow up on repairs and restore the stock.\n\n"
        "Context: Amounts are moved out of service and back with POST "
        "products/{product_id}/stock-adjustments/. A variant is listed when unavailable_stock > 0 or "
        "unavailable_fabric_length > 0; other variants of the same product are left out. The amounts are "
        "combined across reasons - the reason of each movement is in GET products/{product_id}/stock-adjustments/. "
        "Only active services, products and variants are included; deleted ones never are. Each product appears "
        "once, ordered by the latest stock adjustment of its listed variants, newest first (products with no "
        "adjustment record, e.g. older data, come last, newest product first). Read-only; works while the shop subscription is inactive. Rate limited to 60 "
        "requests per minute per user.\n\n"
        "Accepted inputs:\n"
        "Headers - Required:\n"
        "- X-Active-Shop-ID (integer): the shop whose products are listed.\n\n"
        "Query parameters - Optional:\n"
        "- shop_service_id (integer >= 1): only products of this service. Default: all active services of the shop.\n"
        "- search (string): text to search for, case-insensitive contains match, same as the product list API. "
        "Blank = no search.\n"
        "- search_by (string): name (default), color, category, model, or a service attribute key of the "
        "product's service (e.g. fabric_type). For gadget services name/color/category/model also match an "
        "active variant's attribute.\n"
        "Search only narrows the list (the order stays latest adjustment first); products with nothing out of "
        "service are never listed.\n"
        "- page (integer >= 1): page number. Default: 1.\n"
        "- page_size (integer 1-100): products per page. Default: 15."
    ),
    parameters=[
        OpenApiParameter(
            name="X-Active-Shop-ID", type=OpenApiTypes.INT, location=OpenApiParameter.HEADER, required=True,
            description=(
                "Purpose: Selects the shop whose products are listed; the user must have access to it. "
                "Location: request header. Format/Type: integer shop id (e.g. 12). Required."
            ),
        ),
        OpenApiParameter(
            name="shop_service_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Limits the list to the products of one service (the same id the product list APIs use). "
                "Must be an active service of the active shop. "
                "Location: query parameter. Format/Type: positive integer (e.g. 8). "
                "Optional. Default: products of all active services of the shop."
            ),
        ),
        OpenApiParameter(
            name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Finds products by text, the same way the product list API does. "
                "Location: query parameter. Format/Type: string, case-insensitive contains match; surrounding "
                "spaces are trimmed (e.g. gown). Optional. Default: no search (blank is the same)."
            ),
        ),
        OpenApiParameter(
            name="search_by", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: The field search is matched against. Location: query parameter. Format/Type: string, "
                "case-insensitive: name, color, category, model, or a service attribute key of the product's "
                "service (e.g. fabric_type). An unknown key matches nothing. Only used with search. "
                "Optional. Default: name."
            ),
        ),
        OpenApiParameter(
            name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description="Purpose: Page number. Location: query parameter. Format/Type: integer >= 1. Optional. Default: 1.",
        ),
        OpenApiParameter(
            name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Number of products per page. Location: query parameter. Format/Type: integer 1-100 "
                "(larger values are capped at 100). Optional. Default: 15."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description=(
            "Success. Page-number paginated list. Exact message: Data retrieved successfully\n\n"
            "Response shape:\n"
            "- data.count, data.page_size, data.current_page, data.total_pages, data.start_index, "
            "data.end_index (integers); data.next, data.previous (string | null): pagination.\n"
            "- data.data (array of products). Each product contains:\n"
            "  - id (integer), name (string), product_code (string | null, e.g. PRD00041), color, category, "
            "model, thumbnail_image (string | null).\n"
            "  - shop_service_id (integer), service_name (string).\n"
            "  - totals: unavailable_stock (integer) and unavailable_fabric_length (string, 2 dp) summed over "
            "the listed variants.\n"
            "  - variants (array, ordered by id, only variants with something out of service). Each contains:\n"
            "    - id (integer), attribute (string), unique_code (string | null).\n"
            "    - stock (integer): usable units. unavailable_stock (integer): units out of service. "
            "total_stock (integer): stock + unavailable_stock.\n"
            "    - fabric_length (string | null, 2 dp): usable fabric. unavailable_fabric_length (string, 2 dp): "
            "fabric out of service. total_fabric_length (string | null): their sum. fabric_length and "
            "total_fabric_length are null when the variant does not track fabric.\n"
            "    - latest_adjustment (object | null): the variant's latest mark-unavailable movement, null when "
            "it has none (e.g. older data). Restores are not shown here; the full history is in GET "
            "products/{product_id}/stock-adjustments/. Contains:\n"
            "      - reason (string | null): damage, maintenance or other; null when no reason was given.\n"
            "      - note (string | null): null when no note was given.\n"
            "      - unavailable_stock (integer | null): units marked unavailable; null when only fabric moved.\n"
            "      - unavailable_fabric_length (string | null, 2 dp): fabric marked unavailable; null when only "
            "units moved.\n"
            "An empty data.data means nothing is out of service, or nothing out of service matches search "
            "(including an unknown search_by attribute key)."
        )),
        400: OpenApiResponse(description=(
            f"Invalid shop_service_id (not a positive integer). Exact message: {M.INVALID_SERVICE_ID.message}"
        )),
        401: OpenApiResponse(description=(
            "Authentication error (DRF `detail` response). Exact messages include:\n"
            "- Authentication credentials were not provided.\n"
            "- Given token not valid for any token type\n"
            "- Session invalidated\n"
            "- Token invalidated by password change"
        )),
        403: OpenApiResponse(description=(
            "The X-Active-Shop-ID header is missing or invalid, or the user cannot access the shop "
            "(DRF `detail` response). Exact message: You do not have permission to perform this action."
        )),
        404: OpenApiResponse(description=(
            "Not found. Exact messages:\n"
            f"- {M.SERVICE_NOT_FOUND.message} (shop_service_id is unknown, inactive, deleted or belongs to "
            "another shop)\n"
            "- Invalid page. (DRF `detail` response; page is past the last page)"
        )),
        429: OpenApiResponse(description=(
            "Rate limited (DRF `detail` response). Exact message: Request was throttled. "
            "Expected available in <n> seconds."
        )),
        500: OpenApiResponse(description=f"Unexpected error. Exact message: {M.SERVER_ERROR.message}"),
    },
    examples=[
        OpenApiExample(
            "Search request",
            value={"shop_service_id": 8, "search": "gown", "search_by": "name", "page": 1},
            request_only=True,
        ),
        OpenApiExample("Success", value=SUCCESS_EXAMPLE, response_only=True, status_codes=["200"]),
        OpenApiExample(
            "Invalid service id",
            value={"status": "validation_error", "message": M.INVALID_SERVICE_ID.message, "data": None,
                   "dev_message": f"{M.INVALID_SERVICE_ID.dev_message} Field: shop_service_id."},
            response_only=True, status_codes=["400"],
        ),
        OpenApiExample(
            "Service not available",
            value={"status": "not_found", "message": M.SERVICE_NOT_FOUND.message, "data": None,
                   "dev_message": M.SERVICE_NOT_FOUND.dev_message},
            response_only=True, status_codes=["404"],
        ),
    ],
)
@skip_subscription_validation
@action(detail=False, methods=["get"], url_path="unavailable-stock")
def unavailable_stock(self, request):
    query = UnavailableStockQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return self.validation_error_response(
            errors=query.errors,
            message=M.INVALID_SERVICE_ID.message,
            dev_message=f"{M.INVALID_SERVICE_ID.dev_message} Field: shop_service_id.",
        )
    service_id = query.validated_data.get("shop_service_id")
    search = query.validated_data.get("search", "")
    search_by = query.validated_data["search_by"].strip().lower() or "name"

    try:
        product_filter = Q(
            is_active=True,
            shop_service__shop_id=self.shop_id,
            shop_service__is_active=True,
            shop_service__deleted__isnull=True,
        )
        if service_id is not None:
            # A service of another shop gets the same 404, so it is not revealed.
            if not ShopOwnerService.objects.filter(id=service_id, shop_id=self.shop_id, is_active=True).exists():
                return self.not_found_response(
                    resource="ShopOwnerService",
                    message=M.SERVICE_NOT_FOUND.message,
                    dev_message=M.SERVICE_NOT_FOUND.dev_message,
                )
            product_filter &= Q(shop_service_id=service_id)

        # Shop scoping goes through the product's service: ProductVariant.shop
        # is nullable on older rows. Exists (not a join) keeps one row per
        # product without distinct(); the Prefetch applies the same filter, so
        # only the variants with something out of service are returned. The
        # sliced nested Prefetch fetches each variant's latest mark-unavailable
        # row in one query per page (window function), not one per variant.
        listed_variants = ProductVariant.objects.filter(UNAVAILABLE, is_active=True)
        latest_adjustment = Prefetch(
            "stock_adjustments",
            queryset=(
                ProductVariantStockAdjustment.objects
                .filter(action=ProductVariantStockAdjustment.Action.MARK_UNAVAILABLE)
                .only(*ADJUSTMENT_FIELDS)
                .order_by("-created_at", "-id")[:1]
            ),
            to_attr="latest_adjustments",
        )
        products = (
            Product.objects
            .filter(product_filter)
            .filter(Exists(listed_variants.filter(product=OuterRef("pk"))))
            .select_related("shop_service__general_service", "shop_service__shop__numbering_sequence")
            .prefetch_related(Prefetch(
                "variants",
                queryset=listed_variants.only(*VARIANT_FIELDS).order_by("id").prefetch_related(latest_adjustment),
                to_attr="unavailable_variants",
            ))
        )
        if search:
            products = _apply_search(products, search, search_by)
        # The subquery returns one value per product, so each product stays one row.
        products = products.annotate(last_adjusted_at=LATEST_ADJUSTMENT_AT).order_by(
            F("last_adjusted_at").desc(nulls_last=True), "-id",
        )

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(products, request, view=self)
        return paginator.get_paginated_response([_product_dict(product) for product in page])
    except Exception:
        logger.exception(
            "Unavailable stock list failed: shop=%s shop_service_id=%s search=%r search_by=%r",
            self.shop_id, service_id, search[:50], search_by[:50],  # truncated against log injection
        )
        return self.error_response(
            message=M.SERVER_ERROR.message,
            dev_message=M.SERVER_ERROR.dev_message,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _apply_search(products, search, search_by):
    """The product list API's search filters, as SQL; the caller keeps its own ordering.

    shop_service_id is optional here, so the general-service checks the
    product list does once in Python (gadget service, attribute template of
    the service) are done per product in SQL. Exists (not joins) keeps one
    row per product, so no distinct() is needed and page counts stay right.
    """
    if search_by not in STANDARD_SEARCH_FIELDS:
        # A service attribute key of the product's own general service; an
        # unknown key simply matches nothing, as in the product list.
        matching_attributes = ServiceAttributeValue.objects.annotate(
            value_text=Cast("value", TextField()),
        ).filter(
            product=OuterRef("pk"),
            attribute__attribute_key=search_by,
            attribute__applies_to="product",
            attribute__general_service_id=OuterRef("shop_service__general_service_id"),
            value_text__icontains=search,
        )
        return products.filter(Exists(matching_attributes))

    # search_by is whitelisted above, so it is safe in the lookups below.
    field_query = Q(**{f"{search_by}__icontains": search})
    gadget_variant_match = Exists(ProductVariant.objects.filter(
        product=OuterRef("pk"), is_active=True, attribute__icontains=search,
    ))
    field_query |= GADGET_SERVICE & Q(gadget_variant_match)
    return products.filter(field_query)


def _adjustment_dict(adjustment):
    """The essentials of a mark-unavailable row; amounts that did not move are null."""
    return {
        "reason": adjustment.reason,
        "note": adjustment.note or None,
        "unavailable_stock": adjustment.quantity or None,
        "unavailable_fabric_length": decimal_str(adjustment.fabric_length) if adjustment.fabric_length else None,
    }


def _variant_dict(variant):
    fabric = variant.fabric_length
    return {
        "id": variant.id,
        "attribute": variant.attribute,
        "unique_code": variant.unique_code,
        "stock": variant.stock,
        "unavailable_stock": variant.unavailable_stock,
        "total_stock": variant.stock + variant.unavailable_stock,
        "fabric_length": decimal_str(fabric),
        "unavailable_fabric_length": decimal_str(variant.unavailable_fabric_length),
        "total_fabric_length": None if fabric is None else decimal_str(fabric + variant.unavailable_fabric_length),
        "latest_adjustment": (
            _adjustment_dict(variant.latest_adjustments[0]) if variant.latest_adjustments else None
        ),
    }


def _product_dict(product):
    variants = product.unavailable_variants
    return {
        "id": product.id,
        "name": product.name,
        "product_code": product.full_product_code,
        "color": product.color,
        "category": product.category,
        "model": product.model,
        "thumbnail_image": product.thumbnail_image,
        "shop_service_id": product.shop_service_id,
        "service_name": product.shop_service.general_service.name,
        "totals": {
            "unavailable_stock": sum(variant.unavailable_stock for variant in variants),
            "unavailable_fabric_length": decimal_str(sum(variant.unavailable_fabric_length for variant in variants)),
        },
        "variants": [_variant_dict(variant) for variant in variants],
    }
