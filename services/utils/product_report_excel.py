# product_report_excel.py
import io
import logging
import re
from typing import Iterable, List, Optional, Tuple

import xlsxwriter

from ERP.utils.number_format import INDIAN_EXCEL_NUM_FORMAT

logger = logging.getLogger("api_requests")

HEADERS = [
    "S.No", "Product Name", "Category", "Model", "Color",
    "Quantity", "Vendor Price", "Rental Price", "Sale Price",
]

COLUMN_WIDTHS = [8, 30, 16, 16, 14, 26, 14, 14, 14]


def _sanitize_filename_segment(value: str, max_len: int = 50) -> str:
    """Replace non-alphanumeric characters (except _ and -) and truncate."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', (value or '').strip())[:max_len]


def build_excel_filename(shop_name: str, service_name: str, suffix: str = '') -> str:
    """Build the download filename, e.g. shop 'TV Shop' + service 'Grooms' -> 'TV_Shop_Grooms.xlsx'."""
    shop_seg = _sanitize_filename_segment(shop_name) or "Shop"
    service_seg = _sanitize_filename_segment(service_name) or "Products"
    suffix_seg = f"_{_sanitize_filename_segment(suffix)}" if suffix else ""
    return f"{shop_seg}_{service_seg}{suffix_seg}.xlsx"


class ProductReportExcelService:
    """Lightweight product stock Excel export - no images, streamed row writes."""

    @staticmethod
    def estimate_generation_time(product_count: int) -> Tuple[int, str]:
        """Estimate time based on product count. Excel has no image/render cost, so this
        is scaled well below the PDF service's tiering."""
        if product_count <= 1000:
            estimated_seconds = max(1, product_count * 0.002)
        elif product_count <= 5000:
            estimated_seconds = max(2, product_count * 0.0015)
        else:
            estimated_seconds = max(5, product_count * 0.001)

        if estimated_seconds < 60:
            time_str = f"{int(estimated_seconds) or 1} seconds"
        else:
            minutes = int(estimated_seconds / 60)
            seconds = int(estimated_seconds % 60)
            time_str = f"{minutes}m {seconds}s"

        return int(estimated_seconds), time_str

    @staticmethod
    def _safe_get_attr(obj, attr, default=''):
        try:
            value = getattr(obj, attr, default)
            return value if value is not None else default
        except Exception:
            return default

    @staticmethod
    def _is_materials_product(product) -> bool:
        try:
            main_category = (
                product.shop_service.general_service.main_category.name
                if product.shop_service
                and product.shop_service.general_service
                and product.shop_service.general_service.main_category
                else None
            )
            return bool(main_category) and main_category.strip().lower() == 'materials'
        except AttributeError:
            return False

    @staticmethod
    def _quantity_cell_value(product, is_materials: bool) -> str:
        variants = list(product.variants.all())

        if not variants:
            return "0"

        if len(variants) == 1 and not (variants[0].attribute or '').strip():
            qty = variants[0].fabric_length if is_materials else variants[0].stock
            return str(qty if qty is not None else 0)

        lines = []
        for variant in variants:
            qty = variant.fabric_length if is_materials else variant.stock
            qty = qty if qty is not None else 0
            name = (variant.attribute or '').strip() or "Default"
            lines.append(f"{name} - {qty}")
        return "\n".join(lines)

    @staticmethod
    def _price_cell_value(value) -> str:
        if value is None:
            return "-"
        return f"{value}"

    @classmethod
    def generate_product_report_excel(
        cls,
        shop,
        products_data: Iterable,
        service_name: Optional[str] = None,
        search_term: Optional[str] = None,
        request_params: Optional[dict] = None,
    ) -> bytes:
        """
        Build the product stock Excel report.
        products_data may be a list or an iterable/queryset of Product objects with
        prefetched `variants` (see services.tasks.pdf_utils._build_streaming_products_queryset).
        """
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {'in_memory': True, 'constant_memory': True})
        worksheet = workbook.add_worksheet("Product Stock Report")

        header_fmt = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#F2F2F2', 'border': 1})
        cell_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        money_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': INDIAN_EXCEL_NUM_FORMAT})
        wrap_fmt = workbook.add_format({'align': 'left', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})

        for col, width in enumerate(COLUMN_WIDTHS):
            worksheet.set_column(col, col, width)

        for col, header in enumerate(HEADERS):
            worksheet.write(0, col, header, header_fmt)

        row = 1
        serial_no = 0

        for product in products_data:
            serial_no += 1
            is_materials = cls._is_materials_product(product)
            quantity_value = cls._quantity_cell_value(product, is_materials)

            worksheet.write(row, 0, serial_no, cell_fmt)
            worksheet.write(row, 1, cls._safe_get_attr(product, 'name', '-'), cell_fmt)
            worksheet.write(row, 2, cls._safe_get_attr(product, 'category', '-'), cell_fmt)
            worksheet.write(row, 3, cls._safe_get_attr(product, 'model', '-'), cell_fmt)
            worksheet.write(row, 4, cls._safe_get_attr(product, 'color', '-'), cell_fmt)
            worksheet.write(row, 5, quantity_value, wrap_fmt)

            purchase_price = cls._safe_get_attr(product, 'purchase_price', None)
            rental_price = cls._safe_get_attr(product, 'price', None)
            sale_price = cls._safe_get_attr(product, 'sale_price', None)

            if purchase_price is not None:
                worksheet.write_number(row, 6, float(purchase_price), money_fmt)
            else:
                worksheet.write(row, 6, "-", cell_fmt)

            if rental_price is not None:
                worksheet.write_number(row, 7, float(rental_price), money_fmt)
            else:
                worksheet.write(row, 7, "-", cell_fmt)

            if sale_price is not None:
                worksheet.write_number(row, 8, float(sale_price), money_fmt)
            else:
                worksheet.write(row, 8, "-", cell_fmt)

            if "\n" in quantity_value:
                worksheet.set_row(row, 15 * quantity_value.count("\n") + 20)

            row += 1

        worksheet.freeze_panes(1, 0)
        workbook.close()

        return buffer.getvalue()
