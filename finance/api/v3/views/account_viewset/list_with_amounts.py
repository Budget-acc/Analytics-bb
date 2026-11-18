import logging
from django.db import transaction
from django.db.models import Sum, Q, F, Case, When, DecimalField
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.serializers.account import AccountSerializer
from finance.models import Account
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


class AccountListWithAmountsMixin:
    """
    Mixin providing list_with_amounts action for AccountViewSet.
    Returns accounts with their current balances and total shop balance.
    """
    @skip_subscription_validation
    @extend_schema(
        tags=["Finance Management V3"],
        summary="List accounts with balances",
        description=(
            "Returns account list with current balances and shop total balance. "
            "Used in payment source selection and balance visibility screens."
        ),
        parameters=[
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="false",
                description="Optional boolean string. true for active-only; false includes active+inactive.",
            ),
            OpenApiParameter(
                name="include_zero_balance",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="true",
                description="Optional boolean string. false excludes zero-balance accounts.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Account balances retrieved successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact message:\n"
                    "- Invalid query parameters provided.\n"
                    "Developer detail:\n"
                    "- Boolean query params 'is_active' and 'include_zero_balance' must be 'true' or 'false'."
                )
            ),
            403: OpenApiResponse(
                description=(
                    "Permission errors. Exact messages:\n"
                    "- Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to retrieve account balances at this time."),
        },
        examples=[
            OpenApiExample(
                "Request example - include all accounts",
                description="GET /finance/v3/accounts/with-amounts/?is_active=false&include_zero_balance=true",
                value={
                    "is_active": "false",
                    "include_zero_balance": "true",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Account balances retrieved successfully.",
                    "data": {
                        "accounts": [
                            {
                                "id": 1,
                                "name": "Cash in Hand",
                                "account_type": "cash",
                                "is_default": True,
                                "is_active": True,
                                "current_balance": 2750.0,
                            }
                        ],
                        "total_balance": 2750.0,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid input response",
                value={
                    "status": "error",
                    "message": "Invalid query parameters provided.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={
                    "status": "permission_denied",
                    "message": "Access to this shop has been deactivated.",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to retrieve account balances at this time.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=False, methods=['get'], url_path='with-amounts')
    def list_with_amounts(self, request):
        """
        GET /finance/v3/accounts/with-amounts/
        List all non-deleted accounts for the active shop with their current balances.
        Includes total balance across all accounts.
        Ordered: cash before bank, default first within type, then alphabetical.
        Accessible by all roles.

        Query params:
            is_active (bool, default=false) — default includes active + inactive; pass ?is_active=true for active-only
            include_zero_balance (bool, default=true) — pass ?include_zero_balance=false to exclude accounts with zero balance

        Response includes:
            - accounts: List of accounts with balance field
            - total_balance: Sum of all account balances
            - account_count: Number of accounts returned
        """
        try:
            # Input validation with safe defaults
            is_active_param = request.query_params.get("is_active", "false").strip().lower()
            include_zero_balance_param = request.query_params.get("include_zero_balance", "true").strip().lower()

            # Parse boolean parameters safely
            try:
                active_only = is_active_param in ('true', '1', 'yes')
                include_zero_balance = include_zero_balance_param not in ('false', '0', 'no')
            except ValueError:
                return self.error_response(
                    message="Invalid query parameters provided.",
                    dev_message="Boolean query params 'is_active' and 'include_zero_balance' must be 'true' or 'false'.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            # Build base queryset with optimized queries
            # Use select_related for shop (though we already have shop_id)
            # Use annotate to calculate balance for each account
            base_queryset = Account.objects.filter(
                shop_id=self.shop_id,
                deleted__isnull=True,  # Only non-deleted accounts
            )
            if active_only:
                base_queryset = base_queryset.filter(is_active=True)

            accounts_queryset = (
                base_queryset
                .select_related('shop')  # Optimize shop relation
                .annotate(
                    # Calculate current balance as sum of signed_amount from all ledger entries
                    # signed_amount is positive for inflows, negative for outflows
                    current_balance=Sum(
                        'ledger_entries__signed_amount',
                        filter=Q(ledger_entries__deleted__isnull=True),  # Only non-deleted entries
                        default=0,
                        output_field=DecimalField(max_digits=12, decimal_places=2)
                    )
                )
                .order_by(
                    # Order by account_type (cash first, then bank)
                    Case(
                        When(account_type='cash', then=0),
                        When(account_type='bank', then=1),
                        default=2,
                        output_field=DecimalField(max_digits=1, decimal_places=0)
                    ),
                    # Within type, default accounts first
                    '-is_default',
                    # Then alphabetical by name
                    'name'
                )
            )

            # Apply zero balance filter if requested
            if not include_zero_balance:
                accounts_queryset = accounts_queryset.filter(current_balance__gt=0)

            # Execute query and get results
            # Use transaction.atomic for consistency (though this is a read operation)
            with transaction.atomic():
                accounts = list(accounts_queryset)  # Evaluate queryset

                # Calculate total balance across all accounts
                # This is more efficient than summing in Python since we already have annotated balances
                total_balance = sum(
                    account.current_balance for account in accounts
                    if hasattr(account, 'current_balance')
                ) or 0

            # Serialize accounts
            serializer = AccountSerializer(accounts, many=True)

            # Add balance to each account data
            accounts_data = []
            for account, serialized_data in zip(accounts, serializer.data):
                account_data = dict(serialized_data)
                account_data['current_balance'] = float(account.current_balance or 0)
                accounts_data.append(account_data)

            # Prepare response data
            response_data = {
                'accounts': accounts_data,
                'total_balance': float(total_balance),
            }

            logger.info(
                f"Account balances fetched successfully for shop {self.shop_id}. "
                f"Accounts: {len(accounts_data)}, Total Balance: {total_balance}"
            )

            return self.success_response(
                data=response_data,
                message="Account balances retrieved successfully.",
                dev_message=f"Retrieved {len(accounts_data)} accounts with balances for shop {self.shop_id}. Total balance: {total_balance}",
            )

        except Exception as e:
            logger.error(
                f"Error fetching account balances for shop {self.shop_id}: {str(e)}",
                exc_info=True
            )
            return self.error_response(
                message="Unable to retrieve account balances at this time.",
                dev_message=f"Unexpected error in list_with_amounts: {type(e).__name__}. Check logs for details.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )