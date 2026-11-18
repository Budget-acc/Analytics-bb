from io import BytesIO
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, Flowable, Image, PageBreak, PageTemplate,
    Frame, NextPageTemplate, BaseDocTemplate
)
from reportlab.platypus import KeepTogether, Spacer
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from ERP.utils.number_format import format_inr
from . import constants
from .constants import (
    logger,
    HEADER_BG_COLOR, SUMMARY_BG_COLOR, TABLE_HEADER_BG, TABLE_HEADER_TEXT,
    TABLE_ROW_ALT_COLOR, TABLE_ROW_MAIN_COLOR, CUSTOM_PURPLE, BLACK, WHITE,
    RED, LIGHT_GREY, DARK_GREY, SUMMARY_BORDER_COLOR,
)
from .images import get_placeholder, download_image_optimized, _resize_image
from .styles import get_cached_style
from .canvas import PageNumberCanvas
from . import fonts  # ensure fonts are registered


# ============================================================================
# LEDGER BOOK PDF SERVICE
# ============================================================================
class LedgerBookPDFService:
    """PDF generation service for ledger book statements."""

    @staticmethod
    def normalize_payment_method(method: Any) -> str:
        """Normalize payment method to only 'Cash' or 'UPI' for PDF display."""
        raw = str(method or "").strip().lower()
        if not raw:
            return "UPI"
        if "cash" in raw:
            return "Cash"
        return "UPI"

    @staticmethod
    def format_date(date_str):
        """Format date string to DD-MM-YYYY format."""
        try:
            if isinstance(date_str, str):
                date_formats = ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d']
                for fmt in date_formats:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        return dt.strftime("%d/%m/%Y")
                    except:
                        continue
            elif hasattr(date_str, 'strftime'):
                return date_str.strftime("%d/%m/%Y")
        except Exception as e:
            logger.warning(f"Failed to format date {date_str}: {e}")
        return str(date_str) if date_str else ""

    @staticmethod
    def format_currency(amount):
        """Format currency with rupee symbol."""
        try:
            if amount is None:
                return "-"
            if isinstance(amount, str):
                amount = Decimal(amount)
            elif not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            return format_inr(amount, decimals=0)
        except:
            return f"₹0"

    @staticmethod
    def _calculate_security_amount(transactions: list) -> Decimal:
        """Sum amounts for transactions where payment_category == 'security'."""
        total = Decimal('0')
        for t in transactions:
            if t.get('payment_category') != 'security':
                continue
            try:
                total += Decimal(str(t.get('amount', 0) or 0))
            except Exception:
                logger.warning(
                    f"[LedgerBookPDFService] Skipping invalid security amount: "
                    f"{t.get('amount')!r}"
                )
        return total

    @staticmethod
    def calculate_total(transactions: List[Dict[str, Any]]) -> Decimal:
        """Calculate total amount from transactions."""
        total = Decimal(0)
        for transaction in transactions:
            try:
                amount = transaction.get('amount', 0)
                if isinstance(amount, str):
                    amount = Decimal(amount.replace('₹', '').replace(',', ''))
                elif not isinstance(amount, Decimal):
                    amount = Decimal(str(amount))
                total += amount
            except:
                continue
        return total

    @staticmethod
    def get_shop_logo_safe(shop, size: int = 60) -> Flowable:
        """Safe wrapper for getting shop logo with fallback."""
        try:
            logo_url = None

            if hasattr(shop, 'logo') and shop.logo:
                if isinstance(shop.logo, str):
                    logo_url = shop.logo
                elif hasattr(shop.logo, 'url'):
                    logo_url = shop.logo.url

            if not logo_url and hasattr(shop, 'img') and shop.img:
                if isinstance(shop.img, str):
                    logo_url = shop.img
                elif hasattr(shop.img, 'url'):
                    logo_url = shop.img.url

            if logo_url:
                raw_bytes = download_image_optimized(logo_url, timeout=3, retries=1)
                if raw_bytes:
                    resized = _resize_image(raw_bytes, size=(size, size), maintain_aspect=True)
                    if resized:
                        try:
                            return Image(resized, width=size, height=size)
                        except:
                            pass
        except Exception as e:
            logger.warning(f"Failed to get shop logo: {e}")

        return get_placeholder(size, size, is_logo=True)

    @staticmethod
    def detect_transaction_types(transactions: List[Dict[str, Any]]) -> set:
        """Detect unique transaction types from the transactions list."""
        return {txn.get('type', 'unknown') for txn in transactions}

    # -------------------------------------------------------------------------
    # Helper: build and return a styled summary table element
    # -------------------------------------------------------------------------
    @staticmethod
    def _make_summary_table(rows: list, available_width: float) -> Table:
        table = Table(rows, colWidths=[available_width * 0.6, available_width * 0.4])
        style = [
            ("BACKGROUND", (0, 0), (-1, -2), SUMMARY_BG_COLOR),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E5D8EF")),  # last row always accented
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 15),
            ("RIGHTPADDING", (0, 0), (-1, -1), 15),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("BOX", (0, 0), (-1, -1), 1, SUMMARY_BORDER_COLOR),
            ("LINEABOVE", (0, 0), (-1, 0), 1, SUMMARY_BORDER_COLOR),
            ("LINEBELOW", (0, -1), (-1, -1), 1, SUMMARY_BORDER_COLOR),
            ("LINEBELOW", (0, 0), (-1, -2), 1, SUMMARY_BORDER_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, -1), BLACK),
        ]
        table.setStyle(TableStyle(style))
        return table

    @staticmethod
    def _format_split_line(upi: Decimal, cash: Decimal) -> str:
        """'UPI: ₹20,000 | Cash: ₹30,000' for use directly below a breakdown title or entry label."""
        return (
            f"UPI: {LedgerBookPDFService.format_currency(upi)} | "
            f"Cash: {LedgerBookPDFService.format_currency(cash)}"
        )

    @staticmethod
    def generate_ledger_pdf(shop_info: Dict[str, Any], transactions: List[Dict[str, Any]],
                           start_date: str, end_date: str, shop=None):
        """
        Generate PDF for ledger book statements with styling matching reference image.
        """
        # Detect transaction types
        txn_types = LedgerBookPDFService.detect_transaction_types(transactions)

        # Calculate totals
        total_amount = LedgerBookPDFService.calculate_total(transactions)

        # Extract expense transactions for categorization
        expense_transactions = [t for t in transactions if t.get('type') == 'expenses']

        # Initialize expense type totals dictionary
        expense_type_totals = {}

        # Calculate expense type totals (normalized)
        for txn in expense_transactions:
            expense_type_raw = txn.get('expense_type')
            if not expense_type_raw:
                expense_type_raw = txn.get('type')
                if expense_type_raw and str(expense_type_raw).lower() == 'expenses':
                    expense_type_raw = None
            if not expense_type_raw:
                expense_type_raw = txn.get('name', '').split('(')[0].strip()
            expense_type = str(expense_type_raw).strip().lower() if expense_type_raw else 'other'
            if not expense_type or expense_type in ('none', 'null', ''):
                expense_type = 'other'
            elif expense_type == 'general expense':
                expense_type = 'other'
            try:
                amount = txn.get('amount', 0)
                if isinstance(amount, str):
                    amount = Decimal(amount.replace('₹', '').replace(',', ''))
                elif not isinstance(amount, Decimal):
                    amount = Decimal(str(amount))
            except:
                amount = Decimal(0)
            expense_type_totals[expense_type] = expense_type_totals.get(expense_type, Decimal(0)) + amount

        sorted_expense_types = sorted(
            expense_type_totals.items(),
            key=lambda x: x[1],
            reverse=True
        )

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=30,
            leftMargin=30,
            topMargin=40,
            bottomMargin=60
        )

        elements = []
        available_width = doc.width

        # ===== HEADER SECTION WITH LOGO AND SHOP INFO =====
        shop_logo = LedgerBookPDFService.get_shop_logo_safe(shop, 50) if shop else get_placeholder(50, 50, is_logo=True)

        shop_name = shop_info.get('name', 'SUIT GALLERY')
        shop_name_para = Paragraph(shop_name, get_cached_style('shop_name'))

        details_lines = []

        address = shop_info.get('address', '')
        if address:
            details_lines.append(Paragraph(address, get_cached_style('shop_address')))

        city = shop_info.get('city', '')
        state = shop_info.get('state', '')
        pincode = shop_info.get('pincode', '')

        location_parts = [p for p in [city, state] if p]
        location_text = ", ".join(location_parts)

        if pincode:
            location_text = f"{location_text} - {pincode}" if location_text else pincode

        if location_text:
            details_lines.append(Paragraph(location_text, get_cached_style('shop_address')))

        phone = shop_info.get('phone', '')
        if phone:
            details_lines.append(Paragraph(f"Phone: {phone}", get_cached_style('shop_contact')))

        phone2 = shop_info.get('phone2', '')
        if phone2:
            details_lines.append(Paragraph(f"Phone 2: {phone2}", get_cached_style('shop_contact')))

        email = shop_info.get('email', '')
        if email:
            details_lines.append(Paragraph(f"Email: {email}", get_cached_style('shop_contact')))

        header_data = [
            [shop_logo, [shop_name_para] + details_lines]
        ]

        header_table = Table(header_data, colWidths=[60, available_width - 60])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 15),
            ("LEFTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

        elements.append(header_table)
        elements.append(Spacer(1, 10))

        class LineFlowable(Flowable):
            def __init__(self, width, color, thickness):
                super().__init__()
                self.width = width
                self.color = color
                self.thickness = thickness
            def wrap(self, availWidth, availHeight):
                return (self.width, self.thickness)
            def draw(self):
                c = self.canv
                c.saveState()
                c.setStrokeColor(HEADER_BG_COLOR)
                c.setLineWidth(self.thickness)
                c.setStrokeAlpha(0.3)
                c.line(0, self.thickness/2, self.width, self.thickness/2)
                c.restoreState()

        elements.append(LineFlowable(available_width, HEADER_BG_COLOR, 0.5))
        elements.append(Spacer(1, 18))

        # ===== LEDGER BOOK STATEMENT TITLE =====
        title = Paragraph("Ledger Book Statements", get_cached_style('title'))
        elements.append(title)

        # ===== STATEMENT PERIOD =====
        formatted_start = LedgerBookPDFService.format_date(start_date)
        formatted_end = LedgerBookPDFService.format_date(end_date)
        type_display_names = {
            'bookings': 'Bookings',
            'sales': 'Sales',
            'expenses': 'Expenses',
            'payments': 'Income',
            'payments_without_security': 'Payments',
            'pendings': 'Pendings',
            'security': 'Security Deposit',
            'tailor_orders': 'Custom Work',
        }
        txn_types_list = [t for t in txn_types if t != 'unknown']
        if len(txn_types_list) == 1:
            label = type_display_names.get(list(txn_types_list)[0], 'Statements')
        elif len(txn_types_list) == 2:
            label = f"{type_display_names.get(list(txn_types_list)[0], list(txn_types_list)[0].capitalize())} and {type_display_names.get(list(txn_types_list)[1], list(txn_types_list)[1].capitalize())}"
        elif len(txn_types_list) > 2:
            display_labels = [type_display_names.get(t, t.capitalize()) for t in txn_types_list]
            label = ', '.join(display_labels[:-1]) + f" and {display_labels[-1]}"
        else:
            label = 'Statements'
        period_text = f"{label} from {formatted_start} - {formatted_end}"
        period_para = Paragraph(period_text, get_cached_style('period'))
        elements.append(period_para)
        elements.append(Spacer(1, 20))

        # ===== SUMMARY SECTION =====
        summary_title = Paragraph("Summary", get_cached_style('section_title'))
        elements.append(summary_title)

        # ===== EXPENSE BREAKDOWN (if expenses present) =====
        if expense_transactions and sorted_expense_types:
            expense_breakdown_title = Paragraph("Expense Breakdown", get_cached_style('expense_breakdown_title'))
            elements.append(expense_breakdown_title)
            elements.append(Spacer(1, 5))

            expense_items = []
            for expense_type, total in sorted_expense_types:
                if expense_type == 'other':
                    display_type = 'Other Expenses'
                else:
                    display_type = ' '.join(
                        word.capitalize() for word in expense_type.replace('_', ' ').split(' ')
                    )
                expense_items.append([
                    Paragraph(display_type, get_cached_style('expense_breakdown_label')),
                    Paragraph(LedgerBookPDFService.format_currency(total), get_cached_style('expense_breakdown_value'))
                ])

            total_expenses = sum(expense_type_totals.values())
            expense_items.append([
                Paragraph("Total Expenses", get_cached_style('summary_label')),
                Paragraph(LedgerBookPDFService.format_currency(total_expenses), get_cached_style('summary_value'))
            ])

            expense_table_width = available_width
            expense_table = Table(expense_items, colWidths=[expense_table_width * 0.6, expense_table_width * 0.4])

            expense_table_style = [
                ("BACKGROUND", (0, 0), (-1, -2), colors.HexColor("#F9FAFB")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E5D8EF")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 15),
                ("RIGHTPADDING", (0, 0), (-1, -1), 15),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 1, SUMMARY_BORDER_COLOR),
                ("LINEABOVE", (0, 0), (-1, 0), 1, SUMMARY_BORDER_COLOR),
                ("LINEBELOW", (0, -2), (-1, -2), 1, SUMMARY_BORDER_COLOR),
                ("LINEBELOW", (0, 0), (-1, -3), 0.5, colors.HexColor("#E5E7EB")),
                ("TEXTCOLOR", (0, 0), (-1, -2), BLACK),
                ("TEXTCOLOR", (0, -1), (-1, -1), CUSTOM_PURPLE),
                ("FONTNAME", (0, -1), (-1, -1), constants.BOLD_FONT),
            ]

            expense_table.setStyle(TableStyle(expense_table_style))
            elements.append(expense_table)
            elements.append(Spacer(1, 15))

        # ===== SUMMARY TABLE(S) =====
        # Shorthand for building a row
        def _row(label, value):
            return [
                Paragraph(label, get_cached_style('summary_label')),
                Paragraph(LedgerBookPDFService.format_currency(value), get_cached_style('summary_value')),
            ]

        def _bold_row(label, value):
            """Emphasized row for section totals (Total Amount, Total Security, Grand Total)."""
            return [
                Paragraph(label, get_cached_style('summary_label_bold')),
                Paragraph(LedgerBookPDFService.format_currency(value), get_cached_style('summary_value_bold')),
            ]

        def _section_title(text):
            """Small descriptive title above a summary sub-table."""
            return Paragraph(text, get_cached_style('summary_section_title'))

        def _split_line(upi, cash):
            """Small-font UPI/Cash split line, placed directly below a breakdown title."""
            return Paragraph(
                LedgerBookPDFService._format_split_line(upi, cash),
                get_cached_style('payment_split_line'),
            )

        def _row_with_split(label, total, upi, cash):
            # DARK_GREY is colors.HexColor("#666666") (constants.py) - the literal hex
            # string is used directly since ReportLab's inline <font color=...> markup
            # requires a string, not a Color object.
            label_html = (
                f"{label}<br/>"
                f"<font size=8 color='#666666'>"
                f"UPI: {LedgerBookPDFService.format_currency(upi)} | Cash: {LedgerBookPDFService.format_currency(cash)}"
                f"</font>"
            )
            return [
                Paragraph(label_html, get_cached_style('summary_label')),
                Paragraph(LedgerBookPDFService.format_currency(total), get_cached_style('summary_value')),
            ]

        is_single_type = len(txn_types) == 1 and 'unknown' not in txn_types

        # ------------------------------------------------------------------
        # payments + expenses combined
        # ------------------------------------------------------------------
        if (len(txn_types) == 2
                and 'payments' in txn_types
                and 'expenses' in txn_types
                and set(txn_types) == {'payments', 'expenses'}):

            _SEC_EXP = {"booking security refund", "custom work security refund"}
            total_payments = sum(
                Decimal(str(t.get('amount', 0) or 0))
                for t in transactions
                if t.get('type') == 'payments' and t.get('payment_category') != 'security'
            )
            total_sec_payments = sum(
                Decimal(str(t.get('amount', 0) or 0))
                for t in transactions
                if t.get('type') == 'payments' and t.get('payment_category') == 'security'
            )
            total_expenses = sum(
                Decimal(str(t.get('amount', 0) or 0))
                for t in transactions
                if t.get('type') == 'expenses'
                and str(t.get('expense_type') or t.get('name') or '').strip().lower() not in _SEC_EXP
            )
            balance = total_payments - total_expenses
            elements.append(_section_title("Amount Breakdown (Excluding Security )"))
            elements.append(LedgerBookPDFService._make_summary_table([
                _row("Total Received", total_payments),
                _row("Total Expense", total_expenses),
                _bold_row("Balance", balance),
            ], available_width))
            elements.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # single type
        # ------------------------------------------------------------------
        elif is_single_type:
            txn_type = list(txn_types)[0]

            # ---- PAYMENTS (Income) ----
            if txn_type == 'payments':
                non_sec = [t for t in transactions if t.get('payment_category') != 'security']
                sec     = [t for t in transactions if t.get('payment_category') == 'security']

                total_upi  = sum(Decimal(str(t.get('amount', 0) or 0)) for t in non_sec
                                 if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'UPI')
                total_cash = sum(Decimal(str(t.get('amount', 0) or 0)) for t in non_sec
                                 if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'Cash')
                total_amount_pay = total_upi + total_cash

                sec_upi  = sum(Decimal(str(t.get('amount', 0) or 0)) for t in sec
                               if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'UPI')
                sec_cash = sum(Decimal(str(t.get('amount', 0) or 0)) for t in sec
                               if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'Cash')
                total_security = sec_upi + sec_cash
                grand_total = total_amount_pay + total_security

                # Compute per-sub_type totals and manual income category breakdown
                _PMT_SUB_TYPE_LABELS = [
                    ("Booking",              "Booking Payments"),
                    ("Sale",                 "Sales Payments"),
                    ("Custom Work",          "Custom Work Payments"),
                    ("Booking Security",     "Booking Security Payments"),
                    ("Custom Work Security", "Custom Work Security Payments"),
                    ("Manual Entry",         "Manual Entry"),
                ]
                _sub_type_totals = {}
                _sub_type_method_totals = {}
                _manual_income_breakdown = {}
                _manual_income_method_totals = {"UPI": Decimal(0), "Cash": Decimal(0)}
                for t in transactions:
                    st = t.get("sub_type") or "-"
                    amt = Decimal(str(t.get("amount", 0) or 0))
                    method = LedgerBookPDFService.normalize_payment_method(t.get('payment_method'))
                    _sub_type_totals[st] = _sub_type_totals.get(st, Decimal(0)) + amt
                    _sub_type_method_totals.setdefault(st, {"UPI": Decimal(0), "Cash": Decimal(0)})
                    _sub_type_method_totals[st][method] += amt
                    if st == "Manual Entry":
                        cat = t.get("name") or "Other"
                        _manual_income_breakdown[cat] = _manual_income_breakdown.get(cat, Decimal(0)) + amt
                        _manual_income_method_totals[method] += amt

                # Table 1 – regular totals
                elements.append(_section_title("Amount Breakdown (Excluding Security)"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Cash", total_cash),
                    _row("UPI", total_upi),
                    _bold_row("Total Amount", total_amount_pay),
                ], available_width))
                elements.append(Spacer(1, 10))

                # Table 2 – security totals
                elements.append(_section_title("Security Deposit Breakdown"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Security Cash", sec_cash),
                    _row("Security UPI", sec_upi),
                    _bold_row("Total Security Collected", total_security),
                ], available_width))
                elements.append(Spacer(1, 10))

                # Table 3 – manual income breakdown by category (only when present)
                if _manual_income_breakdown:
                    mi_rows = [
                        _row(cat, amt)
                        for cat, amt in _manual_income_breakdown.items()
                    ]
                    mi_rows.append(_bold_row("Total Manual Entry", sum(_manual_income_breakdown.values())))
                    elements.append(_section_title("Manual Entry Breakdown"))
                    elements.append(LedgerBookPDFService._make_summary_table(mi_rows, available_width))
                    elements.append(Spacer(1, 10))

                # Table 4 – payment type breakdown + grand total (merged)
                pay_breakdown_rows = [
                    _row_with_split(
                        label,
                        _sub_type_totals[key],
                        _sub_type_method_totals[key]["UPI"],
                        _sub_type_method_totals[key]["Cash"],
                    )
                    for key, label in _PMT_SUB_TYPE_LABELS
                    if _sub_type_totals.get(key, Decimal(0)) > Decimal(0)
                ]
                pay_breakdown_rows.append(_bold_row("Grand Total", grand_total))
                elements.append(_section_title("Payment Type Breakdown"))
                elements.append(LedgerBookPDFService._make_summary_table(pay_breakdown_rows, available_width))

            # ---- PAYMENTS without security ----
            elif txn_type == 'payments_without_security':

                total_upi  = sum(Decimal(str(t.get('amount', 0) or 0)) for t in transactions
                                 if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'UPI')
                total_cash = sum(Decimal(str(t.get('amount', 0) or 0)) for t in transactions
                                 if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'Cash')
                total_amount_pay = total_upi + total_cash

                # Compute per-sub_type totals and manual income category breakdown
                # Security sub_types are absent — data layer already excludes them
                _PMT_SUB_TYPE_LABELS_NO_SEC = [
                    ("Booking",     "Booking Payments"),
                    ("Sale",        "Sales Payments"),
                    ("Custom Work", "Custom Work Payments"),
                    ("Manual Entry","Manual Entry"),
                ]
                _sub_type_totals = {}
                _sub_type_method_totals = {}
                _manual_income_breakdown = {}
                _manual_income_method_totals = {"UPI": Decimal(0), "Cash": Decimal(0)}
                for t in transactions:
                    st = t.get("sub_type") or "-"
                    amt = Decimal(str(t.get("amount", 0) or 0))
                    method = LedgerBookPDFService.normalize_payment_method(t.get('payment_method'))
                    _sub_type_totals[st] = _sub_type_totals.get(st, Decimal(0)) + amt
                    _sub_type_method_totals.setdefault(st, {"UPI": Decimal(0), "Cash": Decimal(0)})
                    _sub_type_method_totals[st][method] += amt
                    if st == "Manual Entry":
                        cat = t.get("name") or "Other"
                        _manual_income_breakdown[cat] = _manual_income_breakdown.get(cat, Decimal(0)) + amt
                        _manual_income_method_totals[method] += amt

                # Table 1 – regular totals
                elements.append(_section_title("Amount Breakdown (Excluding Security)"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Cash", total_cash),
                    _row("UPI", total_upi),
                    _bold_row("Total Amount", total_amount_pay),
                ], available_width))
                elements.append(Spacer(1, 10))

                # Table 2 – manual income breakdown by category (only when present)
                if _manual_income_breakdown:
                    mi_rows = [
                        _row(cat, amt)
                        for cat, amt in _manual_income_breakdown.items()
                    ]
                    mi_rows.append(_bold_row("Total Manual Entry", sum(_manual_income_breakdown.values())))
                    elements.append(_section_title("Manual Entry Breakdown"))
                    elements.append(LedgerBookPDFService._make_summary_table(mi_rows, available_width))
                    elements.append(Spacer(1, 10))

                # Table 3 – payment type breakdown + grand total (merged)
                pay_breakdown_rows = [
                    _row_with_split(
                        label,
                        _sub_type_totals[key],
                        _sub_type_method_totals[key]["UPI"],
                        _sub_type_method_totals[key]["Cash"],
                    )
                    for key, label in _PMT_SUB_TYPE_LABELS_NO_SEC
                    if _sub_type_totals.get(key, Decimal(0)) > Decimal(0)
                ]
                pay_breakdown_rows.append(_bold_row("Grand Total", total_amount_pay))
                elements.append(_section_title("Payment Type Breakdown"))
                elements.append(LedgerBookPDFService._make_summary_table(pay_breakdown_rows, available_width))

            # ---- EXPENSES ----
            elif txn_type == 'expenses':
                _SEC_EXPENSE_TYPES = {"booking security refund", "custom work security refund"}
                non_sec_exp = [
                    t for t in transactions
                    if str(t.get('expense_type') or t.get('name') or '').strip().lower() not in _SEC_EXPENSE_TYPES
                ]
                sec_exp = [
                    t for t in transactions
                    if str(t.get('expense_type') or t.get('name') or '').strip().lower() in _SEC_EXPENSE_TYPES
                ]
                total_upi  = sum(Decimal(str(t.get('amount', 0) or 0)) for t in non_sec_exp
                                 if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'UPI')
                total_cash = sum(Decimal(str(t.get('amount', 0) or 0)) for t in non_sec_exp
                                 if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'Cash')
                total_amount_exp = total_upi + total_cash

                sec_upi  = sum(Decimal(str(t.get('amount', 0) or 0)) for t in sec_exp
                               if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'UPI')
                sec_cash = sum(Decimal(str(t.get('amount', 0) or 0)) for t in sec_exp
                               if LedgerBookPDFService.normalize_payment_method(t.get('payment_method')) == 'Cash')
                total_security = sec_upi + sec_cash
                grand_total = total_amount_exp + total_security

                # Table 1 – regular totals
                elements.append(_section_title("Amount Breakdown (Excluding Security Refunds)"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Cash", total_cash),
                    _row("UPI", total_upi),
                    _bold_row("Total Amount", total_amount_exp),
                ], available_width))
                elements.append(Spacer(1, 10))

                # Table 2 – security totals
                elements.append(_section_title("Security Refund Breakdown"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Security Cash", sec_cash),
                    _row("Security UPI", sec_upi),
                    _bold_row("Total Security Refunded", total_security),
                ], available_width))
                elements.append(Spacer(1, 10))

                # Table 3 – grand total
                elements.append(_section_title("Combined Total"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _bold_row("Grand Total", grand_total),
                ], available_width))

            # ---- SECURITY ----
            elif txn_type == 'security':
                total_amt      = sum(Decimal(str(t.get('amount', 0) or 0)) for t in transactions)
                total_deducted = sum(Decimal(str(t.get('deducted_amount', 0) or 0)) for t in transactions)
                total_refunded = sum(Decimal(str(t.get('refunded_amount', 0) or 0)) for t in transactions)
                total_pending  = total_amt - (total_deducted + total_refunded)

                # Compute pending by payment method.
                # Accumulate raw (unclamped) per-method totals first, then anchor both
                # to the authoritative total_pending so that Cash + UPI == Total Pending
                # always holds in the rendered table — even when a single method is
                # over-refunded (which would inflate the sum under independent clamping).
                pending_cash_raw = Decimal('0')
                pending_upi_raw  = Decimal('0')
                for t in transactions:
                    item_pending = (
                        Decimal(str(t.get('amount', 0) or 0))
                        - Decimal(str(t.get('refunded_amount', 0) or 0))
                        - Decimal(str(t.get('deducted_amount', 0) or 0))
                    )
                    raw_method = str(t.get('payment_method') or '').strip().lower()
                    if raw_method == 'cash':
                        pending_cash_raw += item_pending
                    else:
                        pending_upi_raw += item_pending
                # Anchor both buckets to the authoritative total so Cash + UPI == Total Pending.
                total_pending_display = max(Decimal('0'), total_pending)
                pending_cash = max(Decimal('0'), min(pending_cash_raw, total_pending_display))
                pending_upi  = total_pending_display - pending_cash

                # Pending breakdown — placed ABOVE the main summary (same pattern as Expense section)
                elements.append(_section_title("Pending Amount Breakdown"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Cash", pending_cash),
                    _row("UPI", pending_upi),
                    _bold_row("Total Pending", total_pending_display),
                ], available_width))
                elements.append(Spacer(1, 10))

                # Main security summary
                summary_items = [
                    _row("Total Amount", total_amt),
                    _row("Total Deducted", total_deducted),
                    _row("Total Refunded", total_refunded),
                    _row("Total Pending", total_pending),
                ]
                elements.append(LedgerBookPDFService._make_summary_table(summary_items, available_width))

            # ---- BOOKINGS ----
            elif txn_type == 'bookings':
                total_amt     = sum(Decimal(str(t.get('amount', 0) or 0)) for t in transactions)
                total_paid    = sum(Decimal(str(t.get('paid_amount', 0) or 0)) for t in transactions)
                total_pending = sum(Decimal(str(t.get('pending_amount', 0) or 0)) for t in transactions)
                paid_cash     = sum(Decimal(str(t.get('paid_cash', 0) or 0)) for t in transactions)
                paid_upi      = sum(Decimal(str(t.get('paid_upi', 0) or 0)) for t in transactions)

                # Paid breakdown — placed ABOVE main summary (same pattern as Security section)
                elements.append(_section_title("Paid Amount Breakdown"))
                elements.append(LedgerBookPDFService._make_summary_table([
                    _row("Cash", paid_cash),
                    _row("UPI", paid_upi),
                    _bold_row("Total Paid", total_paid),
                ], available_width))
                elements.append(Spacer(1, 10))

                summary_items = [
                    _row("Total", total_amt),
                    _row("Total Paid", total_paid),
                    _row("Total Pending", total_pending),
                ]
                elements.append(LedgerBookPDFService._make_summary_table(summary_items, available_width))

            # ---- TAILOR ORDERS ----
            elif txn_type == 'tailor_orders':
                total_paid = sum(Decimal(str(t.get('paid_amount', 0) or 0)) for t in transactions)
                summary_items = [_row("Total Paid Amount", total_paid)]
                elements.append(LedgerBookPDFService._make_summary_table(summary_items, available_width))

            # ---- OTHER SINGLE TYPES ----
            else:
                summary_items = [_row("Total Amount", total_amount)]
                elements.append(LedgerBookPDFService._make_summary_table(summary_items, available_width))

        # ------------------------------------------------------------------
        # Multiple / mixed transaction types (unchanged logic, single table)
        # ------------------------------------------------------------------
        else:
            totals_by_type = {}
            for txn in transactions:
                t = txn.get('type', 'unknown')
                amt = Decimal(str(txn.get('amount', 0) or 0))
                totals_by_type[t] = totals_by_type.get(t, Decimal(0)) + amt

            type_display_names_local = {
                'bookings': 'Bookings', 'sales': 'Sales', 'expenses': 'Expenses',
                'payments': 'Payments', 'pendings': 'Pendings', 'security': 'Security',
                'unknown': 'Other', 'tailor_orders': 'Custom Work',
            }
            summary_items = []
            for t, total in sorted(totals_by_type.items()):
                display_name = type_display_names_local.get(t, t.capitalize())
                summary_items.append(_row(display_name, total))

            if not (len(txn_types) == 2 and 'payments' in txn_types and 'expenses' in txn_types
                    and set(txn_types) == {'payments', 'expenses'}):
                summary_items.append(_row("Grand Total", total_amount))

            total_security_mixed = LedgerBookPDFService._calculate_security_amount(transactions)
            if total_security_mixed > 0:
                summary_items.append(_row("Total Security Deposit", total_security_mixed))

            elements.append(LedgerBookPDFService._make_summary_table(summary_items, available_width))

        elements.append(Spacer(1, 25))

        # ===== TRANSACTIONS SECTION =====
        if transactions:
            transactions_title = Paragraph("Transactions", get_cached_style('section_title'))
            elements.append(transactions_title)
            elements.append(Spacer(1, 10))

            if is_single_type:
                txn_type = list(txn_types)[0]

                if txn_type == 'security':
                    header_row = [
                        Paragraph("Date", get_cached_style('table_header')),
                        Paragraph("ID", get_cached_style('table_header')),
                        Paragraph("Name", get_cached_style('table_header_left')),
                        Paragraph("Amount", get_cached_style('table_header_right')),
                        Paragraph("Deducted", get_cached_style('table_header_right')),
                        Paragraph("Refunded", get_cached_style('table_header_right')),
                    ]
                    col_widths = [
                        available_width * 0.25, available_width * 0.15,
                        available_width * 0.23, available_width * 0.125,
                        available_width * 0.125, available_width * 0.125,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        shop_id = txn.get('shop_booking_id') or txn.get('shop_tailor_id') or txn.get('shop_sale_id') or '-'
                        raw_date = txn.get('date', '') or txn.get('sort_date', '')
                        display_date = LedgerBookPDFService.format_date(raw_date)
                        table_data.append([
                            Paragraph(display_date, get_cached_style('table_cell_center')),
                            Paragraph(str(shop_id), get_cached_style('table_cell_center')),
                            Paragraph(str(txn.get('name', '')), get_cached_style('table_cell')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('deducted_amount', 0)), get_cached_style('table_cell_right')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('refunded_amount', 0)), get_cached_style('table_cell_right')),
                        ])

                elif txn_type == 'sales':
                    header_row = [
                        Paragraph("S.No.", get_cached_style('table_header_sm')),
                        Paragraph("Date", get_cached_style('table_header_sm')),
                        Paragraph("Product Name", get_cached_style('table_header_left_sm')),
                        Paragraph("Invoice ID", get_cached_style('table_header_sm')),
                        Paragraph("Payment Method", get_cached_style('table_header_sm')),
                        Paragraph("Amount", get_cached_style('table_header_right_sm')),
                    ]
                    col_widths = [
                        available_width * 0.07,
                        available_width * 0.16, available_width * 0.26,
                        available_width * 0.13, available_width * 0.18,
                        available_width * 0.20,
                    ]
                    table_data = [header_row]
                    for index, txn in enumerate(transactions, start=1):
                        table_data.append([
                            Paragraph(str(index), get_cached_style('table_cell_center_sm')),
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center_sm')),
                            Paragraph(str(txn.get('product_names', '-')), get_cached_style('table_cell_sm')),
                            Paragraph(str(txn.get('invoice_id', '-') or '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.normalize_payment_method(txn.get('payment_method')), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right_sm')),
                        ])

                elif txn_type == 'expenses':
                    header_row = [
                        Paragraph("Date & Time", get_cached_style('table_header')),
                        Paragraph("Name", get_cached_style('table_header_left')),
                        Paragraph("Type", get_cached_style('table_header')),
                        Paragraph("Payment Method", get_cached_style('table_header')),
                        Paragraph("Amount", get_cached_style('table_header_right')),
                    ]
                    col_widths = [
                        available_width * 0.22,
                        available_width * 0.28,
                        available_width * 0.18,
                        available_width * 0.16,
                        available_width * 0.16,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        row = [
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center')),
                            Paragraph(str(txn.get('name', '')), get_cached_style('table_cell')),
                            Paragraph(txn.get('sub_type', '-'), get_cached_style('table_cell_center')),
                            Paragraph(LedgerBookPDFService.normalize_payment_method(txn.get('payment_method')), get_cached_style('table_cell_center')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right')),
                        ]
                        table_data.append(row)                    

                elif txn_type == 'payments_without_security':
                    header_row = [
                        Paragraph("Date & Time", get_cached_style('table_header_sm')),
                        Paragraph("Customer Name", get_cached_style('table_header_left_sm')),
                        Paragraph("Invoice ID", get_cached_style('table_header_sm')),
                        Paragraph("Type", get_cached_style('table_header_sm')),
                        Paragraph("Payment Method", get_cached_style('table_header_sm')),
                        Paragraph("Amount", get_cached_style('table_header_right_sm')),
                    ]
                    col_widths = [
                        available_width * 0.22,
                        available_width * 0.20,
                        available_width * 0.13,
                        available_width * 0.16,
                        available_width * 0.14,
                        available_width * 0.15,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        row = [
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center_sm')),
                            Paragraph(str(txn.get('name', '-')), get_cached_style('table_cell_sm')),
                            Paragraph(str(txn.get('invoice_id', '-') or '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(txn.get('sub_type', '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.normalize_payment_method(txn.get('payment_method')), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right_sm')),
                        ]
                        table_data.append(row)

                elif txn_type == 'payments':
                    header_row = [
                        Paragraph("Date & Time", get_cached_style('table_header_sm')),
                        Paragraph("Customer Name", get_cached_style('table_header_left_sm')),
                        Paragraph("Invoice ID", get_cached_style('table_header_sm')),
                        Paragraph("Type", get_cached_style('table_header_sm')),
                        Paragraph("Payment Method", get_cached_style('table_header_sm')),
                        Paragraph("Amount", get_cached_style('table_header_right_sm')),
                    ]
                    col_widths = [
                        available_width * 0.22,
                        available_width * 0.20,
                        available_width * 0.13,
                        available_width * 0.16,
                        available_width * 0.14,
                        available_width * 0.15,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        row = [
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center_sm')),
                            Paragraph(str(txn.get('name', '-')), get_cached_style('table_cell_sm')),
                            Paragraph(str(txn.get('invoice_id', '-') or '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(txn.get('sub_type', '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.normalize_payment_method(txn.get('payment_method')), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right_sm')),
                        ]
                        table_data.append(row)

                elif txn_type == 'pendings':
                    header_row = [
                        Paragraph("Date", get_cached_style('table_header_sm')),
                        Paragraph("Customer Name", get_cached_style('table_header_left_sm')),
                        Paragraph("Invoice ID", get_cached_style('table_header_sm')),
                        Paragraph("Product Name", get_cached_style('table_header_left_sm')),
                        Paragraph("Phone", get_cached_style('table_header_sm')),
                        Paragraph("Amount", get_cached_style('table_header_right_sm')),
                    ]
                    col_widths = [
                        available_width * 0.14, available_width * 0.16,
                        available_width * 0.13, available_width * 0.25,
                        available_width * 0.17, available_width * 0.15,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        item_text = str(txn.get('items', '-'))
                        if not item_text or item_text.strip() == '':
                            item_text = '-'
                        if len(item_text) > 50:
                            item_text = item_text[:50] + '...'
                        table_data.append([
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center_sm')),
                            Paragraph(str(txn.get('name', '-')), get_cached_style('table_cell_sm')),
                            Paragraph(str(txn.get('invoice_id', '-') or '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(item_text, get_cached_style('table_cell_sm')),
                            Paragraph(str(txn.get('client_phone', '-')), get_cached_style('table_cell_center_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right_sm')),
                        ])

                elif txn_type == 'bookings':
                    header_row = [
                        Paragraph("S.No.", get_cached_style('table_header_sm')),
                        Paragraph("Date", get_cached_style('table_header_sm')),
                        Paragraph("Product", get_cached_style('table_header_left_sm')),
                        Paragraph("Invoice ID", get_cached_style('table_header_sm')),
                        Paragraph("Name", get_cached_style('table_header_left_sm')),
                        Paragraph("Paid", get_cached_style('table_header_right_sm')),
                        Paragraph("Pending", get_cached_style('table_header_right_sm')),
                        Paragraph("Total", get_cached_style('table_header_right_sm')),
                    ]
                    col_widths = [
                        available_width * 0.07,
                        available_width * 0.13, available_width * 0.20,
                        available_width * 0.13, available_width * 0.14,
                        available_width * 0.11, available_width * 0.11,
                        available_width * 0.11,
                    ]
                    table_data = [header_row]
                    for index, txn in enumerate(transactions, start=1):
                        table_data.append([
                            Paragraph(str(index), get_cached_style('table_cell_center_sm')),
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center_sm')),
                            Paragraph(str(txn.get('product_names', '-')), get_cached_style('table_cell_sm')),
                            Paragraph(str(txn.get('invoice_id', '-') or '-'), get_cached_style('table_cell_center_sm')),
                            Paragraph(str(txn.get('name', '')), get_cached_style('table_cell_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('paid_amount', 0)), get_cached_style('table_cell_right_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('pending_amount', 0)), get_cached_style('table_cell_right_sm')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right_sm')),
                        ])

                elif txn_type == 'tailor_orders':
                    header_row = [
                        Paragraph("Date & Time", get_cached_style('table_header')),
                        Paragraph("ID", get_cached_style('table_header')),
                        Paragraph("Client Phone", get_cached_style('table_header')),
                        Paragraph("Paid Amount", get_cached_style('table_header_right')),
                    ]
                    col_widths = [
                        available_width * 0.30, available_width * 0.20,
                        available_width * 0.25, available_width * 0.25,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        table_data.append([
                            Paragraph(txn.get('created_at', txn.get('date', '')), get_cached_style('table_cell_center')),
                            Paragraph(str(txn.get('invoice_id', '-')), get_cached_style('table_cell_center')),
                            Paragraph(str(txn.get('client_phone', '-')), get_cached_style('table_cell_center')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('paid_amount', 0)), get_cached_style('table_cell_right')),
                        ])

                else:
                    header_row = [
                        Paragraph("Date", get_cached_style('table_header')),
                        Paragraph("Name", get_cached_style('table_header_left')),
                        Paragraph("Amount", get_cached_style('table_header_right')),
                    ]
                    col_widths = [
                        available_width * 0.25, available_width * 0.50, available_width * 0.25,
                    ]
                    table_data = [header_row]
                    for txn in transactions:
                        table_data.append([
                            Paragraph(txn.get('date', ''), get_cached_style('table_cell_center')),
                            Paragraph(str(txn.get('name', '')), get_cached_style('table_cell')),
                            Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right')),
                        ])

            else:
                # MIXED TRANSACTION TYPES
                header_row = [
                    Paragraph("Date", get_cached_style('table_header')),
                    Paragraph("Type", get_cached_style('table_header')),
                    Paragraph("Customer/Category", get_cached_style('table_header_left')),
                    Paragraph("Phone", get_cached_style('table_header')),
                    Paragraph("Amount", get_cached_style('table_header_right')),
                ]
                col_widths = [
                    available_width * 0.25, available_width * 0.15,
                    available_width * 0.25, available_width * 0.20,
                    available_width * 0.15,
                ]
                table_data = [header_row]
                for txn in transactions:
                    txn_type = txn.get('type', 'unknown').capitalize()
                    table_data.append([
                        Paragraph(txn.get('date', ''), get_cached_style('table_cell_center')),
                        Paragraph(txn_type, get_cached_style('table_cell_center')),
                        Paragraph(str(txn.get('name', '')), get_cached_style('table_cell')),
                        Paragraph(str(txn.get('client_phone', '-')), get_cached_style('table_cell_center')),
                        Paragraph(LedgerBookPDFService.format_currency(txn.get('amount', 0)), get_cached_style('table_cell_right')),
                    ])

            # Build and style the transactions table
            transactions_table = Table(table_data, colWidths=col_widths, repeatRows=1)

            table_style = [
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                ("LEFTPADDING", (0, 0), (-1, 0), 8),
                ("RIGHTPADDING", (0, 0), (-1, 0), 8),
                ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("TOPPADDING", (0, 1), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
                ("LEFTPADDING", (0, 1), (-1, -1), 8),
                ("RIGHTPADDING", (0, 1), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [TABLE_ROW_MAIN_COLOR, TABLE_ROW_ALT_COLOR]),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_HEADER_BG),
                ("LINEBELOW", (0, 1), (-1, -2), 0.5, colors.HexColor("#E5E7EB")),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("LINEBEFORE", (0, 0), (0, -1), 1, colors.HexColor("#D1D5DB")),
                ("LINEAFTER", (-1, 0), (-1, -1), 1, colors.HexColor("#D1D5DB")),
            ]

            if txn_type == 'bookings':
                table_style.extend([
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),   # S.No.
                    ("ALIGN", (1, 1), (1, -1), "CENTER"),   # Date
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),   # Invoice ID
                    ("ALIGN", (5, 1), (7, -1), "RIGHT"),    # Paid, Pending, Total
                    # Narrow S.No. column: smaller padding so "S.No." never wraps
                    ("LEFTPADDING", (0, 0), (0, -1), 4),
                    ("RIGHTPADDING", (0, 0), (0, -1), 4),
                ])
            elif txn_type == 'sales':
                table_style.extend([
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),   # S.No.
                    ("ALIGN", (1, 1), (1, -1), "CENTER"),   # Date
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),   # Invoice ID
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),   # Payment Method
                    ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),  # Amount
                    # Narrow S.No. column: smaller padding so "S.No." never wraps
                    ("LEFTPADDING", (0, 0), (0, -1), 4),
                    ("RIGHTPADDING", (0, 0), (0, -1), 4),
                ])
            elif txn_type in ('payments', 'payments_without_security'):
                table_style.extend([
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),   # Date
                    ("ALIGN", (1, 1), (1, -1), "LEFT"),      # Name
                    ("ALIGN", (2, 1), (2, -1), "CENTER"),    # Invoice ID
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),    # Type
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),    # Method
                    ("ALIGN", (5, 1), (5, -1), "RIGHT"),     # Amount
                ])
            elif txn_type == 'expenses':
                table_style.extend([
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),   # Date
                    ("ALIGN", (1, 1), (1, -1), "LEFT"),      # Name
                    ("ALIGN", (2, 1), (2, -1), "CENTER"),    # Type
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),    # Method
                    ("ALIGN", (4, 1), (4, -1), "RIGHT"),     # Amount
                ])
            elif txn_type == 'pendings':
                table_style.extend([
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),   # Date
                    ("ALIGN", (2, 1), (2, -1), "CENTER"),   # Invoice ID
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),   # Phone
                    ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),  # Amount
                ])
            elif txn_type == 'security':
                table_style.extend([
                    ("ALIGN", (0, 1), (1, -1), "CENTER"),
                    ("ALIGN", (2, 1), (2, -1), "LEFT"),
                    ("ALIGN", (3, 1), (5, -1), "RIGHT"),
                ])
            else:
                table_style.extend([
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),
                    ("ALIGN", (1, 1), (1, -1), "CENTER"),
                    ("ALIGN", (2, 1), (2, -1), "LEFT"),
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),
                    ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
                ])

            transactions_table.setStyle(TableStyle(table_style))
            elements.append(transactions_table)

        else:
            no_data_style = ParagraphStyle(
                'NoData',
                parent=getSampleStyleSheet()['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=12,
                leading=16,
                textColor=DARK_GREY,
                alignment=TA_CENTER,
            )
            elements.append(Paragraph("No transactions found for the selected period", no_data_style))

        # ===== BUILD PDF WITH PAGE NUMBERS =====
        try:
            doc.build(elements, canvasmaker=PageNumberCanvas)
        except Exception as e:
            logger.error(f"PDF generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate PDF: {e}")

        buffer.seek(0)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes