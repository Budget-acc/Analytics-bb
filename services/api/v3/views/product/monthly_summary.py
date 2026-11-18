import logging
from collections import defaultdict
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, IntegerField, Case, When
from django.db.models.functions import ExtractYear, ExtractMonth
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product, ProductVariant
from expenses_v2.models import ProductVariantExpense
from booking_v2.models import BookedProductVariant

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Get monthly product summary",
    description="Returns month-wise total expense and earned amount for a product.",
    parameters=[OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Product id.")],
    responses={
        200: OpenApiResponse(description="Standard success response with monthly summary list."),
        403: OpenApiResponse(description="Exact message: You do not have permission to access this product."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Monthly summary retrieved successfully.", "data": [{"year": 2026, "month": 5, "total_expense": 2000, "total_earned": 4500}]}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path=r"monthly-summary/(?P<pk>\d+)")
def monthly_summary(self, request, pk=None):
    """
    Returns product-wise monthly total expense and earnings,
    aggregated from all its variants.
    """
    try:
        try:
            from shop.api.v3.utils.shop import get_active_shop_id
            shop_id = get_active_shop_id(request)
        except Exception as e:
            return self.permission_denied_response(
                message="You do not have permission to access this product."
            )

        product = get_object_or_404(Product, id=pk, is_active=True)

        variants = ProductVariant.objects.filter(product=product, is_active=True)

        expense_qs = ProductVariantExpense.objects.filter(
            product_variant__in=variants, shop_id=shop_id
        ).annotate(
            effective_year=Case(
                When(expense_date_time__isnull=False, then=ExtractYear('expense_date_time')),
                default=ExtractYear('date'),
                output_field=IntegerField(),
            ),
            effective_month=Case(
                When(expense_date_time__isnull=False, then=ExtractMonth('expense_date_time')),
                default=ExtractMonth('date'),
                output_field=IntegerField(),
            ),
        ).values("effective_year", "effective_month").annotate(
            total_expense=Sum("expense")
        )

        # earning_qs = BookedProductVariant.objects.filter(
        #     product_variant__in=variants,
        #     shop_id=shop_id,
        #     booking__payment_status=True,
        #     booking__booking_status="completed",
        #     booking__deleted__isnull=True,
        # ).values("booking__pickup_date__year","booking__pickup_date__month").annotate(
        #     total_earned=Sum("amount")
        # )
        booked_items = (
            BookedProductVariant.objects.filter(
                product_variant__in=variants,
                shop_id=shop_id,
                booking__payment_status=True,
                booking__booking_status="completed",
                booking__deleted__isnull=True,
            )
            .select_related("booking")
            .values(
                "id",
                "amount",
                "booking__discount_amount",
                "booking__pickup_date__year",
                "booking__pickup_date__month",
                "booking__id",
            )
        )

        booking_item_counts = (
            BookedProductVariant.objects.filter(
                shop_id=shop_id,
                booking__payment_status=True,
                booking__booking_status="completed",
                booking__deleted__isnull=True,
            )
            .values("booking_id")
            .annotate(total_items=Count("id"))
        )
        booking_item_count_map = {
            entry["booking_id"]: entry["total_items"]
            for entry in booking_item_counts
        }

        monthly_data = defaultdict(lambda: {"total_expense": 0, "total_earned": 0})

        for entry in expense_qs:
            key = (entry["effective_year"], entry["effective_month"])
            monthly_data[key]["total_expense"] = entry["total_expense"]

        # for entry in earning_qs:
        #     # key = (entry["created_at__year"], entry["created_at__month"])
        #     key = (entry["booking__pickup_date__year"], entry["booking__pickup_date__month"])
        #     monthly_data[key]["total_earned"] = entry["total_earned"]
        for item in booked_items:
            year = item["booking__pickup_date__year"]
            month = item["booking__pickup_date__month"]
            booking_id = item["booking__id"]
            discount = item["booking__discount_amount"] or 0
            total_items = booking_item_count_map.get(booking_id, 1)

            distributed_discount = discount / total_items if total_items > 0 else 0
            effective_amount = item["amount"] - distributed_discount

            key = (year, month)
            monthly_data[key]["total_earned"] += effective_amount

        result = [
            {
                "year": year,
                "month": month,
                "total_expense": monthly_data[(year, month)]["total_expense"],
                "total_earned": monthly_data[(year, month)]["total_earned"],
            }
            for (year, month) in sorted(monthly_data.keys())
        ]

        return self.success_response(
            data=result,
            message="Monthly summary retrieved successfully."
        )

    except Exception as e:
        logger.error(f"Monthly summary error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching. Please try again.",
            dev_message=str(e)
        )
