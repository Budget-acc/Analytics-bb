import calendar
import re

import xlsxwriter
from django.utils.timezone import localtime

REPORT_TITLE = "Client Details"
ROW_CHUNK_SIZE = 2000

# (header, width) - same columns, in the same order, as the Client Details PDF.
_COLUMNS = (
    ("S.No.", 8),
    ("Date", 14),
    ("Name", 32),
    ("Phone Number 1", 20),
    ("Phone Number 2", 20),
)
_DATE_FORMAT = "%d/%m/%Y"


def build_excel_filename(shop_name, start_date=None, end_date=None):
    """<Shop>_Client_Details_<dd-mm-yyyy>_to_<dd-mm-yyyy>.xlsx, or
    <Shop>_Client_Details_All.xlsx when there is no date range. Filesystem-safe."""
    safe_shop = re.sub(r"[^A-Za-z0-9]+", "_", shop_name or "Shop").strip("_") or "Shop"
    if start_date is None or end_date is None:
        return f"{safe_shop}_Client_Details_All.xlsx"
    return f"{safe_shop}_Client_Details_{start_date:%d-%m-%Y}_to_{end_date:%d-%m-%Y}.xlsx"


def write_client_details_excel(output, shop_name, start_date, end_date, clients):
    """
    Writes the Client Details report into `output` (BytesIO, built in memory).

    `clients` is client_details_queryset(...) - already newest first. Values
    match the PDF (Date = local created_at dd/mm/yyyy, phones or '-'); the
    only addition is a month heading (e.g. "July 2026") before each month's
    clients. S.No. is continuous across months, as in the PDF.
    start_date / end_date are the requested range, or None for all clients.
    Returns the number of client rows written.
    """
    has_range = start_date is not None and end_date is not None
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    try:
        sheet = workbook.add_worksheet("Clients")
        title_fmt = workbook.add_format({"bold": True, "font_size": 13})
        header_fmt = workbook.add_format({
            "bold": True, "bg_color": "#E5D8EF", "border": 1,
            "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        month_fmt = workbook.add_format({
            "bold": True, "bg_color": "#F3EEF8", "border": 1, "valign": "vcenter",
        })
        cell_fmt = workbook.add_format({"border": 1, "valign": "vcenter"})
        center_fmt = workbook.add_format({"border": 1, "align": "center", "valign": "vcenter"})

        last_col = len(_COLUMNS) - 1
        for col, (_, width) in enumerate(_COLUMNS):
            sheet.set_column(col, col, width)

        period = (
            f"{start_date:{_DATE_FORMAT}} to {end_date:{_DATE_FORMAT}}" if has_range else "All Clients"
        )
        sheet.merge_range(0, 0, 0, last_col, f"{shop_name} - {REPORT_TITLE} ({period})", title_fmt)

        header_row = 2
        for col, (header, _) in enumerate(_COLUMNS):
            sheet.write(header_row, col, header, header_fmt)
        sheet.freeze_panes(header_row + 1, 0)

        row = header_row
        serial = 0
        current_month = None
        for client in clients.iterator(chunk_size=ROW_CHUNK_SIZE):
            created = localtime(client.created_at) if client.created_at else None
            month_key = (created.year, created.month) if created else None
            if month_key != current_month:
                current_month = month_key
                row += 1
                heading = f"{calendar.month_name[created.month]} {created.year}" if created else "No Date"
                sheet.merge_range(row, 0, row, last_col, heading, month_fmt)

            serial += 1
            row += 1
            sheet.write_number(row, 0, serial, center_fmt)
            sheet.write_string(row, 1, created.strftime(_DATE_FORMAT) if created else "-", center_fmt)
            sheet.write_string(row, 2, str(client.client_name or ""), cell_fmt)
            sheet.write_string(row, 3, str(client.phone_1_e164) if client.phone_1_e164 else "-", center_fmt)
            sheet.write_string(row, 4, str(client.phone_2_e164) if client.phone_2_e164 else "-", center_fmt)

        if not serial:
            sheet.merge_range(
                header_row + 1, 0, header_row + 1, last_col,
                "No clients found for the selected date range" if has_range else "No clients found",
                center_fmt,
            )
        return serial
    finally:
        workbook.close()
