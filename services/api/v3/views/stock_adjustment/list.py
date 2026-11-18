from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from ERP.utils.custom_pagination import StandardPageNumberPagination
from services.api.v3.serializers.stock_adjustment import (
    StockAdjustmentHistoryQuerySerializer,
    StockAdjustmentHistorySerializer,
)
from services.constants.stock_adjustment import StockAdjustmentMessages as M
from services.models import ProductVariantStockAdjustment

from .docs import COMMON_ERROR_RESPONSES, PRODUCT_ID_PARAMETER, TAG

HISTORY_FIELDS = (
    "id", "batch_id", "action", "reason", "note", "variant_id",
    "quantity", "fabric_length",
    "previous_stock", "updated_stock", "previous_unavailable_stock", "updated_unavailable_stock",
    "previous_fabric_length", "updated_fabric_length",
    "previous_unavailable_fabric_length", "updated_unavailable_fabric_length",
    "created_at", "variant__id", "variant__attribute", "created_by__id", "created_by__full_name",
)


@extend_schema(
    tags=[TAG],
    summary="List stock adjustment history for a product",
    description=(
        "Returns every stock / fabric movement recorded for the product's variants, newest first.\n\n"
        "Purpose: Shows when stock was marked unavailable or restored, by whom, for what reason (damage, "
        "maintenance, other) and the before/after values, so damaged and maintenance stock can be told apart "
        "and audited.\n\n"
        "Context: Read-only. Rows are written only by the stock-adjustment POST endpoint and are never edited "
        "or deleted. Includes rows for variants deleted later.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- product_id: the product.\n\n"
        "Query parameters - Optional:\n"
        "- variant_id (integer), action (mark_unavailable | restore), reason (damage | maintenance | other), "
        "batch_id (UUID): filters.\n"
        "- page (integer, default 1), page_size (integer, default 15, max 100): pagination."
    ),
    parameters=[
        PRODUCT_ID_PARAMETER,
        OpenApiParameter(
            name="variant_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Show only the history of one variant. Location: query parameter. "
                "Format/Type: positive integer. Optional. Default: all variants of the product."
            ),
        ),
        OpenApiParameter(
            name="action", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            enum=[choice for choice, _ in ProductVariantStockAdjustment.Action.choices],
            description=(
                "Purpose: Show only one direction of movement. Location: query parameter. "
                "Format/Type: string, mark_unavailable or restore. Optional. Default: both."
            ),
        ),
        OpenApiParameter(
            name="reason", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            enum=[choice for choice, _ in ProductVariantStockAdjustment.Reason.choices],
            description=(
                "Purpose: Show only movements for one reason, e.g. only damage. Location: query parameter. "
                "Format/Type: string, damage, maintenance or other. Optional. Default: all reasons."
            ),
        ),
        OpenApiParameter(
            name="batch_id", type=OpenApiTypes.UUID, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Show only the rows written by one POST request (its data.batch_id). "
                "Location: query parameter. Format/Type: UUID string. Optional."
            ),
        ),
        OpenApiParameter(
            name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description="Purpose: Page number. Location: query parameter. Format/Type: integer >= 1. Optional. Default: 1.",
        ),
        OpenApiParameter(
            name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Rows per page. Location: query parameter. Format/Type: integer 1-100. "
                "Optional. Default: 15."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description=(
            "Success. Page-number paginated history.\n\n"
            "Response shape:\n"
            "- data.count, data.page_size, data.current_page, data.total_pages, data.start_index, data.end_index "
            "(integers); data.next, data.previous (string | null).\n"
            "- data.data (array). Each row contains:\n"
            "  - id (integer), batch_id (UUID string), action (string), "
            "reason (string | null - null when no reason was given), note (string).\n"
            "  - variant: {id (integer), attribute (string)}.\n"
            "  - quantity (integer), fabric_length (string, 2 dp): amount moved.\n"
            "  - previous_stock, updated_stock, previous_unavailable_stock, updated_unavailable_stock (integers).\n"
            "  - previous_fabric_length, updated_fabric_length (string | null), "
            "previous_unavailable_fabric_length, updated_unavailable_fabric_length (string).\n"
            "  - created_by: {id (integer), full_name (string | null)} or null.\n"
            "  - created_at (ISO 8601 datetime)."
        )),
        400: OpenApiResponse(description=f"Invalid filter. Exact message: {M.INVALID_FILTERS.message}"),
        **COMMON_ERROR_RESPONSES,
        404: OpenApiResponse(description=(
            "Not found. Exact messages:\n"
            f"- {M.PRODUCT_NOT_FOUND.message}\n"
            "- Invalid page."
        )),
    },
    examples=[
        OpenApiExample(
            "History row",
            value={
                "id": 9001, "batch_id": "9a1e6c1e-3f2b-4c7d-9a43-2f1d8c6b5e10", "action": "mark_unavailable",
                "reason": "damage", "note": "Torn sleeve", "variant": {"id": 101, "attribute": "M"},
                "quantity": 2, "fabric_length": "0.00",
                "previous_stock": 5, "updated_stock": 3, "previous_unavailable_stock": 0, "updated_unavailable_stock": 2,
                "previous_fabric_length": None, "updated_fabric_length": None,
                "previous_unavailable_fabric_length": "0.00", "updated_unavailable_fabric_length": "0.00",
                "created_by": {"id": 7, "full_name": "Shop Owner"}, "created_at": "2026-10-28T10:15:00+05:30",
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def list(self, request, product_id=None):
    product = self._get_product(product_id)
    if product is None:
        return self._product_not_found_response()

    filters = StockAdjustmentHistoryQuerySerializer(data=request.query_params)
    if not filters.is_valid():
        return self.validation_error_response(
            errors=filters.errors,
            message=M.INVALID_FILTERS.message,
            dev_message=M.INVALID_FILTERS.dev_message.format(fields=", ".join(filters.errors)),
        )

    # validated_data keys are whitelisted serializer fields that match model
    # field names, so they are safe to pass straight to filter().
    queryset = (
        ProductVariantStockAdjustment.objects
        .filter(product_id=product.id, shop_id=self.shop_id, **filters.validated_data)
        .select_related("variant", "created_by")
        .only(*HISTORY_FIELDS)
        .order_by("-created_at", "-id")
    )

    paginator = StandardPageNumberPagination()
    page = paginator.paginate_queryset(queryset, request, view=self)
    return paginator.get_paginated_response(StockAdjustmentHistorySerializer(page, many=True).data)
