import logging

from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from expenses_v2.models import Expenses, ProductVariantExpense
from shop.models import ShopUser
from shop.api.v3.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiResponse, extend_schema

logger = logging.getLogger("api_requests")


class ShopExpenseAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Get wallet paid expenses feed",
        description=(
            "Returns a combined paginated feed of general and product-variant paid expenses for wallet outflow screens. "
            "This is the primary paid-expense consumption API in Expense V3."
        ),
        responses={
            200: OpenApiResponse(description="Paginated combined expense feed."),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching shop expenses."),
        },
    )
    def get(self, request):
        try:
            try:
                shop_id = get_active_shop_id(request)
            except Exception as e:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated.",
                )

            owner_user = (
                ShopUser.objects
                .filter(shop_id=shop_id, role="OWNER", is_active=True)
                .select_related("user")
                .first()
            )
            owner_name = owner_user.user.full_name if owner_user and owner_user.user else None

            # General Expenses
            general_expenses = Expenses.objects.filter(shop_id=shop_id).select_related("user")
            general_data = [
                {
                    "id": exp.id,
                    "variant_id": None,
                    "product_id": None,
                    "service_id": None,
                    "sort_date": localtime(exp.expense_date_time).date() if exp.expense_date_time else exp.date,
                    "sort_created": exp.created_at,
                    "date": localtime(exp.expense_date_time).date() if exp.expense_date_time else exp.date,
                    "created_at": exp.created_at,
                    "user": exp.user.full_name if exp.user else owner_name,
                    "expense": exp.expense,
                    "type": exp.type,
                    "description": exp.description,
                    "is_deleted": False,
                }
                for exp in general_expenses
            ]

            # Product Variant Expenses
            variant_expenses = ProductVariantExpense.objects.filter(shop_id=shop_id).select_related(
                "linked_expense",
                "product_variant",
                "shop",
                "product_variant__product",
                "product_variant__product__shop_service"
            )
            variant_data = [
                {
                    "id": exp.id,
                    "variant_id": exp.product_variant.id if exp.product_variant else None,
                    "product_id": exp.product_variant.product.id if exp.product_variant and exp.product_variant.product else None,
                    "service_id": (
                        exp.product_variant.product.shop_service.id
                        if exp.product_variant and exp.product_variant.product and exp.product_variant.product.shop_service
                        else None
                    ),
                    "sort_date": localtime(exp.expense_date_time).date() if exp.expense_date_time else exp.date,
                    "sort_created": exp.created_at,
                    "date": localtime(exp.expense_date_time).date() if exp.expense_date_time else exp.date,
                    "created_at": exp.created_at,
                    "user": (
                        exp.linked_expense.user.full_name if exp.linked_expense and exp.linked_expense.user
                        else (exp.user.full_name if exp.user else owner_name)
                    ),
                    "expense": exp.expense,
                    "type": exp.type,
                    "description": exp.description,
                    "is_deleted": True if not exp.product_variant or not exp.product_variant.product else False,
                }
                for exp in variant_expenses
            ]

            # Combine and sort
            all_expenses = sorted(
                general_data + variant_data,
                key=lambda x: (x["sort_date"], x["sort_created"]),
                reverse=True
            )

            # Format date fields for output
            for item in all_expenses:
                item["date"] = item["date"].strftime("%d-%m-%Y") if item["date"] else None
                item["created_at"] = localtime(item["created_at"]).strftime("%d-%m-%Y %H:%M:%S") if item["created_at"] else None
                del item["sort_date"]
                del item["sort_created"]

            # Paginate
            paginator = StandardPageNumberPagination()
            paginated_expenses = paginator.paginate_queryset(all_expenses, request)
            return paginator.get_paginated_response(paginated_expenses)

        except Exception as e:
            logger.error(f"ShopExpenseAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching shop expenses.",
                dev_message=str(e)
            )
