import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from expenses_v2.models import ProductVariantExpense
from finance.api.v3.utils import get_available_balance_as_of
from services.models import ProductVariant
from shop.api.v3.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from .helpers import (
    resolve_account_and_payment_method,
    get_first_validation_error_message,
    resolve_category_and_type,
)
from ..serializers import (
    ProductVariantExpenseReadSerializer,
    ProductVariantExpensesWriteSerializer,
)
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


def _ledger_created_at(expense):
    if expense.expense_date_time:
        return expense.expense_date_time
    return timezone.make_aware(datetime.combine(expense.date, datetime.min.time()))


class ProductVariantExpensesViewSet(StandardResponseMixin, ViewSet):
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in ExpensesViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Your access to this shop has been deactivated.",
                "data": None
            })

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Create paid product-variant expense",
        description=(
            "Creates a paid product variant expense entry and ledger out entry. "
            "Ensures product variant belongs to the active shop."
        ),
        request=inline_serializer(
            name="ProductVariantExpenseCreateV3Request",
            fields={
                "product_variant": serializers.IntegerField(required=True, help_text="Product variant id."),
                "date": serializers.DateField(required=False, help_text="Format: YYYY-MM-DD or DD-MM-YYYY."),
                "expense_date_time": serializers.DateTimeField(required=False, allow_null=True),
                "expense": serializers.IntegerField(required=True, help_text="Paid amount."),
                "type": serializers.CharField(required=False, help_text="Expense category/type string. If a matching ShopCategory exists, category_id is set automatically."),
                "category": serializers.IntegerField(required=False, allow_null=True, help_text="ShopCategory id. If provided, type is overwritten with the category name."),
                "description": serializers.CharField(required=False, allow_blank=True),
                "account_id": serializers.IntegerField(required=False),
                "payment_method": serializers.CharField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Product variant expense created successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages include:\n"
                    "- Invalid product variant.\n"
                    "- Either account or payment method must be provided\n"
                    "- Invalid account\n"
                    "- Account is inactive\n"
                    "- No active cash account found for this shop.\n"
                    "- No active bank account found for this shop.\n"
                    "- Invalid payment method. Use cash or upi.\n"
                    "- Expense date is required.\n"
                    "- Insufficient balance in this account. Available balance: ₹<available>.\n"
                    "- Validation failed while creating product variant expense."
                )
            ),
            403: OpenApiResponse(description="Exact messages include:\n- Your access to this shop has been deactivated.\n- Invalid product variant."),
            500: OpenApiResponse(description="Exact message: Something went wrong while creating the product variant expense."),
        },
        examples=[
            OpenApiExample(
                "Create product variant paid expense",
                value={
                    "product_variant": 12,
                    "date": "2026-05-04",
                    "expense": 350,
                    "type": "stitching",
                    "description": "Alteration labor",
                    "payment_method": "upi",
                },
                request_only=True,
            )
        ],
    )
    def create(self, request):
        try:
            data = request.data.copy()
            data['shop'] = self.shop_id
            data['user'] = request.user.id

            try:
                variant = ProductVariant.objects.select_related(
                    "product__shop_service__shop"
                ).get(id=data.get("product_variant"))
            except ProductVariant.DoesNotExist:
                return self.validation_error_response(
                    errors={"product_variant": ["Invalid product variant."]},
                    message="Invalid product variant."
                )

            variant_shop_id = variant.product.shop_service.shop_id
            if variant_shop_id != self.shop_id:
                return self.permission_denied_response(
                    message="Invalid product variant."
                )

            account, payment_method, errors = resolve_account_and_payment_method(data, self.shop_id)
            if errors:
                message = get_first_validation_error_message(
                    errors,
                    'Validation failed while creating product variant expense.',
                )
                return self.validation_error_response(
                    errors=errors,
                    message=message,
                )

            category_errors = resolve_category_and_type(data, self.shop_id)
            if category_errors:
                return self.validation_error_response(
                    errors=category_errors,
                    message=get_first_validation_error_message(
                        category_errors,
                        'Please check your category details and try again.',
                    ),
                )

            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
            data.pop('account_id', None)
            data['account'] = account.id
            data['payment_method'] = payment_method

            serializer = ProductVariantExpensesWriteSerializer(data=data)
            if serializer.is_valid():
                with transaction.atomic():
                    # Lock the account and verify it can currently cover this
                    # expense before posting the outflow - same balance-check
                    # helper/locking pattern used by withdrawal creation and
                    # investment/manual-income deletion.
                    available = get_available_balance_as_of(
                        account.id, timezone.localdate(), lock_for_update=True
                    )
                    try:
                        expense_amount = Decimal(str(data.get('expense')))
                    except (TypeError, ValueError, InvalidOperation):
                        expense_amount = None
                    if expense_amount is not None and expense_amount > available:
                        message = (
                            f'Insufficient balance in this account. '
                            f'Available balance: ₹{available}.'
                        )
                        return self.validation_error_response(
                            errors={'expense': [message]},
                            message=message,
                        )

                    variant_expense = serializer.save()
                    _create_ledger_entries(
                        payments=[{
                            'amount': variant_expense.expense,
                            'direction': 'out',
                            'account': account,
                            'payment_method': payment_method,
                            'reference_id': variant_expense.id,
                            'created_at': _ledger_created_at(variant_expense),
                        }],
                        shop=variant_expense.shop,
                        transaction_type='product_expense',
                        reference_type='ProductVariantExpense',
                    )

                return self.success_response(
                    data=ProductVariantExpenseReadSerializer(variant_expense).data,
                    message="Product variant expense created successfully."
                )

            return self.validation_error_response(
                errors=serializer.errors,
                message=get_first_validation_error_message(
                    serializer.errors,
                    'Validation failed while creating product variant expense.',
                ),
                dev_message=f"Serializer errors: {serializer.errors}"
            )

        except Exception as e:
            logger.error(f"ProductVariantExpense create error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while creating the product variant expense.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="List paid product-variant expenses",
        description=(
            "Returns paginated paid expense entries for a specific product variant."
        ),
        parameters=[
            OpenApiParameter(name="product_variant", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=True, description="Product variant id."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={
            200: OpenApiResponse(description="Paginated product variant expense list."),
            400: OpenApiResponse(description="Exact message: Product is required."),
            403: OpenApiResponse(description="Exact messages include:\n- Your access to this shop has been deactivated.\n- Product variant not found for this shop."),
            404: OpenApiResponse(description="Exact message: Product variant not found."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching product variant expenses."),
        },
    )
    @skip_subscription_validation
    def list(self, request):
        try:
            variant_id = request.query_params.get("product_variant")
            if not variant_id:
                return self.validation_error_response(
                    errors={"product_variant": ["product_variant query parameter is required."]},
                    message="Product is required."
                )

            shop_id = self.shop_id

            try:
                variant = ProductVariant.objects.select_related(
                    "product__shop_service__shop"
                ).get(id=variant_id)
            except ProductVariant.DoesNotExist:
                return self.not_found_response(
                    resource="ProductVariant",
                    message="Product variant not found.",
                    dev_message=f"ProductVariant with id {variant_id} not found for shop {shop_id}."
                )

            if variant.product.shop_service.shop_id != shop_id:
                return self.permission_denied_response(
                    message="Product variant not found for this shop."
                )

            expenses = (
                ProductVariantExpense.objects
                .filter(product_variant=variant, shop_id=shop_id)
                .select_related('account', 'category', 'product_variant__product__shop_service__shop')
                .order_by("-created_at")
            )

            paginator = StandardPageNumberPagination()
            paginated_qs = paginator.paginate_queryset(expenses, request)
            serializer = ProductVariantExpenseReadSerializer(paginated_qs, many=True)

            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            logger.error(f"ProductVariantExpense list error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching product variant expenses.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Retrieve paid product-variant expense",
        description="Fetches a product variant expense entry by id.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Product variant expense id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Product variant expense retrieved successfully."),
            403: OpenApiResponse(description="Exact messages include:\n- Your access to this shop has been deactivated.\n- Expense not found for this shop."),
            404: OpenApiResponse(description="Exact message: Expense not found."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching the product variant expense."),
        },
    )
    @skip_subscription_validation
    def retrieve(self, request, pk=None):
        try:
            shop_id = self.shop_id
            try:
                expense = ProductVariantExpense.objects.select_related(
                    'account',
                    'category',
                    "product_variant__product__shop_service__shop"
                ).get(id=pk, shop_id=shop_id)
            except ProductVariantExpense.DoesNotExist:
                return self.not_found_response(
                    resource="ProductVariantExpense",
                    message="Expense not found.",
                    dev_message=f"ProductVariantExpense with id {pk} not found for shop {shop_id}."
                )

            if expense.product_variant.product.shop_service.shop_id != shop_id:
                return self.permission_denied_response(
                    message="Expense not found for this shop."
                )

            serializer = ProductVariantExpenseReadSerializer(expense)
            return self.success_response(
                data=serializer.data,
                message="Product variant expense retrieved successfully."
            )

        except Exception as e:
            logger.error(f"ProductVariantExpense retrieve error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching the product variant expense.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Update paid product-variant expense",
        description="Partially updates a product variant expense and rewrites ledger out entry.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Product variant expense id."),
        ],
        request=inline_serializer(
            name="ProductVariantExpensePartialUpdateV3Request",
            fields={
                "date": serializers.DateField(required=False),
                "expense_date_time": serializers.DateTimeField(required=False, allow_null=True),
                "expense": serializers.IntegerField(required=False),
                "type": serializers.CharField(required=False, help_text="Expense category/type string. If a matching ShopCategory exists, category_id is set automatically."),
                "category": serializers.IntegerField(required=False, allow_null=True, help_text="ShopCategory id. If provided, type is overwritten with the category name."),
                "description": serializers.CharField(required=False, allow_blank=True),
                "account_id": serializers.IntegerField(required=False),
                "payment_method": serializers.CharField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Product variant expense updated successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages include:\n"
                    "- Either account or payment method must be provided\n"
                    "- Invalid account\n"
                    "- Account is inactive\n"
                    "- No active cash account found for this shop.\n"
                    "- No active bank account found for this shop.\n"
                    "- Invalid payment method. Use cash or upi.\n"
                    "- Expense date is required.\n"
                    "- Insufficient balance in this account as of <DD-MM-YYYY>. Available balance: ₹<available>.\n"
                    "- Validation failed while updating product variant expense."
                )
            ),
            403: OpenApiResponse(description="Exact messages include:\n- Your access to this shop has been deactivated.\n- Expense not found."),
            404: OpenApiResponse(description="Exact message: Expense not found."),
            500: OpenApiResponse(description="Exact message: Something went wrong while updating the product variant expense."),
        },
    )
    def partial_update(self, request, pk=None):
        try:
            shop_id = self.shop_id

            try:
                expense = ProductVariantExpense.objects.select_related(
                    "product_variant__product__shop_service__shop"
                ).get(id=pk, shop_id=shop_id)
            except ProductVariantExpense.DoesNotExist:
                return self.not_found_response(
                    resource="ProductVariantExpense",
                    message="Expense not found.",
                    dev_message=f"ProductVariantExpense with id {pk} not found for shop {shop_id}."
                )

            if expense.product_variant.product.shop_service.shop_id != shop_id:
                return self.permission_denied_response(
                    message="Expense not found."
                )


            data = request.data.copy()

            # Preserve existing date values when omitted from the PATCH body
            if 'date' not in data and 'expense_date_time' not in data:
                if expense.expense_date_time:
                    data['expense_date_time'] = expense.expense_date_time
                elif expense.date:
                    data['date'] = expense.date

            # Preserve existing account/payment when omitted from PATCH body.
            # Validate incoming values only when client explicitly sends them.
            if 'account_id' not in data and 'payment_method' not in data:
                if expense.account_id:
                    data['account_id'] = expense.account_id
                elif expense.payment_method:
                    data['payment_method'] = expense.payment_method

            account, payment_method, errors = resolve_account_and_payment_method(data, self.shop_id)
            if errors:
                message = get_first_validation_error_message(
                    errors,
                    'Validation failed while updating product variant expense.',
                )
                return self.validation_error_response(
                    errors=errors,
                    message=message,
                )

            category_errors = resolve_category_and_type(data, self.shop_id)
            if category_errors:
                return self.validation_error_response(
                    errors=category_errors,
                    message=get_first_validation_error_message(
                        category_errors,
                        'Please check your category details and try again.',
                    ),
                )

            from finance.api.v3.utils import (
                create_ledger_entries as _create_ledger_entries,
                delete_ledger_entries as _delete_ledger_entries,
            )

            data.pop('account_id', None)
            data['account'] = account.id
            data['payment_method'] = payment_method

            serializer = ProductVariantExpensesWriteSerializer(expense, data=data, partial=True)
            if serializer.is_valid():
                with transaction.atomic():
                    # Verify the account can cover the updated outflow as of
                    # the expense date, excluding this expense's own existing
                    # ledger entry - same pattern as withdrawal update.
                    validated = serializer.validated_data
                    as_of_date = timezone.localtime(validated['expense_date_time']).date()
                    available = get_available_balance_as_of(
                        account.id, as_of_date,
                        exclude_reference=('ProductVariantExpense', expense.id),
                        lock_for_update=True,
                    )
                    expense_amount = Decimal(str(validated.get('expense', expense.expense)))
                    if expense_amount > available:
                        date_label = as_of_date.strftime('%d-%m-%Y')
                        message = (
                            f'Insufficient balance in this account as of {date_label}. '
                            f'Available balance: ₹{available}.'
                        )
                        return self.validation_error_response(
                            errors={'expense': [message]},
                            message=message,
                        )

                    _delete_ledger_entries(
                        reference_ids=[expense.id],
                        reference_type='ProductVariantExpense',
                    )
                    updated_expense = serializer.save()
                    _create_ledger_entries(
                        payments=[{
                            'amount': updated_expense.expense,
                            'direction': 'out',
                            'account': account,
                            'payment_method': payment_method,
                            'reference_id': updated_expense.id,
                            'created_at': _ledger_created_at(updated_expense),
                        }],
                        shop=updated_expense.shop,
                        transaction_type='product_expense',
                        reference_type='ProductVariantExpense',
                    )

                return self.success_response(
                    data=ProductVariantExpenseReadSerializer(updated_expense).data,
                    message="Product variant expense updated successfully."
                )

            return self.validation_error_response(
                errors=serializer.errors,
                message=get_first_validation_error_message(
                    serializer.errors,
                    'Validation failed while updating product variant expense.',
                ),
                dev_message=f"Serializer errors: {serializer.errors}"
            )

        except Exception as e:
            logger.error(f"ProductVariantExpense update error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while updating the product variant expense.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Delete paid product-variant expense",
        description="Deletes a product variant expense entry and corresponding ledger data.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Product variant expense id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Expense deleted successfully."),
            403: OpenApiResponse(description="Exact messages include:\n- Your access to this shop has been deactivated.\n- Expense not found for this shop."),
            404: OpenApiResponse(description="Exact message: Expense not found."),
            500: OpenApiResponse(description="Exact message: Something went wrong while deleting the product variant expense."),
        },
    )
    def destroy(self, request, pk=None):
        try:
            shop_id = self.shop_id
            expense = ProductVariantExpense.objects.select_related(
                "product_variant__product__shop_service__shop"
            ).get(id=pk, shop_id=shop_id)

            if expense.product_variant.product.shop_service.shop_id != shop_id:
                return self.permission_denied_response(
                    message="Expense not found for this shop."
                )

            from finance.api.v3.utils import (
                delete_ledger_entries as _delete_ledger_entries,
                update_daily_balance as _update_daily_balance,
            )
            _delete_ledger_entries(
                reference_ids=[expense.id],
                reference_type='ProductVariantExpense',
            )

            # Explicitly update the correct date's balance using expense.date.
            # delete_ledger_entries uses LedgerEntry.created_at which may differ
            # from expense.date for legacy entries.
            if expense.account_id:
                _update_daily_balance(
                    shop=expense.shop_id,
                    account=expense.account_id,
                    date=expense.date,
                )

            expense.delete()
            return self.success_response(
                message="Expense deleted successfully.",
                data=None
            )

        except ProductVariantExpense.DoesNotExist:
            return self.not_found_response(
                resource="ProductVariantExpense",
                message="Expense not found.",
                dev_message=f"ProductVariantExpense with id {pk} not found for shop {shop_id}."
            )
        except Exception as e:
            logger.error(f"ProductVariantExpense delete error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while deleting the product variant expense.",
                dev_message=str(e)
            )
