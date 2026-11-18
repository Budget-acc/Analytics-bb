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
from expenses_v2.models import Expenses
from finance.api.v3.utils import get_available_balance_as_of
from shop.api.v3.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from .helpers import (
    resolve_account_and_payment_method,
    get_first_validation_error_message,
    resolve_category_and_type,
)
from ..serializers import ExpensesReadSerializer, ExpensesWriteSerializer
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


def _ledger_created_at(expense):
    if expense.expense_date_time:
        return expense.expense_date_time
    return timezone.make_aware(datetime.combine(expense.date, datetime.min.time()))


class ExpensesViewSet(StandardResponseMixin, ViewSet):
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

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return ExpensesReadSerializer
        return ExpensesWriteSerializer

    @extend_schema(
        tags=["Expense Management V3"],
        summary="List paid expenses",
        description=(
            "Returns paginated general paid expense entries for the active shop. "
            "Use this in expense ledger and paid-outflow screens."
        ),
        parameters=[
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
            OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Page size."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated expense list response."),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching expenses."),
        },
    )
    @skip_subscription_validation    
    def list(self, request):
        try:
            expenses = (
                Expenses.objects
                .filter(shop_id=self.shop_id)
                .select_related('account', 'category')
                .order_by('-created_at')
            )

            paginator = StandardPageNumberPagination()
            result_page = paginator.paginate_queryset(expenses, request)
            serializer = ExpensesReadSerializer(result_page, many=True)

            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            logger.error(f"Expenses list error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching expenses.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Retrieve paid expense",
        description="Fetches a single paid expense entry by id for the active shop.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Expense id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Expense retrieved successfully."),
            404: OpenApiResponse(description="Exact message: Expense not found."),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching the expense."),
        },
    )
    @skip_subscription_validation
    def retrieve(self, request, pk=None):
        try:
            expense = Expenses.objects.select_related('account', 'category').get(pk=pk, shop_id=self.shop_id)
            serializer = ExpensesReadSerializer(expense)
            return self.success_response(
                data=serializer.data,
                message="Expense retrieved successfully."
            )
        except Expenses.DoesNotExist:
            return self.not_found_response(
                resource="Expense",
                message="Expense not found.",
                dev_message=f"Expense with id {pk} not found for shop {self.shop_id}."
            )
        except Exception as e:
            logger.error(f"Expense retrieve error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching the expense.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Create paid expense entry",
        description=(
            "Creates a paid general expense and writes corresponding ledger out entry. "
            "Payment source can be resolved from account_id or payment_method."
        ),
        request=inline_serializer(
            name="ExpenseCreateV3Request",
            fields={
                "date": serializers.DateField(required=False, help_text="Format: YYYY-MM-DD or DD-MM-YYYY."),
                "expense_date_time": serializers.DateTimeField(required=False, allow_null=True, help_text="Optional datetime of expense."),
                "expense": serializers.IntegerField(required=True, help_text="Paid amount."),
                "type": serializers.CharField(required=False, help_text="Expense category/type string. If a matching ShopCategory exists, category_id is set automatically."),
                "category": serializers.IntegerField(required=False, allow_null=True, help_text="ShopCategory id. If provided, type is overwritten with the category name."),
                "description": serializers.CharField(required=False, allow_blank=True),
                "account_id": serializers.IntegerField(required=False, help_text="Preferred payment account id."),
                "payment_method": serializers.CharField(required=False, help_text="cash, upi, gpay, or bank."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Expense created successfully."),
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
                    "- Insufficient balance in this account. Available balance: ₹<available>.\n"
                    "- Please check your expense details and try again."
                )
            ),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while creating the expense."),
        },
        examples=[
            OpenApiExample(
                "Create paid expense",
                value={
                    "date": "04-05-2026",
                    "expense": 1200,
                    "type": "transport",
                    "description": "Delivery fuel",
                    "payment_method": "cash",
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

            account, payment_method, errors = resolve_account_and_payment_method(data, self.shop_id)
            if errors:
                message = get_first_validation_error_message(
                    errors,
                    'Please check your expense details and try again.',
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

            write_serializer = ExpensesWriteSerializer(data=data)
            if write_serializer.is_valid():
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

                    expense = write_serializer.save()
                    _create_ledger_entries(
                        payments=[{
                            'amount': expense.expense,
                            'direction': 'out',
                            'account': account,
                            'payment_method': payment_method,
                            'reference_id': expense.id,
                            'created_at': _ledger_created_at(expense),
                        }],
                        shop=expense.shop,
                        transaction_type='expense',
                        reference_type='Expenses',
                    )

                read_serializer = ExpensesReadSerializer(expense)
                return self.success_response(
                    data=read_serializer.data,
                    message="Expense created successfully."
                )

            return self.validation_error_response(
                errors=write_serializer.errors,
                message=get_first_validation_error_message(
                    write_serializer.errors,
                    'Please check your expense details and try again.',
                ),
            )

        except Exception as e:
            logger.error(f"Expense create error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while creating the expense.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Update paid expense entry",
        description=(
            "Partially updates an expense entry and rewrites ledger entries with updated amount/account/method."
        ),
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Expense id."),
        ],
        request=inline_serializer(
            name="ExpensePartialUpdateV3Request",
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
            200: OpenApiResponse(description="Exact message: Expense updated successfully."),
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
                    "- Please check your details and try again."
                )
            ),
            404: OpenApiResponse(description="Exact message: Expense not found."),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while updating the expense."),
        },
    )
    def partial_update(self, request, pk=None):
        try:
            expense = Expenses.objects.get(pk=pk, shop_id=self.shop_id)

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
                    'Please check your details and try again.',
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

            write_serializer = ExpensesWriteSerializer(expense, data=data, partial=True)
            if write_serializer.is_valid():
                with transaction.atomic():
                    # Verify the account can cover the updated outflow as of
                    # the expense date, excluding this expense's own existing
                    # ledger entry - same pattern as withdrawal update.
                    validated = write_serializer.validated_data
                    as_of_date = timezone.localtime(validated['expense_date_time']).date()
                    available = get_available_balance_as_of(
                        account.id, as_of_date,
                        exclude_reference=('Expenses', expense.id),
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
                        reference_type='Expenses',
                    )
                    updated_expense = write_serializer.save()
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
                        transaction_type='expense',
                        reference_type='Expenses',
                    )

                read_serializer = ExpensesReadSerializer(updated_expense)
                return self.success_response(
                    data=read_serializer.data,
                    message="Expense updated successfully."
                )

            return self.validation_error_response(
                errors=write_serializer.errors,
                message=get_first_validation_error_message(
                    write_serializer.errors,
                    'Please check your details and try again.',
                ),
            )

        except Expenses.DoesNotExist:
            return self.not_found_response(
                resource="Expense",
                message="Expense not found.",
                dev_message=f"Expense with id {pk} not found for shop {self.shop_id}."
            )
        except Exception as e:
            logger.error(f"Expense update error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while updating the expense.",
                dev_message=str(e)
            )

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Delete paid expense entry",
        description="Deletes an expense entry and removes associated ledger out entries.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Expense id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Expense deleted successfully."),
            404: OpenApiResponse(description="Exact message: Expense not found."),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while deleting the expense."),
        },
    )
    def destroy(self, request, pk=None):
        try:
            expense = Expenses.objects.get(pk=pk, shop_id=self.shop_id)

            from finance.api.v3.utils import (
                delete_ledger_entries as _delete_ledger_entries,
                update_daily_balance as _update_daily_balance,
            )
            _delete_ledger_entries(
                reference_ids=[expense.id],
                reference_type='Expenses',
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
        except Expenses.DoesNotExist:
            return self.not_found_response(
                resource="Expense",
                message="Expense not found.",
                dev_message=f"Expense with id {pk} not found for shop {self.shop_id}."
            )
        except Exception as e:
            logger.error(f"Expense delete error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while deleting the expense.",
                dev_message=str(e)
            )
