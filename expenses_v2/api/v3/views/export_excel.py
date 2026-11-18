import io
import logging
from collections import defaultdict
from decimal import Decimal

import xlsxwriter
from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.number_format import INDIAN_EXCEL_NUM_FORMAT
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from pdf_service.api.v3.views.mixins import ShopLedgerTransactionExportMixin
from pdf_service.api.v3.utils.helpers import safe_decimal
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ExportTransactionsExcelView(ShopLedgerTransactionExportMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Export paid transactions as Excel",
        description=(
            "Exports transaction data into Excel with dedicated layouts for paid flows such as payments, expenses, "
            "or combined expenses+payments reconciliation."
        ),
        parameters=[
            OpenApiParameter(name="from_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Optional start date."),
            OpenApiParameter(name="to_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Optional end date."),
            OpenApiParameter(name="type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Comma-separated types. Examples: payments, expenses, expenses,payments."),
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client filter."),
        ],
        responses={
            200: OpenApiResponse(description="Excel file download response."),
            400: OpenApiResponse(description="Validation errors delegated from transaction data retrieval."),
            403: OpenApiResponse(description="Permission denied response from active shop check."),
            500: OpenApiResponse(description="Exact message: Excel export failed"),
        },
    )
    def get(self, request):
        try:
            ZERO = Decimal('0')

            def as_decimal(value):
                return safe_decimal(value, default=ZERO)

            def as_xlsx_number(value):
                return float(as_decimal(value))

            def normalize_payment_method(value):
                raw = (value or "").strip().lower()
                if not raw:
                    return "-"
                if "cash" in raw:
                    return "Cash"
                if "gpay" in raw or "google" in raw or "upi" in raw:
                    return "UPI"
                return "UPI"

            TYPE_MAPPING = {
                "sales": "sales",
                "bookings": "bookings",
                "pendings": "pendings",
                "expense": "expenses",
                "expenses": "expenses",
                "payments": "payments",
                "payments_without_security": "payments_without_security",
                "security": "security",
                "tailor_orders": "tailor_orders",
            }

            input_types = request.query_params.get("type", "all").split(",")
            normalized_types = [t.strip().lower() for t in input_types]
            txn_types = [TYPE_MAPPING.get(t) for t in normalized_types if TYPE_MAPPING.get(t)]
            txn_types = list(dict.fromkeys(txn_types))

            if not txn_types:
                txn_types = ["all"]

            transactions = self.get_transaction_data(request, txn_types=txn_types)

            output = io.BytesIO()
            wb = xlsxwriter.Workbook(output, {'in_memory': True})
            ws = wb.add_worksheet("Transactions")

            header_fmt = wb.add_format({'bold': True, 'align': 'center'})
            center_fmt = wb.add_format({'align': 'center'})
            money_fmt = wb.add_format({'num_format': INDIAN_EXCEL_NUM_FORMAT, 'align': 'center'})

            row = 0

            # ============================================================
            # BOOKINGS EXPORT
            # ============================================================
            if len(txn_types) == 1 and txn_types[0] == "bookings":
                ws.set_column(0, 0, 8)
                ws.set_column(1, 1, 15)
                ws.set_column(2, 2, 55)
                ws.set_column(3, 3, 18)
                ws.set_column(4, 4, 25)
                ws.set_column(5, 7, 18)

                headers = ["S.No.", "Date", "Product", "Invoice ID", "Client Name", "Paid Amount", "Pending Amount", "Total Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_paid = ZERO
                total_pending = ZERO
                total_amount = ZERO
                paid_cash = ZERO
                paid_upi  = ZERO
                sno = 0
                row += 1

                for txn in transactions:
                    if txn["type"] != "bookings":
                        continue
                    sno += 1
                    ws.write(row, 0, sno, center_fmt)
                    ws.write(row, 1, txn["date"], center_fmt)
                    ws.write(row, 2, txn.get("product_names", "-"), center_fmt)
                    ws.write(row, 3, str(txn.get("invoice_id") or "-"), center_fmt)
                    ws.write(row, 4, txn["name"], center_fmt)
                    paid_amount = as_decimal(txn.get("paid_amount"))
                    pending_amount = as_decimal(txn.get("pending_amount"))
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 5, float(paid_amount), money_fmt)
                    ws.write(row, 6, float(pending_amount), money_fmt)
                    ws.write(row, 7, float(amount), money_fmt)
                    total_paid += paid_amount
                    total_pending += pending_amount
                    total_amount += amount
                    paid_cash += as_decimal(txn.get("paid_cash"))
                    paid_upi  += as_decimal(txn.get("paid_upi"))
                    row += 1

                ws.write(row + 1, 4, "TOTAL",     header_fmt)
                ws.write(row + 1, 5, float(total_paid),    money_fmt)
                ws.write(row + 1, 6, float(total_pending), money_fmt)
                ws.write(row + 1, 7, float(total_amount),  money_fmt)
                # blank gap at row + 2
                ws.write(row + 3, 4, "PAID CASH", header_fmt)
                ws.write(row + 3, 5, float(paid_cash),     money_fmt)
                ws.write(row + 4, 4, "PAID UPI",  header_fmt)
                ws.write(row + 4, 5, float(paid_upi),      money_fmt)
                ws.write(row + 5, 4, "TOTAL PAID", header_fmt)
                ws.write(row + 5, 5, float(paid_cash + paid_upi), money_fmt)

            # ============================================================
            # PAYMENTS EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "payments":
                ws.set_column(0, 0, 20)
                ws.set_column(1, 1, 25)
                ws.set_column(2, 2, 18)
                ws.set_column(3, 3, 30)
                ws.set_column(4, 4, 35)
                ws.set_column(5, 5, 15)

                headers = ["Date & Time", "Customer Name", "Invoice ID", "Type", "Payment Method", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_upi = ZERO
                total_cash = ZERO
                total_amount = ZERO
                security_upi = ZERO
                security_cash = ZERO
                security_total = ZERO
                sub_type_totals = defaultdict(Decimal)
                manual_income_breakdown = defaultdict(Decimal)
                row += 1

                for txn in transactions:
                    if txn["type"] not in ("payments", "payment"):
                        continue
                    ws.write(row, 0, txn["date"], center_fmt)
                    ws.write(row, 1, txn["name"], center_fmt)
                    ws.write(row, 2, str(txn.get("invoice_id") or "-"), center_fmt)
                    ws.write(row, 3, txn.get("sub_type", "-"), center_fmt)
                    method = normalize_payment_method(txn.get("payment_method"))
                    ws.write(row, 4, method, center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 5, float(amount), money_fmt)
                    if txn.get("payment_category") == "security":
                        security_total += amount
                        if method == "UPI":
                            security_upi += amount
                        else:
                            security_cash += amount
                    else:
                        total_amount += amount
                        if method == "UPI":
                            total_upi += amount
                        else:
                            total_cash += amount
                    sub_type_totals[txn.get("sub_type") or "-"] += amount
                    if txn.get("sub_type") == "Manual Entry":
                        manual_income_breakdown[txn.get("name") or "Other"] += amount
                    row += 1

                grand_total = total_amount + security_total

                # --- Group 1: regular totals ---
                ws.write(row + 1, 4, "TOTAL CASH",   header_fmt)
                ws.write(row + 1, 5, float(total_cash),  money_fmt)
                ws.write(row + 2, 4, "TOTAL UPI",    header_fmt)
                ws.write(row + 2, 5, float(total_upi),   money_fmt)
                ws.write(row + 3, 4, "TOTAL AMOUNT", header_fmt)
                ws.write(row + 3, 5, float(total_amount), money_fmt)

                # --- blank gap row ---
                # row + 4 is intentionally left empty

                # --- Group 2: security totals ---
                ws.write(row + 5, 4, "SECURITY CASH",              header_fmt)
                ws.write(row + 5, 5, float(security_cash),          money_fmt)
                ws.write(row + 6, 4, "SECURITY UPI",               header_fmt)
                ws.write(row + 6, 5, float(security_upi),           money_fmt)
                ws.write(row + 7, 4, "TOTAL SECURITY COLLECTED",   header_fmt)
                ws.write(row + 7, 5, float(security_total),         money_fmt)

                # --- blank gap row ---
                # row + 8 is intentionally left empty

                # --- Dynamic blocks start at row+9; Grand Total follows after ---
                fr = row + 9

                # --- Group 3: manual income breakdown by category ---
                if manual_income_breakdown:
                    ws.write(fr, 4, "MANUAL ENTRY BREAKDOWN", header_fmt)
                    fr += 1
                    for category, amt in manual_income_breakdown.items():
                        ws.write(fr, 4, category, center_fmt)
                        ws.write(fr, 5, float(amt), money_fmt)
                        fr += 1
                    ws.write(fr, 4, "TOTAL MANUAL ENTRY", header_fmt)
                    ws.write(fr, 5, float(sum(manual_income_breakdown.values())), money_fmt)
                    fr += 1  # blank separator after manual income

                # --- Group 4: payment type breakdown (only non-zero sub_types) ---
                _PAYMENT_SUB_TYPE_LABELS = [
                    ("Booking",              "Booking Payments"),
                    ("Sale",                 "Sales Payments"),
                    ("Custom Work",          "Custom Work Payments"),
                    ("Booking Security",     "Booking Security Payments"),
                    ("Custom Work Security", "Custom Work Security Payments"),
                    ("Manual Entry",         "Manual Entry"),
                ]
                breakdown_rows = [(label, sub_type_totals[key]) for key, label in _PAYMENT_SUB_TYPE_LABELS if sub_type_totals[key] > ZERO]
                if breakdown_rows:
                    ws.write(fr, 4, "PAYMENT TYPE BREAKDOWN", header_fmt)
                    fr += 1
                    for label, amt in breakdown_rows:
                        ws.write(fr, 4, label, center_fmt)
                        ws.write(fr, 5, float(amt), money_fmt)
                        fr += 1

                # --- Group 5: grand total (after all dynamic blocks) ---
                fr += 1  # blank gap
                ws.write(fr, 4, "GRAND TOTAL", header_fmt)
                ws.write(fr, 5, float(grand_total), money_fmt)


            # ============================================================
            # PAYMENTS WIHTOUT SECURITY EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "payments_without_security":
                ws.set_column(0, 0, 20)
                ws.set_column(1, 1, 25)
                ws.set_column(2, 2, 18)
                ws.set_column(3, 3, 30)
                ws.set_column(4, 4, 35)
                ws.set_column(5, 5, 15)

                headers = ["Date & Time", "Customer Name", "Invoice ID", "Type", "Payment Method", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_upi = ZERO
                total_cash = ZERO
                total_amount = ZERO
                security_upi = ZERO
                security_cash = ZERO
                security_total = ZERO
                sub_type_totals = defaultdict(Decimal)
                manual_income_breakdown = defaultdict(Decimal)
                row += 1

                for txn in transactions:
                    if txn["type"] != "payments_without_security":
                        continue
                    ws.write(row, 0, txn["date"], center_fmt)
                    ws.write(row, 1, txn["name"], center_fmt)
                    ws.write(row, 2, str(txn.get("invoice_id") or "-"), center_fmt)
                    ws.write(row, 3, txn.get("sub_type", "-"), center_fmt)
                    method = normalize_payment_method(txn.get("payment_method"))
                    ws.write(row, 4, method, center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 5, float(amount), money_fmt)

                    total_amount += amount
                    if method == "UPI":
                        total_upi += amount
                    else:
                        total_cash += amount
                    sub_type_totals[txn.get("sub_type") or "-"] += amount
                    if txn.get("sub_type") == "Manual Entry":
                        manual_income_breakdown[txn.get("name") or "Other"] += amount

                    row += 1

                grand_total = total_amount + security_total

                # --- Group 1: regular totals ---
                ws.write(row + 1, 4, "TOTAL CASH",   header_fmt)
                ws.write(row + 1, 5, float(total_cash),  money_fmt)
                ws.write(row + 2, 4, "TOTAL UPI",    header_fmt)
                ws.write(row + 2, 5, float(total_upi),   money_fmt)
                ws.write(row + 3, 4, "TOTAL AMOUNT", header_fmt)
                ws.write(row + 3, 5, float(total_amount), money_fmt)

                # --- Dynamic blocks start at row+4; Grand Total follows after ---
                fr = row + 4  # blank at row+4; fr += 1 inside blocks lands at row+5

                # --- Group 2: manual income breakdown by category ---
                if manual_income_breakdown:
                    fr += 1  # blank separator — lands at row+5 on first use
                    ws.write(fr, 4, "MANUAL INCOME BREAKDOWN", header_fmt)
                    fr += 1
                    for category, amt in manual_income_breakdown.items():
                        ws.write(fr, 4, category, center_fmt)
                        ws.write(fr, 5, float(amt), money_fmt)
                        fr += 1
                    ws.write(fr, 4, "TOTAL MANUAL INCOME", header_fmt)
                    ws.write(fr, 5, float(sum(manual_income_breakdown.values())), money_fmt)

                # --- Group 3: payment type breakdown (only non-zero sub_types) ---
                # Security sub_types are absent here — data layer already excludes them
                _PAYMENT_SUB_TYPE_LABELS = [
                    ("Booking",     "Booking Payments"),
                    ("Sale",        "Sales Payments"),
                    ("Custom Work", "Custom Work Payments"),
                    ("Manual Entry","Manual Entry"),
                ]
                breakdown_rows = [(label, sub_type_totals[key]) for key, label in _PAYMENT_SUB_TYPE_LABELS if sub_type_totals[key] > ZERO]
                if breakdown_rows:
                    fr += 1  # blank separator before payment type block
                    ws.write(fr, 4, "PAYMENT TYPE BREAKDOWN", header_fmt)
                    fr += 1
                    for label, amt in breakdown_rows:
                        ws.write(fr, 4, label, center_fmt)
                        ws.write(fr, 5, float(amt), money_fmt)
                        fr += 1

                # --- Group 4: grand total (after all dynamic blocks) ---
                fr += 1  # blank gap
                ws.write(fr, 4, "GRAND TOTAL", header_fmt)
                ws.write(fr, 5, float(grand_total), money_fmt)



            # ============================================================
            # PENDINGS EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "pendings":
                ws.set_column(0, 0, 15)
                ws.set_column(1, 1, 25)
                ws.set_column(2, 2, 18)
                ws.set_column(3, 3, 55)
                ws.set_column(4, 4, 18)
                ws.set_column(5, 5, 15)

                headers = ["Date", "Customer Name", "Invoice ID", "Product", "Phone", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_amt = ZERO
                row += 1

                for txn in transactions:
                    if txn["type"] != "pendings":
                        continue
                    ws.write(row, 0, txn["date"], center_fmt)
                    ws.write(row, 1, txn["name"], center_fmt)
                    ws.write(row, 2, str(txn.get("invoice_id") or "-"), center_fmt)
                    ws.write(row, 3, txn.get("items", "-"), center_fmt)
                    ws.write(row, 4, txn.get("client_phone", "-"), center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 5, float(amount), money_fmt)
                    total_amt += amount
                    row += 1

                ws.write(row + 1, 4, "TOTAL", header_fmt)
                ws.write(row + 1, 5, float(total_amt), money_fmt)

            # ============================================================
            # EXPENSE EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "expenses":
                ws.set_column(0, 0, 22)
                ws.set_column(1, 1, 30)
                ws.set_column(2, 2, 30)  
                ws.set_column(3, 3, 30)
                ws.set_column(4, 4, 15)

                headers = ["Date & Time", "Name", "Type", "Payment Method", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                _SECURITY_EXPENSE_TYPES = {"Booking Security Refund", "Custom work Security Refund"}
                total_upi = ZERO
                total_cash = ZERO
                total_amt = ZERO
                security_upi = ZERO
                security_cash = ZERO
                security_total = ZERO
                expense_breakdown = defaultdict(Decimal)
                row += 1

                for txn in transactions:
                    if txn["type"] != "expenses":
                        continue
                    ws.write(row, 0, txn["date"], center_fmt)
                    ws.write(row, 1, txn["name"], center_fmt)
                    ws.write(row, 2, txn.get("sub_type", "-"), center_fmt)
                    method = normalize_payment_method(txn.get("payment_method"))
                    ws.write(row, 3, method, center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 4, float(amount), money_fmt)
                    is_security = (txn.get("expense_type") or "") in _SECURITY_EXPENSE_TYPES
                    if is_security:
                        security_total += amount
                        if method == "UPI":
                            security_upi += amount
                        else:
                            security_cash += amount
                    else:
                        total_amt += amount
                        if method == "UPI":
                            total_upi += amount
                        else:
                            total_cash += amount
                        expense_breakdown[txn.get("expense_type") or "Other"] += amount
                    row += 1

                grand_total = total_amt + security_total

                # --- Group 1: regular totals ---
                ws.write(row + 1, 3, "TOTAL CASH",   header_fmt)
                ws.write(row + 1, 4, float(total_cash),  money_fmt)
                ws.write(row + 2, 3, "TOTAL UPI",    header_fmt)
                ws.write(row + 2, 4, float(total_upi),   money_fmt)
                ws.write(row + 3, 3, "TOTAL AMOUNT", header_fmt)
                ws.write(row + 3, 4, float(total_amt),  money_fmt)

                # --- blank gap row (row + 4 empty) ---

                # --- Group 2: security totals ---
                ws.write(row + 5, 3, "SECURITY CASH",             header_fmt)
                ws.write(row + 5, 4, float(security_cash),         money_fmt)
                ws.write(row + 6, 3, "SECURITY UPI",              header_fmt)
                ws.write(row + 6, 4, float(security_upi),          money_fmt)
                ws.write(row + 7, 3, "TOTAL SECURITY REFUNDED",   header_fmt)
                ws.write(row + 7, 4, float(security_total),        money_fmt)

                # --- blank gap row (row + 8 empty) ---

                # --- Group 3: grand total ---
                ws.write(row + 9, 3, "GRAND TOTAL",  header_fmt)
                ws.write(row + 9, 4, float(grand_total), money_fmt)

                # --- Expense Breakdown ---
                row += 11
                ws.write(row, 1, "Expense Breakdown", header_fmt)
                for k, v in expense_breakdown.items():
                    row += 1
                    ws.write(row, 1, k)
                    ws.write(row, 2, float(v), money_fmt)

            # ============================================================
            # SALES EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "sales":
                ws.set_column(0, 0, 8)
                ws.set_column(1, 1, 20)
                ws.set_column(2, 2, 55)
                ws.set_column(3, 3, 18)
                ws.set_column(4, 4, 18)
                ws.set_column(5, 5, 15)

                headers = ["S.No.", "Date", "Product Name", "Invoice ID", "Payment Method", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_sales = ZERO
                sno = 0
                row += 1

                for txn in transactions:
                    if txn["type"] != "sales":
                        continue
                    sno += 1
                    ws.write(row, 0, sno, center_fmt)
                    ws.write(row, 1, txn.get("date"), center_fmt)
                    ws.write(row, 2, txn.get("product_names", "-"), center_fmt)
                    ws.write(row, 3, str(txn.get("invoice_id") or "-"), center_fmt)
                    ws.write(row, 4, normalize_payment_method(txn.get("payment_method")), center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 5, float(amount), money_fmt)
                    total_sales += amount
                    row += 1

                ws.write(row + 1, 4, "TOTAL", header_fmt)
                ws.write(row + 1, 5, float(total_sales), money_fmt)

            # ============================================================
            # SECURITY EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "security":
                ws.set_column(0, 0, 20)
                ws.set_column(1, 1, 20)
                ws.set_column(2, 2, 25)
                ws.set_column(3, 3, 15)
                ws.set_column(4, 4, 15)
                ws.set_column(5, 5, 15)

                headers = ["Date & Time", "ID", "Name", "Amount", "Deducted", "Refunded"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_amount = ZERO
                total_deducted = ZERO
                total_refunded = ZERO
                pending_cash_raw = ZERO
                pending_upi_raw  = ZERO
                row += 1

                for txn in transactions:
                    if txn["type"] != "security":
                        continue
                    txn_id = txn.get("shop_booking_id") or txn.get("shop_tailor_id")
                    ws.write(row, 0, txn["date"], center_fmt)
                    ws.write(row, 1, txn_id, center_fmt)
                    ws.write(row, 2, txn["name"], center_fmt)
                    amount   = as_decimal(txn.get("amount"))
                    deducted = as_decimal(txn.get("deducted_amount"))
                    refunded = as_decimal(txn.get("refunded_amount"))
                    ws.write(row, 3, float(amount),   money_fmt)
                    ws.write(row, 4, float(deducted), money_fmt)
                    ws.write(row, 5, float(refunded), money_fmt)
                    total_amount   += amount
                    total_deducted += deducted
                    total_refunded += refunded
                    # Accumulate raw (unclamped) per-method pending; clamping happens below.
                    item_pending = amount - refunded - deducted
                    raw_method = str(txn.get("payment_method") or "").strip().lower()
                    if raw_method == "cash":
                        pending_cash_raw += item_pending
                    else:
                        pending_upi_raw += item_pending
                    row += 1

                # Anchor both buckets to the authoritative total so PENDING CASH + PENDING UPI
                # always equals TOTAL PENDING — independent per-method clamping would inflate
                # the breakdown sum whenever a single method is over-refunded.
                total_pending = total_amount - (total_deducted + total_refunded)
                total_pending_display = max(ZERO, total_pending)
                pending_cash = max(ZERO, min(pending_cash_raw, total_pending_display))
                pending_upi  = total_pending_display - pending_cash

                ws.write(row + 1, 2, "TOTAL AMOUNT",   header_fmt)
                ws.write(row + 1, 3, float(total_amount),   money_fmt)
                ws.write(row + 2, 2, "TOTAL DEDUCTED", header_fmt)
                ws.write(row + 2, 3, float(total_deducted), money_fmt)
                ws.write(row + 3, 2, "TOTAL REFUNDED", header_fmt)
                ws.write(row + 3, 3, float(total_refunded), money_fmt)
                ws.write(row + 4, 2, "TOTAL PENDING",  header_fmt)
                ws.write(row + 4, 3, float(total_pending_display), money_fmt)
                # blank gap at row + 5
                ws.write(row + 6, 2, "PENDING CASH",   header_fmt)
                ws.write(row + 6, 3, float(pending_cash),   money_fmt)
                ws.write(row + 7, 2, "PENDING UPI",    header_fmt)
                ws.write(row + 7, 3, float(pending_upi),    money_fmt)

            # ============================================================
            # TAILOR ORDERS (CUSTOM WORK) EXPORT
            # ============================================================
            elif len(txn_types) == 1 and txn_types[0] == "tailor_orders":
                ws.set_column(0, 0, 22)
                ws.set_column(1, 1, 18)
                ws.set_column(2, 2, 20)
                ws.set_column(3, 3, 18)

                headers = ["Date & Time", "ID", "Client Phone", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_amount = ZERO
                row += 1

                for txn in transactions:
                    if txn["type"] != "tailor_orders":
                        continue
                    ws.write(row, 0, txn.get("created_at", txn.get("date", "-")), center_fmt)
                    ws.write(row, 1, txn.get("invoice_id", "-"), center_fmt)
                    ws.write(row, 2, txn.get("client_phone", "-"), center_fmt)
                    amount = as_decimal(txn.get("paid_amount"))
                    ws.write(row, 3, float(amount), money_fmt)
                    total_amount += amount
                    row += 1

                ws.write(row + 1, 2, "TOTAL", header_fmt)
                ws.write(row + 1, 3, float(total_amount), money_fmt)

            # ============================================================
            # SPECIAL CASE → EXPENSES + PAYMENTS ONLY
            # ============================================================
            elif len(txn_types) == 2 and set(txn_types) == {"expenses", "payments"}:
                ws.set_column(0, 0, 20)
                ws.set_column(1, 1, 12)
                ws.set_column(2, 2, 30)
                ws.set_column(3, 3, 18)
                ws.set_column(4, 4, 15)

                headers = ["Date & Time", "Type", "Customer / Category", "Payment Method", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                _SECURITY_EXPENSE_TYPES_COMBINED = {"Booking Security Refund", "Custom work Security Refund"}
                total_payment = ZERO
                total_expense = ZERO
                security_payment = ZERO
                expense_breakdown = defaultdict(Decimal)
                row += 1

                for txn in transactions:
                    if txn["type"] not in ("payments", "payment", "expenses"):
                        continue
                    ws.write(row, 0, txn.get("date"), center_fmt)
                    txn_type_title = "Payment" if txn["type"] in ("payments", "payment") else "Expense"
                    ws.write(row, 1, txn_type_title, center_fmt)
                    name = (
                        txn.get("name") if txn["type"] in ("payments", "payment")
                        else txn.get("expense_type") or txn.get("name")
                    )
                    ws.write(row, 2, name or "-", center_fmt)
                    ws.write(row, 3, normalize_payment_method(txn.get("payment_method")), center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 4, float(amount), money_fmt)
                    if txn["type"] in ("payments", "payment"):
                        if txn.get("payment_category") == "security":
                            security_payment += amount
                        else:
                            total_payment += amount
                    if txn["type"] == "expenses":
                        if (txn.get("expense_type") or "") in _SECURITY_EXPENSE_TYPES_COMBINED:
                            pass  # security refunds excluded from expense total
                        else:
                            total_expense += amount
                            expense_breakdown[name] += amount
                    row += 1

                ws.write(row + 1, 3, "TOTAL PAYMENTS",  header_fmt)
                ws.write(row + 1, 4, float(total_payment),  money_fmt)
                ws.write(row + 2, 3, "TOTAL EXPENSES",   header_fmt)
                ws.write(row + 2, 4, float(total_expense),  money_fmt)
                ws.write(row + 3, 3, "BALANCE",           header_fmt)
                ws.write(row + 3, 4, float(total_payment - total_expense), money_fmt)
                ws.write(row + 4, 3, "TOTAL SECURITY COLLECTED", header_fmt)
                ws.write(row + 4, 4, float(security_payment),    money_fmt)
                ws.write(row + 5, 3, "GRAND TOTAL",       header_fmt)
                ws.write(row + 5, 4, float(total_payment + security_payment), money_fmt)

                if expense_breakdown:
                    row += 7
                    ws.write(row, 2, "Expense Breakdown", header_fmt)
                    for k, v in expense_breakdown.items():
                        row += 1
                        ws.write(row, 2, k)
                        ws.write(row, 3, float(v), money_fmt)

            # ============================================================
            # MULTIPLE / COMMON EXPORT
            # ============================================================
            else:
                ws.set_column(0, 0, 20)
                ws.set_column(1, 1, 12)
                ws.set_column(2, 2, 30)
                ws.set_column(3, 3, 18)
                ws.set_column(4, 4, 15)

                headers = ["Date & Time", "Type", "Customer/Category", "Payment Method", "Amount"]
                for col, h in enumerate(headers):
                    ws.write(row, col, h, header_fmt)

                total_amt = ZERO
                expense_breakdown = defaultdict(Decimal)
                row += 1

                for txn in transactions:
                    ws.write(row, 0, txn.get("date"), center_fmt)
                    ws.write(row, 1, txn.get("type").title(), center_fmt)
                    name = txn.get("name") or txn.get("expense_type")
                    ws.write(row, 2, name, center_fmt)
                    ws.write(row, 3, normalize_payment_method(txn.get("payment_method")), center_fmt)
                    amount = as_decimal(txn.get("amount"))
                    ws.write(row, 4, float(amount), money_fmt)
                    total_amt += amount
                    if txn.get("type") == "expenses":
                        expense_breakdown[name] += amount
                    row += 1

                ws.write(row + 1, 3, "TOTAL", header_fmt)
                ws.write(row + 1, 4, float(total_amt), money_fmt)

                if expense_breakdown:
                    row += 3
                    ws.write(row, 2, "Expense Breakdown", header_fmt)
                    for k, v in expense_breakdown.items():
                        row += 1
                        ws.write(row, 2, k)
                        ws.write(row, 3, float(v), money_fmt)

            wb.close()
            output.seek(0)

            from_date = request.query_params.get("from_date")
            to_date = request.query_params.get("to_date")

            if txn_types == ["all"]:
                file_type = "all"
            elif len(txn_types) == 1:
                file_type = txn_types[0]
            else:
                file_type = "_".join(sorted(txn_types))

            if from_date and to_date:
                filename = f"{file_type}_{from_date}_{to_date}.xlsx"
            elif from_date:
                filename = f"{file_type}_{from_date}.xlsx"
            else:
                filename = f"{file_type}.xlsx"

            response = FileResponse(
                output,
                as_attachment=True,
                filename=filename,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Access-Control-Expose-Headers'] = 'Content-Disposition'
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        except Exception as e:
            logger.error(f"ExportTransactionsExcelView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Excel export failed",
                dev_message=str(e)
            )