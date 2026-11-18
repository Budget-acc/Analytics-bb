import calendar
import re

import xlsxwriter

from ERP.utils.number_format import INDIAN_EXCEL_NUM_FORMAT

REPORT_TITLE = "Staff Earnings Report"

# (header, width) of the transaction table. The summary block above it reuses
# the same columns: label merged across 0-2, Count in 3, Amount in 4.
_COLUMNS = (
    ("Sl. No.", 8),
    ("Date", 14),
    ("Invoice ID", 18),
    ("Transaction Type", 20),
    ("Amount", 16),
)
_DATE_FORMAT = "%d/%m/%Y"
# Summary rows left out when their count and amount are both 0.
_HIDE_WHEN_EMPTY = {"Bookings", "Sales", "Custom Work"}


def _safe(value, fallback):
    return re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_") or fallback


def build_excel_filename(shop_name, staff_name, year, month):
    """<Shop>_<Staff>_Performance_Report_<Mon>-<YYYY>.xlsx, filesystem-safe."""
    return (
        f"{_safe(shop_name, 'Shop')}_{_safe(staff_name, 'Staff')}_Performance_Report_"
        f"{calendar.month_abbr[month]}-{year}.xlsx"
    )


def build_summary(bookings_data, sales_data, tailor_data, pending_data):
    """Summary figures exactly as get_earnings_report builds them, from the
    same _get_staff_*_data / _get_pending_bookings_data results."""
    bookings_amount = bookings_data.get("total_bookings_earned_amount", 0) or 0
    sales_amount = sales_data.get("total_sales_earning", 0) or 0
    tailor_amount = tailor_data.get("total_tailor_order_earnings", 0) or 0
    bookings_count = bookings_data.get("total_bookings_count", 0) or 0
    sales_count = sales_data.get("total_sales_count", 0) or 0
    tailor_count = tailor_data.get("total_tailor_order_count", 0) or 0
    return {
        "bookings_count": bookings_count,
        "bookings_amount": bookings_amount,
        "sales_count": sales_count,
        "sales_amount": sales_amount,
        "tailor_count": tailor_count,
        "tailor_amount": tailor_amount,
        "total_count": bookings_count + sales_count + tailor_count,
        "total_amount": bookings_amount + sales_amount + tailor_amount,
        "pending_count": pending_data.get("pending_bookings_count", 0) or 0,
        "pending_amount": pending_data.get("pending_bookings_amount", 0) or 0,
    }


def write_staff_earnings_excel(output, shop_name, staff_name, year, month, summary, rows):
    """
    Writes the monthly staff earnings report into `output` (a BytesIO; built
    in memory, no temp file): a summary block (same figures as the
    earnings-report API) followed by the transaction table (rows from
    _get_staff_monthly_transaction_rows, already sorted earliest first).
    Returns the number of transaction rows written.
    """
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    try:
        sheet = workbook.add_worksheet("Earnings")
        title_fmt = workbook.add_format({"bold": True, "font_size": 13})
        header_fmt = workbook.add_format({
            "bold": True, "bg_color": "#E5D8EF", "border": 1,
            "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        label_fmt = workbook.add_format({"border": 1, "valign": "vcenter"})
        center_fmt = workbook.add_format({"border": 1, "align": "center", "valign": "vcenter"})
        money_fmt = workbook.add_format({"border": 1, "valign": "vcenter", "num_format": INDIAN_EXCEL_NUM_FORMAT})
        bold_label_fmt = workbook.add_format({"bold": True, "border": 1, "valign": "vcenter"})
        bold_center_fmt = workbook.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter"})
        bold_money_fmt = workbook.add_format({
            "bold": True, "border": 1, "valign": "vcenter", "num_format": INDIAN_EXCEL_NUM_FORMAT,
        })

        last_col = len(_COLUMNS) - 1
        for col, (_, width) in enumerate(_COLUMNS):
            sheet.set_column(col, col, width)

        sheet.merge_range(
            0, 0, 0, last_col,
            f"{shop_name} - {REPORT_TITLE} - {staff_name} ({calendar.month_name[month]} {year})",
            title_fmt,
        )

        # ----- Summary block -----
        row = 2
        sheet.merge_range(row, 0, row, 2, "Summary", header_fmt)
        sheet.write(row, 3, "Count", header_fmt)
        sheet.write(row, 4, "Amount", header_fmt)
        summary_rows = (
            ("Bookings", summary["bookings_count"], summary["bookings_amount"], False),
            ("Sales", summary["sales_count"], summary["sales_amount"], False),
            ("Custom Work", summary["tailor_count"], summary["tailor_amount"], False),
            ("Total Earnings", summary["total_count"], summary["total_amount"], True),
            ("Pending Bookings", summary["pending_count"], summary["pending_amount"], False),
        )
        for label, count, amount, bold in summary_rows:
            if label in _HIDE_WHEN_EMPTY and not count and not amount:
                continue
            row += 1
            sheet.merge_range(row, 0, row, 2, label, bold_label_fmt if bold else label_fmt)
            sheet.write_number(row, 3, count, bold_center_fmt if bold else center_fmt)
            sheet.write_number(row, 4, float(amount), bold_money_fmt if bold else money_fmt)

        # ----- Transaction table -----
        row += 2
        header_row = row
        for col, (header, _) in enumerate(_COLUMNS):
            sheet.write(header_row, col, header, header_fmt)

        if not rows:
            row += 1
            sheet.merge_range(row, 0, row, last_col, "No transactions for this month", center_fmt)
            return 0

        for serial, item in enumerate(rows, start=1):
            row += 1
            sheet.write_number(row, 0, serial, center_fmt)
            sheet.write_string(row, 1, item["date"].strftime(_DATE_FORMAT), center_fmt)
            sheet.write_string(row, 2, str(item["invoice_id"]), center_fmt)
            sheet.write_string(row, 3, item["transaction_type"], center_fmt)
            sheet.write_number(row, 4, float(item["amount"]), money_fmt)

        first_data_row = header_row + 1
        last_data_row = row
        row += 1
        sheet.merge_range(row, 0, row, 3, "Total", bold_label_fmt)
        sheet.write_formula(
            row, 4, f"=SUM(E{first_data_row + 1}:E{last_data_row + 1})", bold_money_fmt,
            float(sum(item["amount"] for item in rows)),
        )
        sheet.autofilter(header_row, 0, last_data_row, last_col)
        return len(rows)
    finally:
        workbook.close()
