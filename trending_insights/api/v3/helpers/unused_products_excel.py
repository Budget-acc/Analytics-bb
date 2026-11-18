import re
from datetime import timedelta

import xlsxwriter

from .unused_products import get_product_usage

REPORT_KEY = "unused_products"
REPORT_TITLE = "Unused & Low-Usage Products"
ROW_CHUNK_SIZE = 2000

# (header, width) - "Color", "Model", "Category" sit under a merged
# "Specification" header.
_COLUMNS = (
    ("S.No", 7),
    ("Product Name", 34),
    ("Color", 16),
    ("Model", 16),
    ("Category", 22),
    ("Number of Orders", 16),
    ("Last Ordered Date", 18),
)
_SPEC_FIRST_COL, _SPEC_LAST_COL = 2, 4
_DATE_FORMAT = "%d-%m-%Y"


def build_excel_filename(shop_name, start, end):
    """<Shop>_Unused_Products_<DD-MM-YYYY>_<DD-MM-YYYY>.xlsx, filesystem-safe."""
    safe_shop = re.sub(r"[^A-Za-z0-9]+", "_", shop_name or "Shop").strip("_") or "Shop"
    last_day = end - timedelta(days=1)  # end is exclusive
    return f"{safe_shop}_Unused_Products_{start:%d-%m-%Y}_{last_day:%d-%m-%Y}.xlsx"


def write_unused_products_excel(file_path, shop_id, shop_name, start, end):
    """
    Writes the Unused & Low-Usage Products report to `file_path` using the
    same get_product_usage() calculation as the Unused Products API.

    Memory-flat for any catalog size: xlsxwriter runs in constant_memory
    mode (each row is flushed to disk once written) and rows are streamed
    from a server-side cursor as plain tuples (no model instances), in
    chunks of ROW_CHUNK_SIZE. Returns the number of product rows written.
    """
    rows = get_product_usage(shop_id, start, end).values_list(
        "name", "color", "model", "category", "ordered_count", "last_ordered_date",
    )

    workbook = xlsxwriter.Workbook(file_path, {"constant_memory": True})
    try:
        sheet = workbook.add_worksheet("Products")
        title_fmt = workbook.add_format({"bold": True, "font_size": 13})
        header_fmt = workbook.add_format({
            "bold": True, "bg_color": "#E5D8EF", "border": 1,
            "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        cell_fmt = workbook.add_format({"border": 1, "valign": "vcenter"})
        center_fmt = workbook.add_format({"border": 1, "align": "center", "valign": "vcenter"})

        for col, (_, width) in enumerate(_COLUMNS):
            sheet.set_column(col, col, width)

        # constant_memory requires rows to be written strictly top-down.
        last_day = end - timedelta(days=1)
        sheet.merge_range(
            0, 0, 0, len(_COLUMNS) - 1,
            f"{shop_name} - {REPORT_TITLE} ({start:{_DATE_FORMAT}} to {last_day:{_DATE_FORMAT}})",
            title_fmt,
        )

        # Header row 1: top-level headers, with "Specification" merged across
        # its 3 columns. Vertical merges are avoided: in constant_memory mode
        # touching row 2 flushes row 1, so every row-1 cell is written first.
        for col, (header, _) in enumerate(_COLUMNS):
            if not _SPEC_FIRST_COL <= col <= _SPEC_LAST_COL:
                sheet.write(1, col, header, header_fmt)
        sheet.merge_range(1, _SPEC_FIRST_COL, 1, _SPEC_LAST_COL, "Specification", header_fmt)
        # Header row 2: Specification sub-headers; other columns stay as a
        # blank continuation of their header cell.
        for col, (header, _) in enumerate(_COLUMNS):
            if _SPEC_FIRST_COL <= col <= _SPEC_LAST_COL:
                sheet.write(2, col, header, header_fmt)
            else:
                sheet.write_blank(2, col, None, header_fmt)

        sheet.freeze_panes(3, 0)

        row = 3
        serial = 0
        for name, color, model, category, ordered_count, last_ordered_date in rows.iterator(
            chunk_size=ROW_CHUNK_SIZE
        ):
            serial += 1
            sheet.write_number(row, 0, serial, center_fmt)
            sheet.write_string(row, 1, name or "", cell_fmt)
            sheet.write_string(row, 2, color or "", cell_fmt)
            sheet.write_string(row, 3, model or "", cell_fmt)
            sheet.write_string(row, 4, category or "", cell_fmt)
            sheet.write_number(row, 5, ordered_count or 0, center_fmt)
            sheet.write_string(
                row, 6,
                last_ordered_date.strftime(_DATE_FORMAT) if last_ordered_date else "",
                center_fmt,
            )
            row += 1

        if serial:
            sheet.autofilter(2, 0, row - 1, len(_COLUMNS) - 1)
    finally:
        workbook.close()

    return serial
