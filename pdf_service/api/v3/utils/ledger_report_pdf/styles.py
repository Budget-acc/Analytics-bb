from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from . import constants

# ============================================================================
# GLOBAL CACHES
# ============================================================================
_STYLE_CACHE = {}


# ============================================================================
# CACHED STYLES
# ============================================================================
def get_cached_style(name, **kwargs):
    """Get or create cached paragraph style."""
    cache_key = f"{name}_{hash(frozenset(kwargs.items()))}"
    if cache_key not in _STYLE_CACHE:
        styles = getSampleStyleSheet()

        if name == 'title':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'Title',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=26,
                leading=22,
                textColor=constants.BLACK,
                alignment=TA_LEFT,
                spaceAfter=14
            )
        elif name == 'shop_name':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'ShopName',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=14,
                leading=18,
                textColor=constants.CUSTOM_PURPLE,
                alignment=TA_LEFT,
                spaceAfter=4
            )
        elif name == 'shop_address':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'ShopAddress',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=9,
                leading=12,
                textColor=constants.DARK_GREY,
                alignment=TA_LEFT,
                spaceAfter=2
            )
        elif name == 'shop_contact':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'ShopContact',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=9,
                leading=12,
                textColor=constants.DARK_GREY,
                alignment=TA_LEFT,
                spaceAfter=2
            )
        elif name == 'period':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'Period',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=12,
                leading=16,
                textColor=constants.BLACK,
                alignment=TA_LEFT,
                spaceAfter=20
            )
        elif name == 'section_title':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SectionTitle',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=14,
                leading=18,
                textColor=constants.BLACK,
                alignment=TA_LEFT,
                spaceAfter=10
            )
        elif name == 'expense_breakdown_title':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SectionTitle',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=12,
                leading=14,
                textColor=constants.BLACK,
                alignment=TA_LEFT,
                spaceAfter=8
            )
        elif name == 'summary_label':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummaryLabel',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=11,
                leading=15,
                textColor=constants.BLACK,  # Changed from DARK_GREY to BLACK
                alignment=TA_LEFT,
                spaceAfter=4
            )
        elif name == 'expense_breakdown_label':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummaryLabel',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=9,
                leading=12,
                textColor=constants.BLACK,  # Changed from DARK_GREY to BLACK
                alignment=TA_LEFT,
                spaceAfter=3
            )
        elif name == 'summary_value':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummaryValue',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=11,
                leading=18,
                textColor=constants.BLACK,  # Changed from BLACK to match reference
                alignment=TA_RIGHT,
                spaceAfter=4
            )
        elif name == 'summary_label_bold':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummaryLabelBold',
                parent=get_cached_style('summary_label'),
                fontName=constants.BOLD_FONT,
                fontSize=14,
            )
        elif name == 'summary_value_bold':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummaryValueBold',
                parent=get_cached_style('summary_value'),
                fontName=constants.BOLD_FONT,
                fontSize=14,
                alignment=TA_RIGHT,
            )
        elif name == 'summary_section_title':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummarySectionTitle',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=8,
                textColor=constants.DARK_GREY,
                spaceBefore=2,
                spaceAfter=3,
                leading=10,
            )

        elif name == 'payment_split_line':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'PaymentSplitLine',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=8,
                leading=10,
                textColor=constants.DARK_GREY,
                alignment=TA_LEFT,
                spaceAfter=4,
            )

        elif name == 'expense_breakdown_value':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'SummaryValue',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=11,
                leading=15,
                textColor=constants.BLACK,  # Changed from BLACK to match reference
                alignment=TA_RIGHT,
                spaceAfter=3
            )
        elif name == 'table_header':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableHeader',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=10,
                textColor=constants.TABLE_HEADER_TEXT,
                alignment=TA_CENTER,
                spaceAfter=0
            )
        elif name == 'table_header_left':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableHeader',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=10,
                textColor=constants.TABLE_HEADER_TEXT,
                alignment=TA_LEFT,
                spaceAfter=0
            )
        elif name == 'table_header_right':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableHeader',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=10,
                textColor=constants.TABLE_HEADER_TEXT,
                alignment=TA_RIGHT,
                spaceAfter=0
            )
        elif name == 'table_cell':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableCell',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=10,
                textColor=constants.BLACK,
                alignment=TA_LEFT,
                spaceAfter=0
            )
        elif name == 'table_cell_center':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableCellCenter',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=10,
                textColor=constants.BLACK,
                alignment=TA_CENTER,
                spaceAfter=0
            )
        elif name == 'table_cell_right':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableCellRight',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=10,
                textColor=constants.BLACK,
                alignment=TA_RIGHT,
                spaceAfter=0
            )
        elif name == 'page_number':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'PageNumber',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=10,
                textColor=constants.BLACK,
                alignment=TA_CENTER,
                spaceAfter=0
            )

        # ---- Smaller-font variants used only by the 5 ledger tables that
        # gained an Invoice ID column (bookings, sales, payments,
        # payments_without_security, pendings) — kept separate from the
        # fontSize=10 styles above so every other report table (security,
        # expenses, tailor_orders, mixed) is completely unaffected. ----
        elif name == 'table_header_sm':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableHeaderSm',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=9,
                textColor=constants.TABLE_HEADER_TEXT,
                alignment=TA_CENTER,
                spaceAfter=0
            )
        elif name == 'table_header_left_sm':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableHeaderLeftSm',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=9,
                textColor=constants.TABLE_HEADER_TEXT,
                alignment=TA_LEFT,
                spaceAfter=0
            )
        elif name == 'table_header_right_sm':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableHeaderRightSm',
                parent=styles['Normal'],
                fontName=constants.BOLD_FONT,
                fontSize=9,
                textColor=constants.TABLE_HEADER_TEXT,
                alignment=TA_RIGHT,
                spaceAfter=0
            )
        elif name == 'table_cell_sm':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableCellSm',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=9,
                textColor=constants.BLACK,
                alignment=TA_LEFT,
                spaceAfter=0
            )
        elif name == 'table_cell_center_sm':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableCellCenterSm',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=9,
                textColor=constants.BLACK,
                alignment=TA_CENTER,
                spaceAfter=0
            )
        elif name == 'table_cell_right_sm':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'TableCellRightSm',
                parent=styles['Normal'],
                fontName=constants.BASE_FONT,
                fontSize=9,
                textColor=constants.BLACK,
                alignment=TA_RIGHT,
                spaceAfter=0
            )

    return _STYLE_CACHE[cache_key]
