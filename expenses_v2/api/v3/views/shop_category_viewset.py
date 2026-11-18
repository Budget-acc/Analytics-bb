import logging

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers

from django.db import IntegrityError, transaction
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from expenses_v2.api.v3.views.helpers.category_defaults import CATEGORY_DEFINITIONS
from expenses_v2.models import ShopCategory
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
from ..serializers import ShopCategoryReadSerializer, ShopCategoryWriteSerializer

logger = logging.getLogger("api_requests")


class ShopCategoryViewSet(StandardResponseMixin, ViewSet):
    permission_classes = [IsAuthenticated, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(
                f"Shop permission denied in ShopCategoryViewSet: {str(e)}",
                exc_info=True,
            )
            raise PermissionDenied(
                {
                    "status": "permission_denied",
                    "message": "Your access to this shop has been deactivated.",
                    "data": None,
                }
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="List shop categories",
        description=(
            "Returns categories for the active shop, ordered alphabetically by name. "
            "Pass `type=income` to get Income and Income & Expense categories. "
            "Pass `type=expense` to get Expense and Income & Expense categories. "
            "Omit `type` to return all categories."
        ),
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by usage type. Allowed values: `income`, `expense`.",
                enum=["income", "expense"],
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="List of shop categories.",
                response=inline_serializer(
                    name="ShopCategoryListResponse",
                    fields={
                        "id": serializers.IntegerField(),
                        "name": serializers.CharField(),
                        "type": serializers.ChoiceField(choices=["income", "expense", "income_and_expense"]),
                        "is_active": serializers.BooleanField(),
                        "created_at": serializers.DateTimeField(),
                    },
                ),
            ),
            400: OpenApiResponse(
                description="Exact message: Invalid type filter. Must be one of: income, expense."
            ),
            403: OpenApiResponse(
                description="Exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Something went wrong while fetching shop categories."
            ),
        },
        examples=[
            OpenApiExample(
                "List response",
                value=[
                    {
                        "id": 1,
                        "name": "Transport",
                        "type": "expense",
                        "is_active": True,
                        "created_at": "2026-05-16T10:00:00Z",
                    },
                    {
                        "id": 2,
                        "name": "Rental Income",
                        "type": "income",
                        "is_active": True,
                        "created_at": "2026-05-16T11:00:00Z",
                    },
                ],
                response_only=True,
            )
        ],
    )
    @skip_subscription_validation
    def list(self, request):
        try:
            type_filter = request.query_params.get("type")
            valid_type_filters = ["income", "expense"]

            queryset = ShopCategory.objects.filter(shop_id=self.shop_id)
            if not queryset.exists():
                self._ensure_default_categories()
                queryset = ShopCategory.objects.filter(shop_id=self.shop_id)

            if type_filter:
                if type_filter not in valid_type_filters:
                    return self.validation_error_response(
                        errors={"type": ["Must be one of: income, expense."]},
                        message="Invalid type filter. Must be one of: income, expense.",
                    )
                queryset = queryset.filter(type__in=[type_filter, "income_and_expense"])

            categories = queryset.order_by("name")
            serializer = ShopCategoryReadSerializer(categories, many=True)
            return self.success_response(
                data=serializer.data,
                message="Shop categories fetched successfully.",
            )
        except Exception as e:
            logger.error(f"ShopCategory list error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching shop categories.",
                dev_message=str(e),
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Create shop category",
        description=(
            "Creates a new category for the active shop. "
            "Category names are **case-insensitive unique** per shop — "
            "submitting 'Transport' when 'transport' already exists will return a 400. "
            "Leading/trailing whitespace is stripped automatically. "
            "Categories created here are reusable across expenses, income, and other financial modules."
        ),
        request=inline_serializer(
            name="ShopCategoryCreateRequest",
            fields={
                "name": serializers.CharField(
                    required=True,
                    help_text="Category name. Must be unique (case-insensitive) within the shop. Max 100 characters.",
                ),
                "type": serializers.ChoiceField(
                    choices=["income", "expense", "income_and_expense"],
                    help_text="Category usage type. Must be one of: income, expense, income_and_expense.",
                ),
                "is_active": serializers.BooleanField(
                    required=False,
                    help_text="Whether the category is active. Defaults to true.",
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                description="Exact message: Shop category created successfully.",
                response=inline_serializer(
                    name="ShopCategoryCreateResponse",
                    fields={
                        "id": serializers.IntegerField(),
                        "name": serializers.CharField(),
                        "type": serializers.ChoiceField(choices=["income", "expense", "income_and_expense"]),
                        "is_active": serializers.BooleanField(),
                        "created_at": serializers.DateTimeField(),
                    },
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages include:\n"
                    "- Category name cannot be blank.\n"
                    "- A category with this name already exists for this shop.\n"
                    "- Invalid category type. Must be one of: income, expense, income_and_expense.\n"
                    "- Please check your category details and try again."
                )
            ),
            403: OpenApiResponse(
                description="Exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Something went wrong while creating the shop category."
            ),
        },
        examples=[
            OpenApiExample(
                "Create expense category",
                value={"name": "Transport", "type": "expense"},
                request_only=True,
            ),
            OpenApiExample(
                "Create shared category",
                value={"name": "Utilities", "type": "income_and_expense", "is_active": True},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "id": 3,
                    "name": "Transport",
                    "type": "expense",
                    "is_active": True,
                    "created_at": "2026-05-16T12:00:00Z",
                },
                response_only=True,
            ),
        ],
    )
    def _ensure_default_categories(self):
        if not CATEGORY_DEFINITIONS:
            return

        try:
            with transaction.atomic():
                for category_type, names in CATEGORY_DEFINITIONS.items():
                    for name in names:
                        ShopCategory.objects.get_or_create(
                            shop_id=self.shop_id,
                            name=name,
                            defaults={"type": category_type},
                        )
        except IntegrityError:
            # Another request created categories concurrently.
            pass

    def create(self, request):
        try:
            data = request.data.copy()
            data["shop"] = self.shop_id

            serializer = ShopCategoryWriteSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return self.success_response(
                    message="Shop category created successfully.",
                )

            errors = serializer.errors
            first_error = next(
                (
                    msgs[0] if isinstance(msgs, list) else str(msgs)
                    for msgs in errors.values()
                ),
                "Please check your category details and try again.",
            )
            return self.validation_error_response(
                errors=errors,
                message=first_error,
            )
        except Exception as e:
            logger.error(f"ShopCategory create error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while creating the shop category.",
                dev_message=str(e),
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Update shop category",
        description=(
            "Partially updates a shop category (name, is_active, or both). "
            "When updating `name`, the same case-insensitive uniqueness rule applies — "
            "the current category is excluded from the duplicate check. "
            "Omit any field you do not want to change."
        ),
        parameters=[
            OpenApiParameter(
                name="pk",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="ID of the shop category to update.",
            ),
        ],
        request=inline_serializer(
            name="ShopCategoryUpdateRequest",
            fields={
                "name": serializers.CharField(
                    required=False,
                    help_text="New category name. Must be unique (case-insensitive) within the shop. Max 100 characters.",
                ),
                "type": serializers.ChoiceField(
                    choices=["income", "expense", "income_and_expense"],
                    required=False,
                    help_text="Category usage type. Must be one of: income, expense, income_and_expense.",
                ),
                "is_active": serializers.BooleanField(
                    required=False,
                    help_text="Set to false to deactivate the category without deleting it.",
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                description="Exact message: Shop category updated successfully.",
                response=inline_serializer(
                    name="ShopCategoryUpdateResponse",
                    fields={
                        "id": serializers.IntegerField(),
                        "name": serializers.CharField(),
                        "type": serializers.ChoiceField(choices=["income", "expense", "income_and_expense"]),
                        "is_active": serializers.BooleanField(),
                        "created_at": serializers.DateTimeField(),
                    },
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages include:\n"
                    "- Category name cannot be blank.\n"
                    "- A category with this name already exists for this shop.\n"
                    "- Invalid category type. Must be one of: income, expense, income_and_expense.\n"
                    "- Please check your category details and try again."
                )
            ),
            404: OpenApiResponse(description="Exact message: Shop category not found."),
            403: OpenApiResponse(
                description="Exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Something went wrong while updating the shop category."
            ),
        },
        examples=[
            OpenApiExample(
                "Rename category",
                value={"name": "Fuel & Transport"},
                request_only=True,
            ),
            OpenApiExample(
                "Change type",
                value={"type": "expense"},
                request_only=True,
            ),
            OpenApiExample(
                "Deactivate category",
                value={"is_active": False},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "id": 3,
                    "name": "Fuel & Transport",
                    "type": "expense",
                    "is_active": True,
                    "created_at": "2026-05-16T12:00:00Z",
                },
                response_only=True,
            ),
        ],
    )
    def partial_update(self, request, pk=None):
        try:
            try:
                category = ShopCategory.objects.get(pk=pk, shop_id=self.shop_id)
            except ShopCategory.DoesNotExist:
                return self.not_found_response(
                    resource="ShopCategory",
                    message="Shop category not found.",
                    dev_message=f"ShopCategory with id {pk} not found for shop {self.shop_id}.",
                )

            serializer = ShopCategoryWriteSerializer(
                category, data=request.data, partial=True
            )
            if serializer.is_valid():
                serializer.save()
                return self.success_response(
                    message="Shop category updated successfully.",
                )

            errors = serializer.errors
            first_error = next(
                (
                    msgs[0] if isinstance(msgs, list) else str(msgs)
                    for msgs in errors.values()
                ),
                "Please check your category details and try again.",
            )
            return self.validation_error_response(
                errors=errors,
                message=first_error,
            )
        except Exception as e:
            logger.error(f"ShopCategory update error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while updating the shop category.",
                dev_message=str(e),
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Delete shop category",
        description=(
            "Permanently deletes a shop category. "
            "This does **not** affect existing expenses or income records that used this category — "
            "the `type` string on those records remains unchanged. "
            "Use PATCH with `is_active: false` if you want to hide a category without deleting it."
        ),
        parameters=[
            OpenApiParameter(
                name="pk",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="ID of the shop category to delete.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Exact message: Shop category deleted successfully."
            ),
            404: OpenApiResponse(description="Exact message: Shop category not found."),
            403: OpenApiResponse(
                description="Exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Something went wrong while deleting the shop category."
            ),
        },
    )
    def destroy(self, request, pk=None):
        try:
            try:
                category = ShopCategory.objects.get(pk=pk, shop_id=self.shop_id)
            except ShopCategory.DoesNotExist:
                return self.not_found_response(
                    resource="ShopCategory",
                    message="Shop category not found.",
                    dev_message=f"ShopCategory with id {pk} not found for shop {self.shop_id}.",
                )

            category.delete()
            return self.success_response(
                data=None,
                message="Shop category deleted successfully.",
            )
        except Exception as e:
            logger.error(f"ShopCategory delete error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while deleting the shop category.",
                dev_message=str(e),
            )
