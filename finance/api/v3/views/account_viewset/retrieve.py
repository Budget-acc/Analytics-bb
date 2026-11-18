import logging
from decimal import Decimal

from django.db.models import Q, Sum
from rest_framework.exceptions import NotFound, ValidationError

from finance.api.v3.serializers import AccountSerializer, LedgerEntryHistorySerializer
from finance.api.v3.utils.account_history import get_account_history_data
from finance.api.v3.utils.reference_id_resolver import resolve_ledger_reference_ids
from finance.api.v3.views.finance_transactions.helpers import parse_date
from finance.models import Account, LedgerEntry
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class AccountRetrieveMixin:

    @extend_schema(
        tags=["Finance Management V3"],
        summary="Retrieve account",
        description=(
            "Fetches one account by id for the active shop, including its current balance and "
            "a paginated transaction history (bank statement style), ordered chronologically "
            "from the oldest transaction to the most recent, so balance_after progresses from "
            "the account's initial balance up to its current balance. "
            "Optionally scope the history to a date range with start_date/end_date: if only "
            "start_date is given, all transactions from that date onward are returned; if only "
            "end_date is given, all transactions up to and including that date are returned; if "
            "both are given, the range is inclusive on both ends. When start_date is given, the "
            "running balance does NOT restart at zero - it opens from the real account balance "
            "as of just before start_date (the 'opening balance'), so balance_after values stay "
            "historically accurate. account_balance in the response then reflects the balance as "
            "of the end of the filtered range, not necessarily the account's live current balance. "
            "\n\n"
            "Each entry in `transactions` has a `transaction_type` (raw code) and a "
            "`transaction_type_display` (human-readable label). Possible `transaction_type` "
            "values are:\n"
            "- `booking_payment` - Booking Payment\n"
            "- `sale_payment` - Sale Payment\n"
            "- `expense` - Expense\n"
            "- `product_expense` - Product Expense\n"
            "- `security_in` - Security Deposit In of booking\n"
            "- `security_out` - Security Refund of booking\n"
            "- `refund` - Booking Refund\n"
            "- `transfer_in` - Account Transfer In\n"
            "- `transfer_out` - Account Transfer Out\n"
            "- `tailor_order_payment` - Tailor Order Payment\n"
            "- `tailor_order_security_in` - Tailor Security Deposit In\n"
            "- `tailor_security_refund` - Tailor Security Deposit Out\n"
            "- `tailor_order_refund` - Tailor Order Refund\n"
            "- `tailor_payment` - Tailor Payment\n"
            "- `manual_income` - Manual Income\n"
            "- `investment` - Investment\n"
            "- `withdrawal` - Withdrawal\n"
            "\n"
            "`direction` is `credit` (money in) or `debit` (money out).\n"
            "\n"
            "`reference_id` always points at the specific business entity for that "
            "transaction_type, not an intermediate payment/adjustment record:\n"
            "- `booking_payment`, `security_in`, `security_out`, `refund` -> booking id\n"
            "- `sale_payment` -> sale id\n"
            "- `expense` -> expense id\n"
            "- `product_expense` -> product variant expense id\n"
            "- `tailor_order_payment`, `tailor_order_security_in`, `tailor_order_refund`, "
            "`tailor_security_refund` -> tailor order id\n"
            "- `tailor_payment` -> tailor id\n"
            "- `manual_income` -> manual income id\n"
            "- `investment` -> investment id\n"
            "- `withdrawal` -> withdrawal id\n"
            "- `transfer_in`, `transfer_out` -> account transfer id"
        ),
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="Account id.",
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination page number for the account's transaction history.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination page size for the account's transaction history.",
            ),
            OpenApiParameter(
                name="start_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional. Only return transactions on/after this date. Formats: "
                    "YYYY-MM-DD or DD-MM-YYYY. The opening balance for the filtered history is "
                    "computed from all transactions strictly before this date."
                ),
            ),
            OpenApiParameter(
                name="end_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional. Only return transactions on/before this date (inclusive). "
                    "Formats: YYYY-MM-DD or DD-MM-YYYY."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Account fetched successfully."),
            404: OpenApiResponse(description="Exact message: Account not found."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- start_date cannot be after end_date.\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.\n"
                    "- Invalid page number."
                )
            ),
            403: OpenApiResponse(
                description=(
                    "Permission errors. Exact messages:\n"
                    "- Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to fetch account."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /finance/v3/accounts/5/",
                value={"id": 5},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                description=(
                    "reference_id resolves to the entity relevant to each transaction_type - "
                    "e.g. reference_id: 87 on the booking_payment row is the booking id (not a "
                    "payment record id), and reference_id: 44 on the expense row is the expense id."
                ),
                value={
                    "status": "success",
                    "message": "Account fetched successfully.",
                    "data": {
                        "next": "https://api.example.com/finance/v3/accounts/5/?page=2",
                        "previous": None,
                        "count": 42,
                        "page_size": 15,
                        "current_page": 1,
                        "total_pages": 3,
                        "start_index": 1,
                        "end_index": 15,
                        "data": {
                            "account": {
                                "id": 5,
                                "name": "HDFC Current Account",
                                "account_type": "bank",
                                "description": "Main settlement account",
                                "account_number": "1234567890",
                                "is_default": False,
                                "is_active": True,
                                "account_balance": "16500.00",
                                "created_at": "2026-01-10T10:00:00Z",
                            },
                            "transactions": [
                                {
                                    "id": 1,
                                    "date": "10-01-2026 10:00:00",
                                    "transaction_type": "manual_income",
                                    "transaction_type_display": "Manual Income",
                                    "direction": "credit",
                                    "amount": "15000.00",
                                    "balance_after": "15000.00",
                                    "reference_id": 1,
                                },
                                {
                                    "id": 2,
                                    "date": "02-08-2026 09:05:44",
                                    "transaction_type": "expense",
                                    "transaction_type_display": "Expense",
                                    "direction": "debit",
                                    "amount": "1000.00",
                                    "balance_after": "14000.00",
                                    "reference_id": 44,
                                },
                                {
                                    "id": 118,
                                    "date": "03-08-2026 14:22:10",
                                    "transaction_type": "booking_payment",
                                    "transaction_type_display": "Booking Payment",
                                    "direction": "credit",
                                    "amount": "2500.00",
                                    "balance_after": "16500.00",
                                    "reference_id": 87,
                                },
                            ],
                        },
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Date-filtered request example",
                description="GET /finance/v3/accounts/5/?start_date=01-07-2026&end_date=31-07-2026",
                value={"id": 5, "start_date": "01-07-2026", "end_date": "31-07-2026"},
                request_only=True,
            ),
            OpenApiExample(
                "Date-filtered success response",
                description=(
                    "Account balance as of 30-06-2026 (before start_date) was 5000.00 - the "
                    "opening_balance. balance_after on each row continues from that opening "
                    "balance instead of restarting at zero, and account_balance reflects the "
                    "balance as of the end of the filtered range (31-07-2026), not the account's "
                    "live current balance."
                ),
                value={
                    "status": "success",
                    "message": "Account fetched successfully.",
                    "data": {
                        "next": None,
                        "previous": None,
                        "count": 2,
                        "page_size": 15,
                        "current_page": 1,
                        "total_pages": 1,
                        "start_index": 1,
                        "end_index": 2,
                        "data": {
                            "account": {
                                "id": 5,
                                "name": "HDFC Current Account",
                                "account_type": "bank",
                                "description": "Main settlement account",
                                "account_number": "1234567890",
                                "is_default": False,
                                "is_active": True,
                                "account_balance": "6500.00",
                                "created_at": "2026-01-10T10:00:00Z",
                            },
                            "transactions": [
                                {
                                    "id": 210,
                                    "date": "05-07-2026 11:00:00",
                                    "transaction_type": "booking_payment",
                                    "transaction_type_display": "Booking Payment",
                                    "direction": "credit",
                                    "amount": "2000.00",
                                    "balance_after": "7000.00",
                                    "reference_id": 140,
                                },
                                {
                                    "id": 215,
                                    "date": "20-07-2026 16:30:00",
                                    "transaction_type": "expense",
                                    "transaction_type_display": "Expense",
                                    "direction": "debit",
                                    "amount": "500.00",
                                    "balance_after": "6500.00",
                                    "reference_id": 61,
                                },
                            ],
                        },
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid date range response",
                value={
                    "status": "validation_error",
                    "message": "start_date cannot be after end_date.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Not found response",
                value={
                    "status": "not_found",
                    "message": "Account not found.",
                    "data": None,
                },
                response_only=True,
                status_codes=["404"],
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
                    "message": "Unable to fetch account.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def retrieve(self, request, pk=None):
        """
        GET /finance/v3/accounts/{id}/
        Retrieve a single account belonging to the active shop, including its current balance
        and a paginated, chronological transaction history, optionally scoped to a
        start_date/end_date range (with an accurate opening balance carried forward).
        Accessible by all roles.
        """
        try:
            account = Account.objects.filter(
                id=pk, shop_id=self.shop_id, deleted=None
            ).first()

            if not account:
                return self.not_found_response(
                    message="Account not found.",
                    dev_message=f"Account id={pk} not found for shop {self.shop_id}.",
                )

            start_date_param = request.query_params.get("start_date")
            end_date_param = request.query_params.get("end_date")
            try:
                start_date = parse_date(start_date_param, "start_date") if start_date_param else None
                end_date = parse_date(end_date_param, "end_date") if end_date_param else None
            except ValidationError as e:
                return self.validation_error_response(
                    errors=e.detail,
                    message="Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.",
                )

            if start_date and end_date and start_date > end_date:
                return self.validation_error_response(
                    errors={"start_date": ["start_date cannot be after end_date."]},
                    message="start_date cannot be after end_date.",
                )

            base_qs, ledger_qs, opening_balance, account_balance = get_account_history_data(
                account, start_date, end_date
            )

            paginator = self.pagination_class()
            try:
                page = paginator.paginate_queryset(ledger_qs, request)
            except NotFound as e:
                return self.validation_error_response(
                    errors={"page": [str(e)]},
                    message="Invalid page number.",
                )

            page_entries = list(page) if page is not None else []

            if page_entries:
                first_entry = page_entries[0]
                # Balance immediately before this page's first row - one bounded
                # aggregate (indexed range scan), NOT a full-range window function.
                # Deliberately queries the account's whole history (not just the
                # filtered range) since entries between start_date and this page's
                # first row must also be included, same as opening_balance's semantics.
                page_opening_balance = base_qs.filter(
                    Q(created_at__lt=first_entry.created_at)
                    | Q(created_at=first_entry.created_at, id__lt=first_entry.id)
                ).aggregate(total=Sum("signed_amount"))["total"] or Decimal("0.00")

                running_balance = page_opening_balance
                for entry in page_entries:
                    running_balance += entry.signed_amount
                    entry.balance_after = running_balance

            resolve_ledger_reference_ids(page_entries)

            transactions_serializer = LedgerEntryHistorySerializer(page_entries, many=True)

            account_data = dict(AccountSerializer(account).data)
            account_data["account_balance"] = account_balance

            response_data = {
                "account": account_data,
                "transactions": transactions_serializer.data,
            }

            return paginator.get_paginated_response(response_data)

        except Exception as e:
            logger.error(
                f"Error retrieving account {pk} for shop {self.shop_id}: {e}",
                exc_info=True,
            )
            return self.error_response(
                message="Unable to fetch account.",
                dev_message=f"Unhandled error in AccountRetrieveMixin.retrieve for account {pk}.",
            )
