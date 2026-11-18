import os
import re
import html
import base64
import logging
import requests
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any
import json as json_module
from datetime import datetime, time, timedelta
from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db.models import Sum
from django.utils.timezone import localtime

from booking_v2.utils.measurements import ordered_value
from ERP.utils.number_format import format_inr

# WeasyPrint for PDF generation with full Malayalam support
# try:
#     from weasyprint import HTML, CSS
#     from weasyprint.text.fonts import FontConfiguration
#     WEASYPRINT_AVAILABLE = True
# except ImportError:
#     WEASYPRINT_AVAILABLE = False
#     raise ImportError("WeasyPrint is required. Install with: pip install WeasyPrint")

# logger = logging.getLogger("api_requests")

# # WeasyPrint for PDF generation with full Malayalam support
try:
    from weasyprint import HTML, CSS
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError) as e:
    WEASYPRINT_AVAILABLE = False
    import warnings
    warnings.warn(
        f"WeasyPrint not available: {e}. "
        "PDF generation will not work. "
        "On Windows, install GTK3 runtime: See INSTALL_GTK_WINDOWS.md"
    )
    # Provide dummy classes to prevent import errors
    class HTML:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("WeasyPrint not available - GTK3 runtime not installed")
    class CSS:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("WeasyPrint not available - GTK3 runtime not installed")
    class FontConfiguration:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("WeasyPrint not available - GTK3 runtime not installed")

@contextmanager
def _silence_pdf_generation_logs():
    """Temporarily silence noisy DEBUG logs during PDF generation."""
    noisy_logger_names = [
        # fontTools (used by WeasyPrint for font subsetting)
        "fontTools",
        "fontTools.ttLib",
        "fontTools.subset",
        "ttFont",
        "loggingTools",
        "__init__",
        # WeasyPrint
        "weasyprint",
    ]

    previous_levels = {}
    try:
        for name in noisy_logger_names:
            log = logging.getLogger(name)
            previous_levels[name] = log.level
            log.setLevel(logging.WARNING)
        yield
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)



# ============================================================================
# DEFAULT TERMS AND CONDITIONS (used when a shop has not set its own terms)
# Shared with invoice_templates/v3/services/terms.py - edit them only here.
# ============================================================================
DEFAULT_BOOKING_TERMS = (
    "No refund will be given after Booking cancellation.",
    "Products should be returned on the exact return date, without any damages.",
    "Any damages must be paid by the customer.",
    "Confirm your Pickup and return dates properly.",
    "Make sure all your needs are clear before booking.",
    "Security deposit (if applicable) will be refunded after return.",
)

DEFAULT_SALE_TERMS = (
    "Damage to the product after delivery will not be our responsibility.",
    "Refund or replacement will not be provided after the product is sold.",
    "Verify all products before leaving the shop.",
    "Products once sold will not be taken back or exchanged.",
)

DEFAULT_TAILOR_ORDER_TERMS = (
    "Alterations must be claimed within 7 days of delivery.",
    "No refund after work has commenced.",
    "Ensure measurements are confirmed before finalising the order.",
    "Security deposit (if applicable) will be refunded after delivery.",
)


# ============================================================================
# IMAGE DOWNLOAD SECURITY CONFIGURATION
# ============================================================================
class ImageDownloadConfig:
    """
    Security configuration for image downloads.
    
    Timeout Justification:
    - 5 seconds: Original timeout, too aggressive for slow S3 regions/CDNs
    - 10 seconds: Balanced timeout allowing for:
        * S3 cross-region requests (can take 3-7 seconds)
        * Network latency in production environments
        * Large logo images (up to 3MB as per upload limits)
    - Still protects against indefinite hangs
    
    Circuit Breaker:
    - Prevents repeated requests to failing endpoints
    - Reduces resource consumption on persistent failures
    """
    # Timeout in seconds (10s allows S3 cross-region + network latency)
    TIMEOUT = getattr(settings, 'IMAGE_DOWNLOAD_TIMEOUT', 10)
    
    # Maximum file size (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024
    
    # Circuit breaker settings
    FAILURE_THRESHOLD = 3  # Number of failures before circuit opens
    RECOVERY_TIMEOUT = 60  # Seconds before attempting recovery
    SUCCESS_THRESHOLD = 2  # Successful requests needed to close circuit
    
    # Rate limiting (per URL)
    MAX_REQUESTS_PER_MINUTE = 10


class CircuitBreaker:
    """
    Circuit breaker pattern for image downloads.
    Prevents repeated requests to failing endpoints.
    
    States:
    - CLOSED: Normal operation, requests allowed
    - OPEN: Too many failures, requests blocked
    - HALF_OPEN: Testing if service recovered
    """
    
    def __init__(self):
        self._failures = defaultdict(int)
        self._successes = defaultdict(int)
        self._last_failure_time = {}
        self._circuit_state = {}  # 'closed', 'open', 'half_open'
        self._lock = Lock()
    
    def call(self, url: str, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        with self._lock:
            state = self._circuit_state.get(url, 'closed')
            
            # Check if circuit is open
            if state == 'open':
                # Check if recovery timeout has passed
                last_failure = self._last_failure_time.get(url)
                if last_failure:
                    time_since_failure = (datetime.now() - last_failure).total_seconds()
                    if time_since_failure < ImageDownloadConfig.RECOVERY_TIMEOUT:
                        logger.warning(
                            f"[Circuit Breaker] Circuit OPEN for {url}. "
                            f"Retry in {ImageDownloadConfig.RECOVERY_TIMEOUT - time_since_failure:.0f}s"
                        )
                        return None
                    else:
                        # Move to half-open state
                        self._circuit_state[url] = 'half_open'
                        logger.info(f"[Circuit Breaker] Circuit HALF-OPEN for {url}, testing recovery")
        
        # Execute the function
        try:
            result = func(*args, **kwargs)
            
            if result is not None:
                self._record_success(url)
            else:
                self._record_failure(url)
            
            return result
            
        except Exception as e:
            self._record_failure(url)
            raise e
    
    def _record_success(self, url: str):
        """Record successful request."""
        with self._lock:
            self._successes[url] = self._successes.get(url, 0) + 1
            state = self._circuit_state.get(url, 'closed')
            
            if state == 'half_open':
                # Check if we have enough successes to close circuit
                if self._successes[url] >= ImageDownloadConfig.SUCCESS_THRESHOLD:
                    self._circuit_state[url] = 'closed'
                    self._failures[url] = 0
                    self._successes[url] = 0
                    logger.info(f"[Circuit Breaker] Circuit CLOSED for {url}")
            elif state == 'closed':
                # Reset failure count on success
                self._failures[url] = 0
    
    def _record_failure(self, url: str):
        """Record failed request."""
        with self._lock:
            self._failures[url] = self._failures.get(url, 0) + 1
            self._last_failure_time[url] = datetime.now()
            
            # Check if we should open the circuit
            if self._failures[url] >= ImageDownloadConfig.FAILURE_THRESHOLD:
                self._circuit_state[url] = 'open'
                logger.error(
                    f"[Circuit Breaker] Circuit OPENED for {url} "
                    f"after {self._failures[url]} failures"
                )
    
    def get_state(self, url: str) -> str:
        """Get current circuit state for URL."""
        return self._circuit_state.get(url, 'closed')


# Global circuit breaker instance
_circuit_breaker = CircuitBreaker()


# ============================================================================
# SVG BACKGROUND SUPPORT
# ============================================================================
def get_svg_background(relative_path: str) -> Optional[str]:
    """Get SVG background as base64 encoded data URL."""
    try:
        svg_path = BasePDFService.get_static_path(relative_path)
        if not svg_path or not os.path.exists(svg_path):
            logger.warning(f"SVG not found at: {relative_path}")
            return None
        
        with open(svg_path, 'rb') as f:
            svg_data = base64.b64encode(f.read()).decode('utf-8')
            return f"data:image/svg+xml;base64,{svg_data}"
    except Exception as e:
        logger.warning(f"Failed to load SVG background {relative_path}: {e}")
        return None


# ============================================================================
# BASE PDF SERVICE
# ============================================================================
class BasePDFService:
    """Base class for PDF generation with common utilities."""

    IST_OFFSET = timedelta(hours=5, minutes=30)

    @staticmethod
    def split_text_by_words(text: str, max_length: int = 28) -> str:
        """
        Split a long text into multiple lines at word boundaries.
        Returns HTML with <br> tags for line breaks.
        """
        if not text or len(text) <= max_length:
            return text
        words = text.split()
        lines = []
        current_line = ""
        for word in words:
            if len(current_line) + len(word) + (1 if current_line else 0) <= max_length:
                if current_line:
                    current_line += " "
                current_line += word
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return '<br>'.join(lines)
    
    # Color constants
    CUSTOM_COLOR = "#6C5CE7"
    GREY_COLOR = "#D9D9D9"
    DARK_GREY = "#666666"
    LIGHT_GREY = "#F9FAFB"
    BLACK = "#000000"
    WHITE = "#FFFFFF"
    RED = "#EF4444"
    
    @staticmethod
    def get_static_path(relative_path: str) -> Optional[str]:
        """Get static file path with fallback logic."""
        path = finders.find(relative_path)
        if path and os.path.exists(path):
            return path
        
        if hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT:
            path = os.path.join(settings.STATIC_ROOT, relative_path)
            if os.path.exists(path):
                return path
        
        logger.warning(f"Static file not found: {relative_path}")
        return None
    
    @staticmethod
    def format_currency(amount) -> str:
        """Format currency with rupee symbol."""
        try:
            if amount is None:
                return "₹0.00"
            if isinstance(amount, str):
                amount = Decimal(amount)
            elif not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            return format_inr(amount, decimals=2)
        except (ValueError, TypeError, InvalidOperation):
            return "₹0.00"

    @staticmethod
    def build_tax_rows_html(tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax, format_currency) -> str:
        """Build the exclusive-tax breakdown row(s) for the invoice totals section.

        Shared by BookingPDFService/TailorOrderPDFService/SalesPDFService so the
        GST->CGST/SGST split lives in one place instead of being triplicated.

        - No row at all when tax isn't applied or is inclusive (inclusive tax is
          already embedded in the product cost - see inclusive_tax_note_html at
          each call site).
        - When the (free-text, shop-configurable) tax_name is "GST" in any case
          (GST/gst/Gst), the tax is split evenly into CGST + SGST rows - e.g.
          18% / ₹360 becomes CGST 9% / ₹180 and SGST 9% / ₹180.
        - Any other tax_name (e.g. VAT) keeps the existing single combined row.
        """
        if not is_tax_applied or is_inclusive_tax:
            return ''

        tax_name = (tax_name or '').strip()
        tax_rate = Decimal(str(tax_rate or 0))
        tax_amount = Decimal(str(tax_amount or 0))

        if tax_name.upper() == 'GST':
            half_rate = tax_rate / 2
            # Round CGST first, then derive SGST as the remainder so the two
            # always sum exactly to tax_amount - halving both independently
            # and rounding each to 2dp can drift by a paisa on odd-cent
            # amounts (e.g. 99.99 -> 49.995 + 49.995, each rounds to 50.00).
            cgst_amount = (tax_amount / 2).quantize(Decimal("0.01"))
            sgst_amount = tax_amount - cgst_amount
            return f'''
            <div class="total-row discount-row">
                <span class="total-label">CGST ({float(half_rate):g}%)</span>
                <span class="total-value">{format_currency(cgst_amount)}</span>
            </div>
            <div class="total-row discount-row">
                <span class="total-label">SGST ({float(half_rate):g}%)</span>
                <span class="total-value">{format_currency(sgst_amount)}</span>
            </div>
            '''

        return f'''
        <div class="total-row discount-row">
            <span class="total-label">{tax_name.upper()} ({float(tax_rate):g}%)</span>
            <span class="total-value">{format_currency(tax_amount)}</span>
        </div>
        '''
    
    @staticmethod
    def safe_get(obj, attr, default=''):
        """Safely get attribute with fallback."""
        try:
            value = getattr(obj, attr, default)
            return value if value is not None else default
        except:
            return default

    @staticmethod
    def get_staff_name(record) -> str:
        """
        Name of the staff assigned to a booking / sale / tailor order, or ''
        when none. Callers load the record with select_related("staff"), so
        this reads the cached relation; when staff_id is null the relation is
        never touched, so it can't trigger a query either way.
        """
        if not getattr(record, "staff_id", None):
            return ""
        staff = BasePDFService.safe_get(record, "staff", None)
        return str(BasePDFService.safe_get(staff, "name", "")).strip()

    @staticmethod
    def build_staff_row_html(staff_name: str) -> str:
        """
        'STAFF:' row for the invoice-details block (shown after INVOICE ID and
        DATE). Returns '' when no staff is assigned so the row is omitted.
        """
        if not staff_name:
            return ""
        return (
            '<div class="invoice-row">'
            '<span class="invoice-label">STAFF:</span>'
            f'<span class="invoice-value">{html.escape(staff_name)}</span>'
            '</div>'
        )

    # ---- Per-person measurements (new booking measurement format) ----------
    # Legacy:  [{"name": "Chest", "value": "69"}, ...]  (one row per measurement)
    # New:     [{"name": "Person 1", "value": "{Chest: 23, Sleeve Length: 32}"}, ...]
    #          or {"name": "Afnan P", "value": {"chest": "23", "sleeve_length": "23"}}
    #          (one entry per person; value holds that person's measurements)
    _PERSON_MEASUREMENT_PAIR_RE = re.compile(r"\s*([^,:{}]+?)\s*:\s*([^,{}]*?)\s*(?:,|$)")
    MISSING_MEASUREMENT = "—"  # em dash

    @staticmethod
    def _measurement_label(key) -> str:
        """Display label: snake_case keys -> 'Title Case'; others kept as entered."""
        label = str(key).strip()
        if "_" in label or label.islower():
            label = label.replace("_", " ").strip().title()
        return label

    @staticmethod
    def _parse_person_measurements(value) -> list:
        """One person's measurements as [(label, value), ...]; [] if unreadable."""
        pairs = []
        if isinstance(value, dict):
            pairs = list(value.items())
        elif isinstance(value, str):
            text = value.strip()
            if text.startswith("{") and text.endswith("}"):
                pairs = BasePDFService._PERSON_MEASUREMENT_PAIR_RE.findall(text[1:-1])
        return [
            (BasePDFService._measurement_label(k), "" if v is None else str(v).strip())
            for k, v in pairs
            if k is not None and str(k).strip()
        ]

    @staticmethod
    def _is_person_measurements(measurements) -> bool:
        """True for the new per-person format (value is a dict or a '{key: value}' string)."""
        if not isinstance(measurements, list):
            return False
        for entry in measurements:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            if isinstance(value, dict):
                return True
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("{") and text.endswith("}") and ":" in text:
                    return True
        return False

    @staticmethod
    def _build_person_measurement_table(people) -> tuple:
        """
        (columns, rows) for the per-person table. Columns are the union of all
        people's measurement names in first-seen order, matched case/space/
        underscore-insensitively; rows are [s_no, name, value per column], with
        an em dash where a person has no value for that column.
        """
        columns, column_index, parsed = [], {}, []
        for entry in people:
            if not isinstance(entry, dict):
                continue
            values = {}
            # ordered_value: the order the user entered (jsonb re-sorts dict keys).
            for label, val in BasePDFService._parse_person_measurements(ordered_value(entry)):
                key = re.sub(r"[\s_]+", " ", label).strip().lower()
                if key not in column_index:
                    column_index[key] = len(columns)
                    columns.append(label)
                if val:
                    values[key] = val
            parsed.append((entry.get("name"), values))

        rows = []
        for s_no, (name, values) in enumerate(parsed, start=1):
            display_name = str(name).strip() if name is not None and str(name).strip() else f"Person {s_no}"
            rows.append(
                [s_no, display_name]
                + [values.get(key, BasePDFService.MISSING_MEASUREMENT) for key in column_index]
            )
        return columns, rows

    PERSONS_PER_MEASUREMENT_TABLE = 4
    # Compact per-person tables (like the legacy key-value cards): each table
    # is only as wide as its columns, never stretched to the full page width.
    PERSON_TABLE_LABEL_COL_PX = 150   # 'Measurements' column
    PERSON_TABLE_PERSON_COL_PX = 120  # each person column

    @staticmethod
    def _person_measurement_tables(columns, rows, size=PERSONS_PER_MEASUREMENT_TABLE) -> list:
        """
        Split one item's per-person table into transposed tables of up to `size`
        persons: [(person_names, [[measurement, value per person], ...]), ...].
        `columns`/`rows` come from _build_person_measurement_table (row = [s_no, name, *values]).
        """
        tables = []
        for start in range(0, len(rows), size):
            group = rows[start:start + size]
            names = [row[1] for row in group]
            body = [[label] + [row[2 + i] for row in group] for i, label in enumerate(columns)]
            tables.append((names, body))
        return tables

    # Header layout for the per-person table. All measurement columns share one
    # width (table-layout: fixed), so horizontal vs rotated is decided per table.
    PERSON_TABLE_WIDTH_PX = 714            # measured A4 content width of the table
    PERSON_FIXED_COLS_PX = 42 + 110        # S. No. + Name columns (see CSS)
    PERSON_HEADER_PADDING_PX = 12          # th padding left + right
    PERSON_HEADER_CHAR_WIDTH_PX = 8.5      # ~9.5pt bold, generous average glyph width
    PERSON_HEADER_MIN_CHARS = 5            # rotate when 5 letters can't fit on one line

    @staticmethod
    def _person_header_cells(columns) -> str:
        """
        <th> cells for the measurement columns of the per-person table. When a
        column is too narrow for PERSON_HEADER_MIN_CHARS letters, every header
        is rotated 90deg counterclockwise as a single line (reads bottom-to-top,
        glyphs rotated) instead of wrapping mid-word; otherwise unchanged.
        """
        S = BasePDFService
        if not columns:
            return ''
        col_width = (S.PERSON_TABLE_WIDTH_PX - S.PERSON_FIXED_COLS_PX) / len(columns)
        if col_width - S.PERSON_HEADER_PADDING_PX >= S.PERSON_HEADER_MIN_CHARS * S.PERSON_HEADER_CHAR_WIDTH_PX:
            return ''.join(f'<th>{html.escape(str(col))}</th>' for col in columns)

        # A transform doesn't change the layout box, so reserve the rotated
        # text's length as the label height (longest label, shared by the row).
        label_height = int(max(len(str(col)) for col in columns) * S.PERSON_HEADER_CHAR_WIDTH_PX) + 8
        return ''.join(
            f'<th class="vertical-header"><div class="vertical-label" style="height:{label_height}px;">'
            f'<span>{html.escape(str(col))}</span></div></th>'
            for col in columns
        )

    @staticmethod
    def build_invoice_filename(customer_name: str, invoice_id) -> str:
        """Build a standardized invoice PDF filename: <Customer_Name>_Invoice_<Invoice_ID>.pdf

        The name is reduced to a safe ASCII/filename-safe token: non-ASCII
        characters (e.g. Malayalam names, which this app fully supports
        elsewhere) and characters illegal in filenames or HTTP header values
        (quotes, backslash/slash, control chars like \\r\\n) are stripped so
        the result is always safe to use both as a filename and inside a
        Content-Disposition header, without ever raising UnicodeEncodeError
        or BadHeaderError.
        """
        name = (customer_name or "").strip()
        # Drop anything outside printable ASCII (also removes \r, \n, \t, etc.)
        name = name.encode("ascii", "ignore").decode("ascii")
        # Remove characters unsafe in filenames / Content-Disposition header syntax
        name = re.sub(r'[\\/:"*?<>|]', "", name)
        name = re.sub(r"\s+", "_", name).strip("_")
        name = name or "Customer"
        return f"{name}_Invoice_{invoice_id}.pdf"

    # ---------------------------------------------------------------------------
    # Terms & Conditions placement — MEASURED, not estimated.
    #
    # Every earlier version of this logic (including the previous fix for this
    # exact bug) guessed page geometry from hand-picked pixel constants
    # (row heights, char-per-line wrap counts, etc.). Any drift between that
    # guess and WeasyPrint's real layout put the decision on the wrong side of
    # "does it fit," which is why the bug kept resurfacing in a new shape after
    # every constant tweak.
    #
    # Instead, _resolve_terms_placement() below renders the real main-content
    # HTML once (via WeasyPrint's .render(), which returns the fully laid-out
    # page/box tree with exact pixel positions - see _measure_main_content_end)
    # to find out exactly where content ends and how much room is left on that
    # page, and renders the Terms & Conditions block once on an artificially
    # tall single page (see _measure_block_height) to get its exact height.
    # The placement decision is then arithmetic on real numbers, not a guess.
    # ---------------------------------------------------------------------------
    # The visual gap between the terms block and the page edge is provided by
    # .footer-content's own `padding-bottom: 20px` — which is measured as
    # part of the terms block's real height (see _measure_block_height, which
    # measures the .footer-content wrapper, not just .terms). _BOTTOM_GAP_PX
    # here is only a small rounding-error safety margin on top of that real
    # measurement, not a second copy of the visual gap.
    _BOTTOM_GAP_PX   = 2     # rounding-safety margin (px) on top of the measured terms height
    _BOTTOM_RESERVE_PX = 10  # .content's bottom padding, reserved conservatively on every page

    _MEASURE_MARKER_ID = '__content_end_marker__'
    _MEASURE_START_ID  = '__block_start_marker__'
    _MEASURE_END_ID    = '__block_end_marker__'

    # .content's real usable width (793.7px A4 width minus its 40px left +
    # 40px right padding) - the terms fragment measured in isolation by
    # _measure_block_height MUST be constrained to this same width, or text
    # wraps differently there than in the real render and the measured
    # height is wrong (narrower real width -> more wrapping -> taller).
    _CONTENT_USABLE_WIDTH_PX = 713.7

    @staticmethod
    def _measure_main_content_end(main_content_inner_html: str, css: str):
        """Render just the main-content HTML (header → invoice section →
        items table → totals-section) inside the same `.content > .main-content`
        wrapper used for the real render, with a zero-height marker div placed
        right after it (as a sibling of `.main-content`, matching exactly
        where `terms_spacer_html`/`.footer-content` sit in the real document),
        and return the exact page/position at which it ends, using
        WeasyPrint's real layout - not an estimate.

        Uses `Page.anchors` — WeasyPrint's public, documented per-page map of
        `element id -> (x, y)` (the same mechanism it uses to resolve internal
        PDF links/bookmarks) rather than any private layout-box internals, so
        this stays stable across WeasyPrint versions.

        Returns (page_index, page_height, marker_y, remaining_px) where
        remaining_px is the exact space left below the marker on that page
        (real page height minus the marker's real position, minus a small
        reserved bottom margin) — this already reflects whatever top padding
        that page type actually got, because the marker's position is real,
        not assumed. `page_height`/`marker_y` are returned too (not just the
        derived `remaining`) so the caller can log every raw input to the
        calculation, not just the result.
        """
        main_content_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body>
            <div class="content"><div class="main-content">{main_content_inner_html}</div>
            <div id="{BasePDFService._MEASURE_MARKER_ID}" style="height:0;"></div></div>
            </body></html>"""

        font_config = FontConfiguration()
        with _silence_pdf_generation_logs():
            document = HTML(string=main_content_html).render(
                stylesheets=[CSS(string=css, font_config=font_config)],
                font_config=font_config,
            )

        if not document.pages:
            logger.warning("Terms placement: measurement render produced 0 pages; forcing new page.")
            return 0, 0, 0, 0

        if not hasattr(document.pages[0], 'anchors'):
            # Page.anchors is documented, public WeasyPrint API (also used
            # internally for PDF bookmarks/links) - if it's genuinely missing,
            # that's a WeasyPrint version incompatibility, not a content issue.
            logger.error(
                "Terms placement: WeasyPrint Page object has no 'anchors' attribute - "
                "measurement technique incompatible with the installed WeasyPrint version. "
                "Forcing terms to a new page as a safe fallback."
            )
            last_page_index = max(0, len(document.pages) - 1)
            return last_page_index, document.pages[last_page_index].height, 0, 0

        for page_index, page in enumerate(document.pages):
            anchor = page.anchors.get(BasePDFService._MEASURE_MARKER_ID)
            if anchor is not None:
                # Index rather than unpack: WeasyPrint's anchor tuple shape has
                # differed across versions (a 2-tuple (x, y) in 52.5, more
                # elements in 67.0 - confirmed in production logs via
                # "ValueError: too many values to unpack"). Indexing [1] for
                # y is robust to extra trailing elements either way.
                marker_y = anchor[1]
                remaining = page.height - BasePDFService._BOTTOM_RESERVE_PX - marker_y
                logger.info(
                    f"Terms placement measurement: content ends on page {page_index} "
                    f"(0-indexed) of {len(document.pages)}, marker_y={marker_y:.1f}, "
                    f"page_height={page.height:.1f}, remaining={remaining:.1f}px"
                )
                return page_index, page.height, marker_y, remaining

        # Marker not found (shouldn't happen) — fail safe to "no room left"
        # so terms move to a fresh page rather than risk overlapping content.
        logger.warning(
            f"Terms placement: content-end marker '{BasePDFService._MEASURE_MARKER_ID}' not found "
            f"in any of {len(document.pages)} measured page(s); forcing new page. "
            f"anchors seen on last page: {list(document.pages[-1].anchors.keys())}"
        )
        last_page_index = max(0, len(document.pages) - 1)
        return last_page_index, document.pages[last_page_index].height, 0, 0

    @staticmethod
    def _measure_block_height(footer_content_inner_html: str, css: str) -> int:
        """Render the Terms & Conditions `.footer-content` block in isolation
        on a single artificially tall page (so it can never be forced to
        paginate/split), constrained to the same real usable width `.content`
        gives it in production (`_CONTENT_USABLE_WIDTH_PX` - narrower than
        the bare page width, which would under-count text wrapping and
        therefore under-count height), and return its real rendered height as
        the distance between two marker divs placed just outside it.
        """
        # A4 width (793.7px @ 96dpi) with an oversized height guarantees the
        # fragment always fits on one page, however long the terms list is.
        # Passed as the LAST stylesheet so its `size` declaration wins the
        # cascade over the A4 `size` declared in `css` (same specificity —
        # later stylesheet in the list takes precedence).
        tall_page_css = "@page { size: 793.7px 20000px; margin: 0; }"
        fragment_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body>
            <div style="width:{BasePDFService._CONTENT_USABLE_WIDTH_PX}px;">
            <div id="{BasePDFService._MEASURE_START_ID}" style="height:0;"></div>
            <div class="footer-content">{footer_content_inner_html}</div>
            <div id="{BasePDFService._MEASURE_END_ID}" style="height:0;"></div>
            </div></body></html>"""

        font_config = FontConfiguration()
        with _silence_pdf_generation_logs():
            document = HTML(string=fragment_html).render(
                stylesheets=[
                    CSS(string=css, font_config=font_config),
                    CSS(string=tall_page_css, font_config=font_config),
                ],
                font_config=font_config,
            )
        anchors = document.pages[0].anchors
        start = anchors.get(BasePDFService._MEASURE_START_ID)
        end = anchors.get(BasePDFService._MEASURE_END_ID)
        if start is None or end is None:
            logger.warning(
                f"Terms placement: block-height markers not found (start={start}, end={end}); "
                f"anchors seen: {list(anchors.keys())}. Treating terms height as 0."
            )
            return 0
        height = max(0, int(end[1] - start[1]))
        logger.info(f"Terms placement measurement: footer-content block height={height}px")
        return height

    @staticmethod
    def _resolve_terms_placement(main_content_inner_html: str, footer_content_inner_html: str, measure_css: str):
        """Single source of truth for Terms & Conditions page placement,
        shared by Booking/Tailor/Sales, driven by real WeasyPrint layout
        measurements instead of estimated pixel heights.

        `main_content_inner_html`: the HTML that goes inside `.main-content`
        (header/invoice/table/totals-section) - the same string used to build
        the real page.
        `footer_content_inner_html`: the HTML that goes inside
        `.footer-content` (the `.terms` block) - the same string used to
        build the real page.
        `measure_css`: the same font+base CSS used for the real render, so
        the measurement matches production exactly.

        Returns (footer_page_break_style, terms_spacer_html) - both to be
        used verbatim in the final HTML alongside `multi_page=True`.
        """
        try:
            page_index, page_height, marker_y, remaining = BasePDFService._measure_main_content_end(
                main_content_inner_html, measure_css
            )
            terms_h = BasePDFService._measure_block_height(footer_content_inner_html, measure_css)
        except Exception as e:
            # Measurement failed for any reason (unexpected WeasyPrint internals
            # change, malformed HTML, etc.) — fail safe: push terms to a new
            # page rather than risk it overlapping other content. Logged with
            # the exception type + input sizes (not full HTML, to keep logs
            # readable) so a recurrence is diagnosable from logs alone instead
            # of requiring another guess.
            logger.error(
                f"Terms placement measurement failed ({type(e).__name__}: {e}); forcing new page. "
                f"main_content_inner_html len={len(main_content_inner_html or '')}, "
                f"footer_content_inner_html len={len(footer_content_inner_html or '')}",
                exc_info=True,
            )
            return 'page-break-before: always; padding-top: 10px;', ''

        fits = remaining >= terms_h + BasePDFService._BOTTOM_GAP_PX

        # Explicit, labeled space-check block at the exact decision point —
        # logged via logger.error() (not .info) so it's visible regardless of
        # the app's configured log level, per request while diagnosing this.
        # This is a diagnostic log only; it does not change the decision.
        # logger.error(
        #     "PDF TERMS & CONDITIONS SPACE CHECK\n"
        #     f"Page (0-indexed): {page_index}\n"
        #     f"Page height: {page_height:.1f}\n"
        #     f"Content bottom position (marker Y): {marker_y:.1f}\n"
        #     f"Reserved bottom margin: {BasePDFService._BOTTOM_RESERVE_PX}\n"
        #     f"Remaining available space: {remaining:.1f}\n"
        #     f"Terms & Conditions required height: {terms_h}\n"
        #     f"Gap required: {BasePDFService._BOTTOM_GAP_PX}\n"
        #     f"Enough space: {'YES' if fits else 'NO'}\n"
        #     f"Decision: {'CURRENT PAGE (spacer)' if fits else 'NEXT PAGE (page-break)'}"
        # )

        if fits:
            spacer_px = max(0, int(remaining - terms_h - BasePDFService._BOTTOM_GAP_PX))
            terms_spacer_html = f'<div style="height: {spacer_px}px;"></div>' if spacer_px > 0 else ''
            return '', terms_spacer_html

        return 'page-break-before: always; padding-top: 10px;', ''

    @staticmethod
    def build_shop_details_html(shop):
        """Build shop details with requested layout for PDF templates."""
        shop_address = BasePDFService.safe_get(shop, 'address', '')
        shop_city = BasePDFService.safe_get(shop, 'city', '')
        shop_pincode = BasePDFService.safe_get(shop, 'pincode', '')
        shop_phone = BasePDFService.safe_get(shop, 'phone', '')
        shop_phone2 = BasePDFService.safe_get(shop, 'phone2', '')
        shop_email = BasePDFService.safe_get(shop, 'email', '')
        shop_gst = BasePDFService.safe_get(shop, 'gst_number', '')

        location_parts = [p for p in [shop_city] if p]
        location_text = ", ".join(location_parts)
        if shop_pincode:
            location_text = f"{location_text} - {shop_pincode}" if location_text else shop_pincode

        phone_parts = []
        if shop_phone:
            phone_parts.append(shop_phone)
        if shop_phone2:
            phone_parts.append(shop_phone2)
        phone_text = ", ".join(phone_parts)

        parts = []
        if shop_address:
            parts.append(shop_address)
        if location_text:
            parts.append(location_text)
        if phone_text:
            parts.append(phone_text)
        if shop_email:
            parts.append(shop_email)
        if shop_gst:
            parts.append(f"GST IN: {shop_gst}")

        return '<br/>'.join(parts)
    
    @staticmethod
    def download_images_parallel(urls: list, timeout: Optional[int] = None, max_workers: int = 5) -> Dict[str, Optional[str]]:
        """
        Download multiple images in parallel for better performance.
        
        Args:
            urls: List of image URLs to download
            timeout: Request timeout in seconds (default from config)
            max_workers: Maximum number of concurrent downloads (default: 5)
            
        Returns:
            Dictionary mapping URLs to base64 data URLs (or None on failure)
        """
        if not urls:
            return {}
        
        results = {}
        
        # Download images concurrently
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all download tasks
            future_to_url = {
                executor.submit(BasePDFService.download_image, url, timeout): url 
                for url in urls if url
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    results[url] = result
                except Exception as e:
                    logger.error(f"Failed to download image {url} in parallel: {e}")
                    results[url] = None
        
        return results
    
    @staticmethod
    def download_image(url: str, timeout: Optional[int] = None) -> Optional[str]:
        """
        Download image and return as base64 data URL with security measures.
        
        Security Features:
        - URL validation and SSRF prevention
        - Configurable timeout (default: 10s for S3 cross-region + network latency)
        - Circuit breaker pattern to prevent repeated failures
        - File size validation (max 10MB)
        - Content-type validation
        - SSL verification enabled
        - Resource cleanup on errors
        
        Args:
            url: Image URL to download
            timeout: Request timeout in seconds (default from config)
            
        Returns:
            Base64 data URL or None on failure
        """
        if not url:
            return None
        
        # Validate URL scheme to prevent file:// and other protocols
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ['http', 'https']:
                logger.warning(
                    f"[Security] Invalid URL scheme '{parsed.scheme}' blocked. "
                    f"Only http/https allowed."
                )
                return None
            
            # Prevent SSRF attacks to internal networks
            hostname = parsed.hostname
            if hostname:
                hostname_lower = hostname.lower()
                # Block localhost variants
                if hostname_lower in ['localhost', '127.0.0.1', '0.0.0.0', '::1']:
                    logger.warning(
                        f"[Security] Attempted localhost access blocked: {url}"
                    )
                    return None
                
                # Block private IP ranges (RFC 1918)
                if (hostname_lower.startswith('192.168.') or 
                    hostname_lower.startswith('10.') or
                    hostname_lower.startswith('172.16.') or
                    hostname_lower.startswith('172.17.') or
                    hostname_lower.startswith('172.18.') or
                    hostname_lower.startswith('172.19.') or
                    hostname_lower.startswith('172.20.') or
                    hostname_lower.startswith('172.21.') or
                    hostname_lower.startswith('172.22.') or
                    hostname_lower.startswith('172.23.') or
                    hostname_lower.startswith('172.24.') or
                    hostname_lower.startswith('172.25.') or
                    hostname_lower.startswith('172.26.') or
                    hostname_lower.startswith('172.27.') or
                    hostname_lower.startswith('172.28.') or
                    hostname_lower.startswith('172.29.') or
                    hostname_lower.startswith('172.30.') or
                    hostname_lower.startswith('172.31.')):
                    logger.warning(
                        f"[Security] Attempted private network access blocked: {url}"
                    )
                    return None
                
                # Block link-local addresses
                if hostname_lower.startswith('169.254.'):
                    logger.warning(
                        f"[Security] Attempted link-local access blocked: {url}"
                    )
                    return None
        except ValueError as e:
            logger.warning(f"[Security] Invalid URL format: {url} - {e}")
            return None
        
        # Use configured timeout if not specified
        if timeout is None:
            timeout = ImageDownloadConfig.TIMEOUT
        
        # Use circuit breaker to prevent repeated failures
        return _circuit_breaker.call(url, BasePDFService._download_image_impl, url, timeout)
    
    @staticmethod
    def _download_image_impl(url: str, timeout: int) -> Optional[str]:
        """Internal implementation of image download."""
        try:
            # Add proper headers for S3 and other services
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://bookiebuddy.in/'
            }
            
            # Handle potential redirects and SSL issues
            response = requests.get(
                url, 
                timeout=timeout,  # Configurable timeout with justification
                headers=headers, 
                allow_redirects=True,
                verify=True,  # SSL verification enabled
                stream=True  # Stream for better memory management
            )
            response.raise_for_status()
            
            # Check content length before downloading
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > ImageDownloadConfig.MAX_FILE_SIZE:
                logger.warning(
                    f"Image too large (Content-Length: {content_length} bytes) from {url}. "
                    f"Max allowed: {ImageDownloadConfig.MAX_FILE_SIZE} bytes"
                )
                return None
            
            # Get content type
            content_type = response.headers.get('content-type', '').lower()
            
            # Validate content type
            if not content_type.startswith('image/'):
                logger.warning(f"URL does not return an image: {url}, Content-Type: {content_type}")
                return None
            
            # Download content with size limit
            content = b''
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > ImageDownloadConfig.MAX_FILE_SIZE:
                    logger.warning(
                        f"Image exceeds size limit during download: {len(content)} bytes from {url}. "
                        f"Max allowed: {ImageDownloadConfig.MAX_FILE_SIZE} bytes"
                    )
                    return None
            
            # Validate content
            if len(content) == 0:
                logger.warning(f"Empty image content from {url}")
                return None
            
            # Encode to base64
            img_data = base64.b64encode(content).decode('utf-8')
            
            # Normalize MIME type
            mime_type = content_type.split(';')[0].strip()
            if 'jpeg' in mime_type or 'jpg' in mime_type:
                mime_type = 'image/jpeg'
            elif 'png' in mime_type:
                mime_type = 'image/png'
            elif 'webp' in mime_type:
                mime_type = 'image/webp'
            elif 'gif' in mime_type:
                mime_type = 'image/gif'
            elif 'svg' in mime_type:
                mime_type = 'image/svg+xml'
            else:
                mime_type = 'image/jpeg'  # default
            
            data_url = f"data:{mime_type};base64,{img_data}"
            
            logger.info(
                f"Successfully downloaded image from {url} "
                f"({len(content)} bytes, {mime_type}, {timeout}s timeout)"
            )
            return data_url
            
        except requests.exceptions.Timeout:
            logger.warning(
                f"Timeout ({timeout}s) downloading image from {url}. "
                f"Consider increasing IMAGE_DOWNLOAD_TIMEOUT setting if S3 cross-region delays are common."
            )
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error downloading image from {url}: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP error {e.response.status_code} downloading image from {url}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading image from {url}: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def format_datetime(dt):
        """Format datetime in local timezone."""
        if not dt:
            return ""
        try:
            local_dt = localtime(dt)
            return local_dt.strftime("%d/%m/%Y %I:%M %p")
        except:
            return ""
    
    @staticmethod
    def format_date(dt):
        """Format date safely."""
        if not dt:
            return ""
        try:
            if hasattr(dt, "utcoffset"):
                local_dt = localtime(dt)
                return local_dt.strftime("%d/%m/%Y")
            return dt.strftime("%d/%m/%Y")
        except:
            return ""

    @staticmethod
    def format_time_ampm(t):
        """Convert 24hr time → AM/PM."""
        if not t:
            return None
        return datetime.combine(datetime.today(), t).strftime("%I:%M %p")

    @staticmethod
    def extract_display_datetime(date_dt, time_field=None, ignore_time=None):
        """
        Extract date + correct time with fallback rules.

        Rules:
        1. Use date from date_dt
        2. Prefer explicit time_field
        3. Else fallback to date_dt time (+5:30 correction)
        4. Ignore placeholder times
        """
        if not date_dt:
            return None

        # Normalize timezone first.
        try:
            local_dt = localtime(date_dt)
        except Exception:
            local_dt = date_dt

        # --- Extract DATE ---
        try:
            display_date = local_dt.strftime("%d/%m/%Y")
        except Exception:
            # Fallback for pure date objects
            display_date = str(local_dt)

        # --- Determine TIME ---
        chosen_time = None

        # Priority 1 → Explicit time field
        if time_field:
            if ignore_time is None or time_field != ignore_time:
                chosen_time = time_field

        # Priority 2 → Fallback from datetime
        elif hasattr(local_dt, "time"):
            fallback_time = local_dt.time()

            if ignore_time is None or fallback_time != ignore_time:
                chosen_time = fallback_time

        # --- Format output ---
        if chosen_time:
            return f"{display_date} {BookingPDFService.format_time_ampm(chosen_time)}"

        return display_date        
    
    @staticmethod
    def parse_transaction_details(details) -> Dict[str, Any]:
        """Safely parse transaction details JSON."""
        if not details:
            return {}
        
        try:
            if isinstance(details, dict):
                return details
            elif isinstance(details, str):
                return json_module.loads(details)
            else:
                return {}
        except json_module.JSONDecodeError:
            return {}
        except Exception:
            return {}
    
    @staticmethod
    def contains_malayalam(text: str) -> bool:
        """Check if text contains Malayalam characters."""
        if not text:
            return False
        return any('\u0D00' <= char <= '\u0D7F' for char in str(text))
    
    @staticmethod
    def get_font_family(text: str, is_bold: bool = False) -> str:
        """Get appropriate font family based on text content."""
        if BasePDFService.contains_malayalam(str(text)):
            return "'Noto Sans Malayalam', sans-serif"
        return "'Inter', sans-serif"
    
    @staticmethod
    def get_font_weight(text: str, is_bold: bool = False) -> str:
        """Get appropriate font weight based on text content."""
        if is_bold:
            return "700"
        return "400"
    
    @staticmethod
    def get_font_css() -> str:
        """Generate CSS for Malayalam font support.
        
        Fonts are registered via CSS @font-face rules for WeasyPrint.
        """
        from pathlib import Path
        
        malayalam_regular = BasePDFService.get_static_path('fonts/NotoSansMalayalam-Regular.ttf')
        malayalam_bold = BasePDFService.get_static_path('fonts/NotoSansMalayalam-Bold.ttf')
        
        css = ""
        if malayalam_regular:
            try:
                # Convert to proper file URL for WeasyPrint
                regular_path = Path(malayalam_regular).resolve()
                regular_url = regular_path.as_uri()
                
                css += f"""
        @font-face {{
            font-family: 'Noto Sans Malayalam';
            src: url('{regular_url}') format('truetype');
            font-weight: 400;
            font-style: normal;
        }}
        """
                logger.info(f"Added Malayalam regular font: {regular_url}")
            except Exception as e:
                logger.warning(f"Failed to add Malayalam regular font: {e}")
        
        if malayalam_bold:
            try:
                # Convert to proper file URL for WeasyPrint
                bold_path = Path(malayalam_bold).resolve()
                bold_url = bold_path.as_uri()

                css += f"""
        @font-face {{
            font-family: 'Noto Sans Malayalam';
            src: url('{bold_url}') format('truetype');
            font-weight: 700;
            font-style: normal;
        }}
        """
                logger.info(f"Added Malayalam bold font: {bold_url}")
            except Exception as e:
                logger.warning(f"Failed to add Malayalam bold font: {e}")

        # Register Inter font for crisp digit/number rendering across all PDF viewers
        inter_regular = BasePDFService.get_static_path('fonts/Inter-Regular.ttf')
        inter_semibold = BasePDFService.get_static_path('fonts/Inter-SemiBold.ttf')
        inter_extrabold = BasePDFService.get_static_path('fonts/Inter-ExtraBold.ttf')

        for inter_path, weight in [(inter_regular, 400), (inter_semibold, 600), (inter_extrabold, 800)]:
            if inter_path:
                try:
                    inter_url = Path(inter_path).resolve().as_uri()
                    css += f"""
        @font-face {{
            font-family: 'Inter';
            src: url('{inter_url}') format('truetype');
            font-weight: {weight};
            font-style: normal;
        }}
        """
                    logger.info(f"Added Inter font weight {weight}: {inter_url}")
                except Exception as e:
                    logger.warning(f"Failed to add Inter font weight {weight}: {e}")

        return css
    
    @staticmethod
    def get_base_css(first_page: bool = True, multi_page: bool = False) -> str:
        """Get base CSS for PDF styling matching old ReportLab design.

        Args:
            first_page: Whether to include first-page specific styles.
            multi_page: When True, switches .content from flex to block layout
                        so WeasyPrint can paginate items across 3+ pages correctly.
        """
        content_display = "display: block;" if multi_page else "display: flex; flex-direction: column;"
        # In flex mode, override flex:1 so .main-content uses its natural height,
        # leaving space for margin-top:auto on .footer-content to bottom-anchor T&C.
        main_content_flex = "" if multi_page else "flex: 1;"
        return f"""
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        @page {{
            size: A4;
            margin: 0;
        }}
        
        body {{
            font-family: 'Noto Sans Malayalam', 'Inter', sans-serif;
            font-size: 10pt;
            line-height: 1.4;
            color: #374151;
            background: transparent;
            position: relative;
            margin: 0;
            padding: 0;
            min-height: 297mm;
        }}
        
        .content {{
            position: relative;
            z-index: 1;
            {content_display}
            min-height: 297mm;
            padding: 60px 40px 10px 40px;
        }}

        @page {{
            padding-top: 60px;
        }}

        @page :first {{
            padding-top: 0;
        }}

        .main-content {{
            flex: 1;
            {main_content_flex}
        }}

        .footer-content {{
            display: flex;
            flex-direction: column;
            justify-content: flex-end;

            /* No margin-top: auto — the spacer div handles vertical push to bottom. */
            padding-bottom: 20px;
            /* Both break properties kept intentionally:
               break-inside (CSS3) for modern WeasyPrint;
               page-break-inside (CSS2 legacy) for older WeasyPrint builds. */
            page-break-inside: avoid;
            break-inside: avoid;
        }}
        
        .header {{
            display: flex;
            align-items: center;
            margin-bottom: 25px;
            margin-top: 40px; /* Increased top gap for more spacing above shop details */
            padding-top: 0;
        }}
        
        .logo {{
            width: 90px;
            height: 90px;
            margin-right: 15px;
            object-fit: contain; /* Prevent black background for non-PNGs */
            background: #fff;    /* Always use white background */
            border-radius: 8px;
            border: 1px solid #e5e7eb;
            flex-shrink: 0;
            padding: 4px;
            box-sizing: border-box;
        }}
        
        .shop-info {{
            flex: 1;
        }}
        
        .shop-name {{
            font-size: 16pt;
            font-weight: 700;
            color: #6C5CE7;
            margin-bottom: 2px;
            letter-spacing: 0.5px;
        }}
        
        .shop-details {{
            font-size: 10pt;
            color: #374151;
            line-height: 1.6;
        }}
        
        .invoice-section {{
            display: flex;
            justify-content: space-between;
            align-items: flex-end; /* Align bottoms of children */
            margin-bottom: 15px;
            margin-top: 10px;
            gap: 20px;
        }}
        .bill-to {{
            display: flex;
            flex-direction: column;
            justify-content: flex-end; /* Push content to bottom */
        }}
        
        .invoice-info {{
            /* Plain block flow (not a flex column): every invoice-row keeps its
               natural height, so INVOICE ID / DATE / STAFF rows are spaced
               identically. As a flex column WeasyPrint stretched rows after the
               first to fill the BILL TO height, leaving gaps between rows. */
            flex: 0 0 auto;
            min-width: 240px;
        }}
        
        .bill-to {{
            flex: 1;
            text-align: right;
        }}
        
        .invoice-row {{
            margin-bottom: 3px;
            display: flex;
            gap: 10px;
        }}
        
        .invoice-label {{
            font-weight: 700;
            color: #000000;
            min-width: 80px;
            font-size: 10pt;
            flex-shrink: 0;
        }}
        
        .invoice-value {{
            color: #374151;
            flex: 1;
            font-size: 10pt;
        }}
        
        .section-title {{
            font-size: 13pt;
            font-weight: 700;
            color: #6C5CE7;
            margin-bottom: 4px;
            letter-spacing: 0.5px;
        }}
        
        .bill-to-content {{
            font-size: 10pt;
            line-height: 1.2;
        }}
        
        .bill-to-name {{
            font-weight: 700;
            font-size: 12pt;
            color: #000000;
            margin: 0 0 1px 0;
            white-space: nowrap;
            overflow: hidden;
        }}
        
        .bill-to-phone {{
            color: #374151;
            margin-bottom: 1px;
        }}
        
        .bill-to-address {{
            color: #374151;
            line-height: 1.3;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 25px 0 20px 0;
            font-size: 10pt;
            border-left: 0.5px solid #D9D9D9;
            border-right: 0.5px solid #D9D9D9;
            border-top: 0.5px solid #D9D9D9;
            border-bottom: 0.5px solid #D9D9D9;
        }}
        
        th {{
            background-color: #D9D9D9;
            padding: 14px 10px;
            text-align: left;
            font-weight: 700;
            font-size: 10pt;
            border: none;
            border-bottom: 0.5px solid #D9D9D9;
            color: #000000;
        }}
        
        th.text-center {{
            text-align: center;
        }}
        
        th.text-right {{
            text-align: right;
        }}
        
        td {{
            padding: 14px 8px;
            border: none;
            vertical-align: middle;
            color: #374151;
        }}

        tr {{
            page-break-inside: avoid;
            break-inside: avoid;
        }}

        tr:nth-child(even) td {{
            background-color: #F9FAFB;
        }}
        
        td.text-center {{
            text-align: center;
        }}
        
        td.text-right {{
            text-align: right;
        }}
        
        .item-row {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .item-image {{
            width: 50px;
            height: 50px;
            border-radius: 8px;
            object-fit: cover;
            flex-shrink: 0;
            border: 1px solid #e5e7eb;
        }}
        
        .item-details {{
            flex: 1;
        }}
        
        .item-name {{
            font-weight: 700;
            font-size: 11pt;
            color: #000000;
            margin-bottom: 3px;
        }}
        
        .item-meta {{
            font-size: 9pt;
            color: #6B7280;
            margin: 2px 0;
            line-height: 1.4;
        }}
        
        .totals-section {{
            display: flex;
            justify-content: space-between;
            margin-top: 20px;
            page-break-inside: avoid;
            break-inside: avoid;
            margin-bottom: 20px;
            gap: 40px;
            page-break-inside: avoid;
            break-inside: avoid;
        }}
        
        .order-info {{
            flex: 1;
        }}
        
        .order-info-title {{
            font-size: 12pt;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        
        .order-info-title .highlight {{
            color: #6C5CE7;
        }}
        
        .order-info-item {{
            font-size: 10pt;
            color: #374151;
            margin-bottom: 4px;
            line-height: 1.6;
        }}
        
        .totals {{
            flex: 0 0 240px;
            text-align: right;
            page-break-inside: avoid;
            break-inside: avoid;
        }}
        
        .total-row {{
            display: flex;
            justify-content: space-between;
            padding: 2px 0;
            font-size: 10pt;
            gap: 15px;
        }}
        
        .total-label {{
            font-weight: 600;
            color: #000000;
            text-align: left;
            flex: 1;
        }}
        
        .total-value {{
            font-weight: 700;
            color: #000000;
            text-align: right;
            min-width: 100px;
            font-family: 'Inter', sans-serif;
        }}
        
        .subtotal-row {{
            padding: 6px 0;
            font-size: 10pt;
        }}
        
        .additional-row {{
            padding: 2px 0;
            font-size: 12pt;
        }}
        
        .discount-row {{
            padding: 2px 0;
            font-size: 10pt;
        }}

        .section-gap {{
            margin-top: 10px;
        }}

        .status-header {{
            margin-top: 10px;
            padding-top: 6px;
            border-top: 1px solid #D9D9D9;
            padding-bottom: 4px;
        }}
        
        .grand-total-row {{
            border-top: 1px solid #D9D9D9;
            margin-top: 8px;
            padding-top: 8px;
            font-weight: 700;
            font-size: 18pt;
            font-family: 'Inter', sans-serif;
            display: flex;
            flex-wrap: nowrap;
            white-space: nowrap;
            gap: 40px;
            width: 100%;
            justify-content: space-between;
        }}
        
        .grand-total-row .total-label,
        .grand-total-row .total-value {{
            color: #6C5CE7;
            font-size: 18pt;
            white-space: nowrap;
        }}
        
        .grand-total-row .total-label {{
            flex: 0 0 auto;
            margin-right: auto;
            padding-right: 20px;
        }}
        
        .grand-total-row .total-value {{
            flex: 0 0 auto;
            text-align: right;
            padding-left: 20px;
        }}
        
        .paid-amount-row {{
            font-size: 14pt;
            font-family: 'Inter', sans-serif;
            display: flex;
            flex-wrap: nowrap;
            white-space: nowrap;
            gap: 40px;
            width: 100%;
            justify-content: space-between;
        }}
        
        .paid-amount-row .total-label,
        .paid-amount-row .total-value {{
            font-size: 14pt;
            white-space: nowrap;
        }}
        
        .paid-amount-row .total-label {{
            flex: 0 0 auto;
            margin-right: auto;
            padding-right: 20px;
        }}
        
        .paid-amount-row .total-value {{
            flex: 0 0 auto;
            text-align: right;
            padding-left: 20px;
        }}

        .cancelled-row {{
            font-size: 11pt;
            font-family: 'Inter', sans-serif;
            display: flex;
            flex-wrap: nowrap;
            white-space: nowrap;
            gap: 40px;
            width: 100%;
            justify-content: space-between;
            margin-bottom: 4px;
        }}

        .cancelled-row .total-label,
        .cancelled-row .total-value {{
            color: #EF4444;
            font-size: 11pt;
            white-space: nowrap;
        }}

        .cancelled-row .total-label {{
            flex: 0 0 auto;
            margin-right: auto;
            padding-right: 20px;
        }}

        .cancelled-row .total-value {{
            flex: 0 0 auto;
            text-align: right;
            padding-left: 20px;
        }}
        
        .balance-due-row {{
            font-size: 14pt;
            font-family: 'Inter', sans-serif;
            display: flex;
            flex-wrap: nowrap;
            white-space: nowrap;
            gap: 40px;
            width: 100%;
            justify-content: space-between;
            margin-bottom: 8px;
            padding-bottom: 4px;
        }}
        
        .balance-due-row .total-label,
        .balance-due-row .total-value {{
            color: #EF4444;
            font-size: 14pt;
            white-space: nowrap;
        }}
        
        .balance-due-row .total-label {{
            flex: 0 0 auto;
            margin-right: auto;
            padding-right: 20px;
        }}
        
        .balance-due-row .total-value {{
            flex: 0 0 auto;
            text-align: right;
            padding-left: 20px;
        }}
        
        .terms {{
            padding-top: 5px;
            border-top: 1px solid #e5e7eb;
            page-break-inside: avoid;
            break-inside: avoid;
        }}
        
        .terms-title {{
            font-weight: 700;
            font-size: 11pt;
            margin-bottom: 8px;
            color: #000000;
        }}
        
        .terms-title .highlight {{
            color: #6C5CE7;
        }}
        
        .terms-list {{
            font-size: 9pt;
            line-height: 1.5;
            color: #374151;
            margin-bottom: 5px;
        }}
        
        .terms-list div {{
            margin-bottom: 2px;
        }}
        
        .font-bold {{
            font-weight: 700;
        }}

        .amount-cell {{
            text-align: right;
            font-weight: 700;
            font-size: 11pt;
            color: #000000;
            font-family: 'Inter', sans-serif;
        }}
        
        .spacer {{
            margin-top: 20px;
        }}
        
        .empty-image {{
            width: 50px;
            height: 50px;
            background: #f0f0f0;
            border-radius: 8px;
            border: 1px solid #e5e7eb;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #999;
            font-size: 8px;
        }}
        
        .empty-logo {{
            width: 60px;
            height: 60px;
            background: #f0f0f0;
            border-radius: 8px;
            border: 1px solid #e5e7eb;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #999;
            font-size: 9px;
        }}
        
        /* Measurements section styles */
        .measurements-section {{
            margin-top: 24px;
        }}

        .measurements-title {{
            text-align: center;
            font-size: 20pt;
            font-weight: 700;
            color: #6C5CE7;
            margin-bottom: 12px;
            letter-spacing: 0.5px;
        }}

        /* Two-column measurements grid — display:table-cell, WeasyPrint-safe.
           float:left inside break-inside:avoid triggers a WeasyPrint broken_out_of_flow
           KeyError during multi-page pagination. display:table-cell is in-flow and
           never enters that dict, eliminating the crash. Visual output is identical. */
        .measurements-pair {{
            display: table;
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            margin-bottom: 16px;
            page-break-inside: avoid;
            break-inside: avoid;
        }}

        .measurements-col-left {{
            display: table-cell;
            width: 49%;
            padding-right: 8px;
            vertical-align: top;
        }}

        .measurements-col-right {{
            display: table-cell;
            width: 49%;
            padding-left: 8px;
            vertical-align: top;
        }}

        .variant-table-card {{
            page-break-inside: avoid;
            break-inside: avoid;
            margin-bottom: 16px;
            background: transparent;
            border: none;
        }}

        .variant-table-heading {{
            font-size: 13pt;
            font-weight: 700;
            color: #000000;
            margin-bottom: 8px;
            padding-bottom: 6px;
            border-bottom: 1px solid #D9D9D9;
            background: transparent;
        }}
        
        .measurements-table {{
            width: 100%;
            border-collapse: collapse;
            border-radius: 8px;
            overflow: hidden;
            font-size: 11pt;
            border: 0.5px solid #D9D9D9;
        }}
        
        .measurements-table thead {{
            background-color: #D9D9D9;
        }}
        
        .measurements-table th {{
            background-color: #D9D9D9;
            padding: 11px 9px;
            text-align: left;
            font-weight: 700;
            font-size: 14pt;
            border: 0.5px solid #000000;
            color: #000000;
        }}
        
        .measurements-table th.text-right {{
            text-align: right;
        }}

        /* Per-person measurements (new booking format): full width, one column
           per measurement, so text is smaller and wraps to fit ~12 columns. */
        .person-measurements-table {{
            width: 100%;
            table-layout: fixed;
            border-collapse: collapse;
            font-size: 10pt;
            border: 0.5px solid #D9D9D9;
        }}

        .person-measurements-table th {{
            background-color: #D9D9D9;
            padding: 8px 6px;
            text-align: left;
            font-weight: 700;
            font-size: 9.5pt;
            border: 0.5px solid #000000;
            color: #000000;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }}

        .person-measurements-table td {{
            padding: 8px 6px;
            border: 0.5px solid #000000;
            vertical-align: middle;
            color: #374151;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }}

        .person-measurements-table .sno {{
            width: 42px;
            text-align: center;
        }}

        .person-measurements-table .person-name {{
            width: 110px;
        }}

        /* Booking invoice: measurement names as rows, up to 4 person columns;
           table/column widths are set inline (compact, not full width). */
        .person-measurements-table th.text-right,
        .person-measurements-table td.text-right {{
            text-align: right;
        }}

        /* Narrow columns: header rotated 90deg counterclockwise as one line
           (reads bottom-to-top). WeasyPrint has no writing-mode, so the label
           box gets an explicit height and the text is rotated with transform. */
        .person-measurements-table th.vertical-header {{
            padding: 6px 2px;
            vertical-align: bottom;
        }}

        .person-measurements-table .vertical-label {{
            position: relative;
            width: 100%;
        }}

        .person-measurements-table .vertical-label span {{
            position: absolute;
            left: 50%;
            bottom: 0;
            margin-left: 0.6em;
            white-space: nowrap;
            transform-origin: left bottom;
            transform: rotate(-90deg);
        }}
        
        .measurements-table td {{
            padding: 11px 9px;
            border: 0.5px solid #000000;
            vertical-align: middle;
            color: #374151;
        }}
        
        .measurements-table td.text-right {{
            text-align: right;
        }}
        
        /* Row alternating bg applied inline in Python — nth-child unreliable in nested WeasyPrint tables */
        
        /* Ensure images display properly */
        img {{
            max-width: 100%;
            height: auto;
        }}

        /* Payment history section */
        .payment-history {{
            margin-top: 20px;
            page-break-inside: avoid;
            break-inside: avoid;
        }}

        .payment-history-title {{
            font-size: 11pt;
            font-weight: 700;
            color: #000000;
            margin-bottom: 6px;
        }}

        .payment-history-title .highlight {{
            color: #6C5CE7;
        }}

        .payment-history-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 8pt;
            border: 0.5px solid #D9D9D9;
            margin: 0;
        }}

        .payment-history-table th {{
            background-color: #D9D9D9;
            padding: 5px 6px;
            text-align: left;
            font-weight: 700;
            font-size: 8pt;
            border: none;
            color: #000000;
            white-space: nowrap;
        }}

        .payment-history-table td {{
            padding: 4px 6px;
            border: none;
            font-size: 8pt;
            color: #374151;
            vertical-align: middle;
            white-space: nowrap;
        }}

        .payment-history-table tr:nth-child(even) td {{
            background-color: #F9FAFB;
        }}
        """
    
    @staticmethod
    def _build_measurements_page_html(sections: list, client_info: str = "", staff_name: str = "",
                                      person_sections: list = None) -> str:
        """
        Build the two-column measurements HTML page.

        Args:
            sections: list of (heading: str, rows: list[{'name': str, 'value': str}])
            client_info: optional HTML (e.g. client name + phone) rendered below the
                         'PRODUCT MEASUREMENTS' title. Pass '' to omit.
            person_sections: optional list of (heading, columns, rows) from
                         _build_person_measurement_table() - new per-person format,
                         rendered as full-width 'Measurements | <person columns>'
                         tables (up to 4 persons each) after the legacy cards.
                         Booking invoice only.

        Returns:
            Full measurements-section HTML string, or '' when there is nothing to show.
        """
        if not sections and not person_sections:
            return ''

        section_htmls = []
        for heading, rows in sections:
            row_html = ''.join([
                f'<tr>'
                f'<td style="background-color:{"#F9FAFB" if idx % 2 == 1 else "#FFFFFF"};">{r["name"]}</td>'
                f'<td class="text-right" style="background-color:{"#F9FAFB" if idx % 2 == 1 else "#FFFFFF"};">{r["value"]}</td>'
                f'</tr>'
                for idx, r in enumerate(rows)
            ])
            section_htmls.append(f"""
            <div class="variant-table-card">
                <div class="variant-table-heading">{heading}</div>
                <table class="measurements-table">
                    <thead>
                        <tr>
                            <th>Measurement</th>
                            <th class="text-right">Size</th>
                        </tr>
                    </thead>
                    <tbody>
                        {row_html}
                    </tbody>
                </table>
            </div>
            """)

        # Build float-based pair divs (2 cards per row)
        pair_htmls = []
        for i in range(0, len(section_htmls), 2):
            left  = section_htmls[i]
            right = section_htmls[i + 1] if i + 1 < len(section_htmls) else ''
            pair_htmls.append(
                f'<div class="measurements-pair">'
                f'<div class="measurements-col-left">{left}</div>'
                f'<div class="measurements-col-right">{right}</div>'
                f'</div>'
            )

        # New per-person format: measurements as rows, one column per person,
        # up to PERSONS_PER_MEASUREMENT_TABLE persons per table; extra persons
        # continue in further tables below (heading on the first table only).
        for heading, columns, rows in person_sections or []:
            tables = BasePDFService._person_measurement_tables(columns, rows)
            for table_idx, (names, body) in enumerate(tables):
                header_html = (
                    '<th class="measurement-name">Measurements</th>'
                    + ''.join(f'<th class="text-right">{html.escape(str(name))}</th>' for name in names)
                )
                body_rows = []
                for row_idx, row in enumerate(body):
                    # Alternating bg applied inline, same as the legacy table.
                    bg = "#F9FAFB" if row_idx % 2 == 1 else "#FFFFFF"
                    row_cells = [f'<td style="background-color:{bg};">{html.escape(str(row[0]))}</td>']
                    row_cells += [
                        f'<td class="text-right" style="background-color:{bg};">{html.escape(str(value))}</td>'
                        for value in row[1:]
                    ]
                    body_rows.append('<tr>' + ''.join(row_cells) + '</tr>')
                body_html = ''.join(body_rows)
                heading_html = (
                    f'<div class="variant-table-heading">{html.escape(str(heading))}</div>'
                    if table_idx == 0 else ''
                )
                label_px = BasePDFService.PERSON_TABLE_LABEL_COL_PX
                person_px = BasePDFService.PERSON_TABLE_PERSON_COL_PX
                width_px = label_px + person_px * len(names)
                colgroup_html = (
                    f'<colgroup><col style="width:{label_px}px;">'
                    + f'<col style="width:{person_px}px;">' * len(names)
                    + '</colgroup>'
                )
                pair_htmls.append(f"""
                <div class="variant-table-card" style="width:{width_px}px;">
                    {heading_html}
                    <table class="person-measurements-table" style="width:{width_px}px;">
                        {colgroup_html}
                        <thead><tr>{header_html}</tr></thead>
                        <tbody>{body_html}</tbody>
                    </table>
                </div>
                """)

        # Title + first pair are wrapped together in a single break-inside: avoid
        # container so WeasyPrint always moves both to the next page as a unit
        # if they don't fit at the bottom of the current page.
        first_pair      = pair_htmls[0] if pair_htmls else ''
        remaining_pairs = ''.join(pair_htmls[1:])

        client_info_html = (
            f'<div style="width:100%;margin-bottom:12px;font-size:11pt;">{client_info}</div>'
            if client_info else ''
        )

        staff_name_html = (
            f'<div style="width:100%;margin-bottom:12px;font-size:11pt;">'
            f'<strong>Staff:</strong> {html.escape(staff_name)}'
            f'</div>'
            if staff_name else ''
        )

        return f"""
        <div class="measurements-section">
            <div style="break-inside: avoid; page-break-inside: avoid;">
                <div class="measurements-title">PRODUCT MEASUREMENTS</div>
                {client_info_html}
                {staff_name_html}
                {first_pair}
            </div>
            {remaining_pairs}
        </div>
        """

    @staticmethod
    def generate_pdf(html_content: str, first_page: bool = True, extra_css: str = "", multi_page: bool = False) -> bytes:
        """Generate PDF from HTML using WeasyPrint."""
        if not WEASYPRINT_AVAILABLE:
            raise RuntimeError("WeasyPrint is not installed")

        try:
            # Create FontConfiguration for proper font handling
            font_config = FontConfiguration()

            # Build CSS with font declarations and base styles
            full_css = BasePDFService.get_font_css() + "\n" + BasePDFService.get_base_css(first_page, multi_page=multi_page) + "\n" + extra_css
            
            # Generate PDF with font configuration
            with _silence_pdf_generation_logs():
                pdf_bytes = HTML(string=html_content).write_pdf(
                    stylesheets=[CSS(string=full_css, font_config=font_config)],
                    font_config=font_config
                )
            
            return pdf_bytes
        except Exception as e:
            logger.error(f"PDF generation failed: {e}", exc_info=True)
            raise


def _build_payment_history_html(rows: list) -> str:
    """Build unified payment + refund history table HTML for PDF invoices.

    Each row dict must contain: date, method, amount, is_refund (bool).
    Refund rows are rendered in red with a '-' prefix and '(REFUND)' label.
    Returns an empty string when rows is empty so the section is omitted.
    """
    if not rows:
        return ''
    rows_html = ''.join(
        (
            f'<tr>'
            f'<td style="color:#EF4444;">{r["date"]}</td>'
            f'<td style="color:#EF4444;">{r["method"]}</td>'
            f'<td style="text-align:right;font-family:\'Inter\',sans-serif;font-weight:700;color:#EF4444;">-{r["amount"]}</td>'
            f'</tr>'
        ) if r.get('is_refund') else (
            f'<tr>'
            f'<td>{r["date"]}</td>'
            f'<td>{r["method"]}</td>'
            f'<td style="text-align:right;font-family:\'Inter\',sans-serif;font-weight:700;">{r["amount"]}</td>'
            f'</tr>'
        )
        for r in rows
    )
    return f'''
    <div class="payment-history">
        <div class="payment-history-title">PAYMENT <span class="highlight">HISTORY</span></div>
        <table class="payment-history-table">
            <thead>
                <tr>
                    <th style="width:44%;">DATE</th>
                    <th style="width:20%;">METHOD</th>
                    <th style="width:36%;text-align:right;">AMOUNT</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    '''


# ============================================================================
# BOOKING PDF SERVICE
# ============================================================================
class BookingPDFService(BasePDFService):
    """PDF service for booking/transaction documents."""

    @staticmethod
    def _parse_bool_flag(value, default=True) -> bool:
        """Parse mixed-type boolean-like values consistently."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    @staticmethod
    def _resolve_security_amount_is_paid(transaction) -> bool:
        """Resolve security paid status using explicit flag first, then latest payment row."""
        explicit_flag = BookingPDFService.safe_get(transaction, 'security_amount_is_paid', None)
        if explicit_flag is not None:
            return BookingPDFService._parse_bool_flag(explicit_flag, default=True)

        try:
            security_payments_rel = getattr(transaction, 'security_deposit_payments', None)
            if security_payments_rel is None:
                return True

            if hasattr(security_payments_rel, 'all'):
                # Use newest payment row as the current source of truth.
                latest_security_payment = security_payments_rel.all().order_by('-created_at').first()
            elif isinstance(security_payments_rel, (list, tuple)):
                latest_security_payment = security_payments_rel[-1] if security_payments_rel else None
            else:
                latest_security_payment = None

            if latest_security_payment is None:
                return True

            return BookingPDFService._parse_bool_flag(getattr(latest_security_payment, 'is_paid', True), default=True)
        except Exception as e:
            logger.warning(f"Failed to resolve security payment status for booking invoice: {e}")
            return True
    
    @staticmethod
    def validate_data(shop, client, transaction, items):
        """Validate required fields matching old validation."""
        errors = []
        
        if not shop:
            errors.append("Shop object is required")
        elif not BookingPDFService.safe_get(shop, 'name'):
            errors.append("Shop name is required")
        
        if not client:
            errors.append("Client object is required")
        elif not BookingPDFService.safe_get(client, 'client_name'):
            errors.append("Client name is required")
        
        if not transaction:
            errors.append("Transaction object is required")
        
        if not items or len(items) == 0:
            errors.append("At least one item is required")
        
        if errors:
            error_msg = "; ".join(errors)
            logger.error(f"Transaction data validation failed: {error_msg}")
            raise ValueError(f"Invalid transaction data: {error_msg}")
    
    @staticmethod
    def get_service_category(item):
        """Get main service category for an item matching old logic."""
        try:
            if hasattr(item, 'product_variant') and item.product_variant:
                product = item.product_variant.product
                if hasattr(product, 'shop_service') and product.shop_service:
                    shop_service = product.shop_service
                    if hasattr(shop_service, 'general_service') and shop_service.general_service:
                        general_service = shop_service.general_service
                        if hasattr(general_service, 'main_category') and general_service.main_category:
                            return general_service.main_category.name.lower()
        except Exception as e:
            logger.warning(f"Failed to get main service category for item: {e}")
        return None
    
    @staticmethod
    def build_item_details(item):
        """Build item details based on category matching old logic."""
        product_name = BookingPDFService.safe_get(item, 'product_name', 'Unknown Product')
        category = BookingPDFService.safe_get(item, 'category', '')
        model = BookingPDFService.safe_get(item, 'model', '')
        color = BookingPDFService.safe_get(item, 'color', '')
        variant = BookingPDFService.safe_get(item, 'variant_attribute', '')
        
        main_category = BookingPDFService.get_service_category(item)
        
        details = []
        
        if main_category and ('dress' in main_category or 'costume' in main_category):
            if variant:
                details.append(f"Size: {variant}")
            if color:
                details.append(f"Color: {color}")
        elif main_category and 'vehicle' in main_category:
            if model:
                details.append(f"Model: {model}")
            if category:
                details.append(f"Brand: {category}")
        elif main_category and 'equipment' in main_category:
            if model:
                details.append(f"Model: {model}")
            if category:
                details.append(f"Category: {category}")
        elif main_category and 'gadget' in main_category:
            if model:
                details.append(f"Model: {model}")
            if category:
                details.append(f"Serial No: {category}")
        else:
            if color:
                details.append(f"Color: {color}")
            if category:
                details.append(f"Category: {category}")
        
        return product_name, details

    @staticmethod
    def get_booking_info(transaction, main_service_name=""):
        booking_info = []

        # Determine labels based on main service category name.
        # main_service_name is resolved by the caller from the booked items'
        # product chain (the authoritative source). Defaults to "" → "Pickup"/"Return".
        is_room_booking = "room" in (main_service_name or "").lower()
        pickup_label = "Check-in" if is_room_booking else "Pickup"
        return_label = "Check-out" if is_room_booking else "Return"

        pickup_date = BookingPDFService.safe_get(transaction, 'pickup_date')
        pickup_time = BookingPDFService.safe_get(transaction, 'pickup_time')

        pickup_display = BookingPDFService.extract_display_datetime(
            pickup_date,
            pickup_time,
            ignore_time=time(0, 0, 0)   # Ignore 00:00:00
        )

        if pickup_display:
            booking_info.append(f"{pickup_label}: {pickup_display}")

        return_date = BookingPDFService.safe_get(transaction, 'return_date')
        return_time = BookingPDFService.safe_get(transaction, 'return_time')

        return_display = BookingPDFService.extract_display_datetime(
            return_date,
            return_time,
            ignore_time=time(23, 59, 59)   # Ignore 23:59:59
        )

        if return_display:
            booking_info.append(f"{return_label}: {return_display}")

        # ---------- DETAILS ----------
        details = BookingPDFService.safe_get(transaction, 'details', None)

        if details:
            details_dict = BookingPDFService.parse_transaction_details(details)

            if details_dict:
                label_map = {
                    "location_start": "Start Location",
                    "location_from": "Pickup Location",
                    "location_to": "Destination",
                    "total_product_count": "Total Item Count",
                }

                for key, label in label_map.items():
                    value = details_dict.get(key)
                    if value:
                        booking_info.append(f"{label}: {value}")

                # ---------- ROOM-SPECIFIC DETAILS ----------
                if is_room_booking:

                    # 1. Meals — key: room_meals, value: list of strings
                    meals_raw = details_dict.get("room_meals")
                    if meals_raw and isinstance(meals_raw, list):
                        meals_display = ", ".join(
                            m.strip().capitalize() for m in meals_raw if m and str(m).strip()
                        )
                        if meals_display:
                            booking_info.append(f"<strong>Meals:</strong> {meals_display}")

                    # 2. Guest counts — key: room_guests, value: dict
                    #    Handle all reasonable key variants gracefully.
                    guests_raw = details_dict.get("room_guests")
                    if guests_raw and isinstance(guests_raw, dict):
                        GUEST_KEY_MAP = [
                            (["adults", "adult"],   "Adults"),
                            (["children", "child"], "Children"),
                            (["person", "persons"], "Persons"),
                        ]
                        for possible_keys, guest_label in GUEST_KEY_MAP:
                            for key in possible_keys:
                                value = guests_raw.get(key)
                                if value is not None:  # 0 is a valid count
                                    booking_info.append(f"<strong>{guest_label}:</strong> {value}")
                                    break

                    # 3. Any other top-level room key not already handled —
                    #    display as "Key Name: value" (snake_case → Title Case).
                    HANDLED_ROOM_KEYS = {"room_meals", "room_guests"}
                    STANDARD_KEYS = set(label_map.keys())
                    for key, value in details_dict.items():
                        if key in HANDLED_ROOM_KEYS or key in STANDARD_KEYS:
                            continue
                        if value is None or value == "" or value == [] or value == {}:
                            continue
                        extra_label = key.replace("_", " ").title()
                        if isinstance(value, list):
                            display = ", ".join(str(v).capitalize() for v in value if v)
                        elif isinstance(value, dict):
                            continue  # skip nested dicts not explicitly handled
                        else:
                            display = str(value).strip()
                        if display:
                            booking_info.append(f"{extra_label}: {display}")

        return booking_info
 

    
    @staticmethod
    def generate_transaction_pdf(shop, client, transaction, items, invoice_prefix="INV", main_service_name=""):
        """Generate booking transaction PDF matching old ReportLab design."""
        # Customized invoice template (invoice_templates app) when switched on
        # for this shop; None -> the existing implementation below runs as-is.
        from invoice_templates.v3.dispatcher import try_render_customized
        customized = try_render_customized("booking", shop=shop, client=client, obj=transaction,
                                           items=items, main_service_name=main_service_name)
        if customized is not None:
            return customized

        BookingPDFService.validate_data(shop, client, transaction, items)
        
        # Get SVG backgrounds
        first_page_bg = get_svg_background("images/invoice_pdf_layout.svg")
        later_pages_bg = get_svg_background("images/other_pages.svg")
        
        # Prepare shop data matching old format
        # shop_name = BookingPDFService.safe_get(shop, 'name', '')
        shop_name = BookingPDFService.split_text_by_words(BookingPDFService.safe_get(shop, 'name', ''))
        shop_address = BookingPDFService.safe_get(shop, 'address', '')
        shop_city = BookingPDFService.safe_get(shop, 'city', '')
        # shop_state = BookingPDFService.safe_get(shop, 'state', '')
        shop_pincode = BookingPDFService.safe_get(shop, 'pincode', '')
        shop_phone = BookingPDFService.safe_get(shop, 'phone', '')
        shop_phone2 = BookingPDFService.safe_get(shop, 'phone2', '')
        shop_email = BookingPDFService.safe_get(shop, 'email', '')
        shop_logo = BookingPDFService.download_image(BookingPDFService.safe_get(shop, 'img'))
        shop_terms = BookingPDFService.safe_get(shop, 'terms_and_conditions', [])

        # Fetch the shop's product code prefix once (single query, not per-item)
        # so item names can show "Name - PrefixCode". Deliberately not using
        # Product.full_product_code here, since that property falls back to
        # "PRD" when a shop has no configured prefix - this invoice must show
        # no prefix (just the raw code) in that case instead.
        from shop.models import ShopNumberingSequence
        shop_id = BookingPDFService.safe_get(shop, 'id')
        product_code_prefix = ShopNumberingSequence.objects.filter(
            shop_id=shop_id
        ).values_list('product_code_prefix', flat=True).first() or ''

        # Build location text like old version
        # location_parts = [p for p in [shop_city, shop_state] if p]
        location_parts = [p for p in [shop_city] if p]
        location_text = ", ".join(location_parts)
        # if shop_pincode:
        #     location_text = f"{location_text} - {shop_pincode}" if location_text else shop_pincode
        
        # Prepare invoice data
        invoice_no = BookingPDFService.safe_get(transaction, 'shop_sale_id') or \
                    BookingPDFService.safe_get(transaction, 'shop_booking_id', 'N/A')
        invoice_date = BookingPDFService.safe_get(transaction, 'created_at') or \
                      BookingPDFService.safe_get(transaction, 'date')
        invoice_date_str = BookingPDFService.format_datetime(invoice_date)
        staff_name = BookingPDFService.get_staff_name(transaction)
        staff_row_html = BookingPDFService.build_staff_row_html(staff_name)
        
        # Prepare client data
        client_name = BookingPDFService.safe_get(client, 'client_name', '')
        client_phone = BookingPDFService.safe_get(client, 'phone_1_e164', '') or \
                          BookingPDFService.safe_get(client, 'client_phone_1', '')
        client_address = BookingPDFService.safe_get(transaction, 'client_address') or \
                        BookingPDFService.safe_get(client, 'address', '')
        
        # Get font families for client data
        client_name_font = BookingPDFService.get_font_family(client_name, True)
        client_name_weight = BookingPDFService.get_font_weight(client_name, True)
        client_address_font = BookingPDFService.get_font_family(client_address)
        client_address_weight = BookingPDFService.get_font_weight(client_address)
        
        # Collect all image URLs for parallel download (performance optimization)
        # Fallback priority: item.product_thumbnail → product.thumbnail_image → product.image
        image_urls = []
        for item in items:
            img_url = BookingPDFService.safe_get(item, 'product_thumbnail')  # Step 1: item-level thumbnail
            if not img_url:
                try:
                    if item.product_variant and item.product_variant.product:
                        prod = item.product_variant.product
                        img_url = prod.thumbnail_image or prod.image          # Steps 2 & 3
                except Exception:
                    pass
            if img_url:
                image_urls.append(img_url)

        # Download all images in parallel - significant performance boost for multi-item PDFs
        logger.info(f"Downloading {len(image_urls)} images in parallel for booking PDF")
        image_results = BookingPDFService.download_images_parallel(image_urls) if image_urls else {}

        # Prepare items with pre-downloaded images
        items_data = []
        for idx, item in enumerate(items, 1):
            name, details = BookingPDFService.build_item_details(item)

            # Get pre-downloaded image using same fallback chain as the collection loop above
            img_url = BookingPDFService.safe_get(item, 'product_thumbnail')
            if not img_url:
                try:
                    if item.product_variant and item.product_variant.product:
                        prod = item.product_variant.product
                        img_url = prod.thumbnail_image or prod.image
                except Exception:
                    pass
            item_image = image_results.get(img_url) if img_url else None

            if img_url and not item_image:
                logger.warning(f"Failed to load image for item {idx}: {name} from {img_url}")

            # Resolve the product code line (own line, no label) using the
            # shop-wide prefix resolved once above. Uses the raw product_code
            # field (not full_product_code) so a shop with no configured
            # prefix shows just the number, never falls back to "PRD".
            product_code_line = ''
            try:
                if item.product_variant and item.product_variant.product:
                    raw_code = item.product_variant.product.product_code
                    if raw_code:
                        product_code_line = f"{product_code_prefix}{raw_code}"
            except Exception:
                pass

            quantity = BookingPDFService.safe_get(item, 'quantity', 0)
            amount = BookingPDFService.safe_get(item, 'amount', 0)

            # Get font for item name
            item_name_font = BookingPDFService.get_font_family(name, True)
            item_name_weight = BookingPDFService.get_font_weight(name, True)

            items_data.append({
                'no': idx,
                'image': item_image,
                'name': name,
                'name_font': item_name_font,
                'name_weight': item_name_weight,
                'product_code_line': product_code_line,
                'details': details,
                'quantity': quantity,
                'amount': BookingPDFService.format_currency(amount)
            })
        
        # Prepare totals matching old calculation logic
        try:
            total = Decimal(str(BookingPDFService.safe_get(transaction, 'total_amount', 0)))
            security = Decimal(str(BookingPDFService.safe_get(transaction, 'security_amount', 0)))
            discount = Decimal(str(BookingPDFService.safe_get(transaction, 'discount_amount', 0)))
            paid = Decimal(str(BookingPDFService.safe_get(transaction, 'advance_amount', 0)))
            is_tax_applied = bool(BookingPDFService.safe_get(transaction, 'is_tax_applied', False))
            tax_name = BookingPDFService.safe_get(transaction, 'tax_name', '') or 'GST'
            tax_rate = Decimal(str(BookingPDFService.safe_get(transaction, 'tax_rate', 0)))
            tax_amount = Decimal(str(BookingPDFService.safe_get(transaction, 'tax_amount', 0)))
            tax_calculation_type = BookingPDFService.safe_get(transaction, 'tax_calculation_type', '') or ''
            # Inclusive tax: total_amount is the raw, tax-embedded figure (tax
            # was never added on top of it) - tax_amount is only an extracted
            # breakdown of a portion of that same total, not an addend.
            # Exclusive tax: total_amount already has tax_amount added on top.
            is_inclusive_tax = is_tax_applied and tax_calculation_type == 'inclusive'
        except (ValueError, InvalidOperation) as e:
            logger.error(f"Failed to parse decimal values: {e}")
            raise ValueError(f"Invalid decimal values in transaction: {e}")
        
        # Calculate additional charges
        additional_total = Decimal(0)
        additional_charges = []
        charges = BookingPDFService.safe_get(transaction, 'additional_charges')
        if charges:
            try:
                for charge in charges.all():
                    amount = Decimal(str(BookingPDFService.safe_get(charge, 'amount', 0)))
                    additional_total += amount
                    additional_charges.append({
                        'name': BookingPDFService.safe_get(charge, 'name', 'Additional Charge'),
                        'amount': BookingPDFService.format_currency(amount)
                    })
            except Exception as e:
                logger.warning(f"Failed to calculate additional charges: {e}")
        
        security_amount_is_paid = BookingPDFService._resolve_security_amount_is_paid(transaction)

        grand_total = (total + security) - discount
        # Only subtract tax_amount for Exclusive tax (where it was genuinely
        # added on top of total). For Inclusive tax, total is already the
        # raw, tax-embedded figure, so subtracting tax_amount here would
        # under-report the product cost by the tax portion.
        sub_total = total - additional_total - (Decimal(0) if is_inclusive_tax else tax_amount)
        collected_security = security if security_amount_is_paid else Decimal(0)
        paid_with_security = paid + collected_security
        total_charges = grand_total - security
        total_payable = total_charges + security
        show_total_charges = security > 0 or additional_total > 0 or discount > 0
        is_cancelled = str(BookingPDFService.safe_get(transaction, 'delivery_status', '')).strip().lower() == 'cancelled'

        # Refund/adjustment data used for conditional status sections.
        security_refund = Decimal(0)
        security_deduction = Decimal(0)
        charges_refund = Decimal(0)
        try:
            from booking_v2.models import SecurityDepositAdjustment, RefundDetails

            booking_id = BookingPDFService.safe_get(transaction, 'id')
            if booking_id:
                security_refund = Decimal(str(
                    SecurityDepositAdjustment.objects.filter(
                        booking_id=booking_id,
                        adjustment_type='refund'
                    ).aggregate(total=Sum('amount'))['total'] or 0
                ))
                security_deduction = Decimal(str(
                    SecurityDepositAdjustment.objects.filter(
                        booking_id=booking_id,
                        adjustment_type='deduction'
                    ).aggregate(total=Sum('amount'))['total'] or 0
                ))
                charges_refund = Decimal(str(
                    RefundDetails.objects.filter(
                        booking_id=booking_id
                    ).aggregate(total=Sum('refunded_amount'))['total'] or 0
                ))
        except Exception as e:
            logger.warning(f"Failed to calculate refund details for booking invoice: {e}")

        security_pending = max(security - security_refund - security_deduction, Decimal(0))
        balance = max(total_payable - paid_with_security, Decimal(0))
        total_refunded = charges_refund + security_refund
        net_amount = max(total_payable - total_refunded, Decimal(0))
        cancellation_penalty = net_amount if is_cancelled else Decimal(0)

        # Default terms matching old version
        default_terms = DEFAULT_BOOKING_TERMS

        terms = shop_terms if (shop_terms and isinstance(shop_terms, list) and len(shop_terms) > 0) else default_terms
        
        # Get booking info
        booking_info = BookingPDFService.get_booking_info(transaction, main_service_name=main_service_name)
        is_room_booking = "room" in (main_service_name or "").lower()

        # ── Payment + refund history (merged, chronological) ─────────────────
        # NOTE: Temporarily disabled — payment history not shown in PDF currently.
        # payment_history_rows = []
        # try:
        #     from booking_v2.models import PaymentDetailsV2, RefundDetails
        #     booking_id = BookingPDFService.safe_get(transaction, 'id')
        #     if booking_id:
        #         combined = []
        #         for p in PaymentDetailsV2.objects.filter(booking_id=booking_id, deleted__isnull=True):
        #             combined.append({
        #                 'amount':    BookingPDFService.format_currency(p.payment_amount),
        #                 'method':    (p.payment_method or '').upper(),
        #                 'date':      BookingPDFService.format_datetime(p.created_at),
        #                 'sort_key':  p.created_at,
        #                 'is_refund': False,
        #             })
        #         for r in RefundDetails.objects.filter(booking_id=booking_id):
        #             combined.append({
        #                 'amount':    BookingPDFService.format_currency(r.refunded_amount),
        #                 'method':    (r.refund_method or '').upper(),
        #                 'date':      BookingPDFService.format_datetime(r.created_at),
        #                 'sort_key':  r.created_at,
        #                 'is_refund': True,
        #             })
        #         payment_history_rows = sorted(combined, key=lambda x: x['sort_key'])
        # except Exception as e:
        #     logger.warning(f"Failed to fetch payment history for booking PDF: {e}")
        # payment_history_html = _build_payment_history_html(payment_history_rows)
        payment_history_html = ''

        # Build shop details dynamically in the requested layout
        shop_details_html = BookingPDFService.build_shop_details_html(shop)
        
        # Build HTML with old styling
        items_html = ''
        for item in items_data:
            image_html = '<div class="empty-image">No Image</div>'
            if item['image']:
                # Ensure image is properly formatted
                try:
                    # Clean up the data URL if needed
                    image_url = item['image'].strip()
                    if image_url.startswith('data:image'):
                        image_html = f'<img src="{image_url}" class="item-image" alt="{item["name"]}" />'
                        logger.info(f"Image loaded successfully for item {item['no']}")
                    else:
                        logger.warning(f"Invalid image format for item {item['no']}: {image_url[:50]}...")
                        image_html = '<div class="empty-image">Invalid Image</div>'
                except Exception as e:
                    logger.warning(f"Error formatting image HTML for item {item['no']}: {e}")
                    image_html = '<div class="empty-image">Error</div>'

            # Product code line - own line, no label (e.g. "PRD00001")
            code_line_html = ''
            if item['product_code_line']:
                code_line_html = f'<div class="item-meta">{item["product_code_line"]}</div>'

            # Combine all applicable attribute details onto one comma-separated line
            # (e.g. "Size: XL, Color: Red") instead of stacking them as separate lines.
            details_html = ''
            if item['details']:
                combined_details = ', '.join(item['details'])
                details_html = f'<div class="item-meta">{combined_details}</div>'

            items_html += f'''
        <tr>
            <td class="text-center">{item['no']}</td>
            <td>
                <div class="item-row">
                    {image_html}
                    <div class="item-details">
                        <div class="item-name" style="font-family: {item['name_font']}; font-weight: {item['name_weight']};">{item['name']}</div>
                        {code_line_html}
                        {details_html}
                    </div>
                </div>
            </td>
            <td class="text-center">{item['quantity']}</td>
            <td class="amount-cell">{item['amount']}</td>
        </tr>
        '''

        additional_html = ''.join([f'''
        <div class="total-row discount-row">
            <span class="total-label">{charge["name"].upper()}</span>
            <span class="total-value">{charge["amount"]}</span>
        </div>
        ''' for charge in additional_charges])
        
        security_html = f'''
        <div class="total-row discount-row section-gap">
            <span class="total-label">SECURITY </span>
            <span class="total-value">{BookingPDFService.format_currency(security)}</span>
        </div>
        ''' if security > 0 else ''
        
        discount_html = f'''
        <div class="total-row discount-row">
            <span class="total-label">DISCOUNT</span>
            <span class="total-value">-{BookingPDFService.format_currency(discount)}</span>
        </div>
        ''' if discount > 0 else ''

        # GST breakdown row: only shown for Exclusive tax, where it's a
        # genuine additive line. For Inclusive tax it's already embedded in
        # the product cost above, so showing it again here would look like
        # a duplicate/second tax charge - an inclusive_tax_note_html is
        # shown near TOTAL PAYABLE instead.
        tax_html = BookingPDFService.build_tax_rows_html(
            tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax,
            BookingPDFService.format_currency,
        )

        total_payable_html = f'''
        <div class="total-row grand-total-row">
            <span class="total-label">TOTAL PAYABLE</span>
            <span class="total-value">{BookingPDFService.format_currency(total_payable)}</span>
        </div>
        '''

        inclusive_tax_note_html = '''
        <div style="font-size:7.5pt;color:#6B7280;text-align:right;margin-top:2px;">(Inclusive of all taxes.)</div>
        ''' if is_inclusive_tax else ''

        if is_cancelled:
            cancelled_banner_html = '''
            <div class="total-row cancelled-row">
                <span class="total-label">BOOKING CANCELLED</span>
                <span class="total-value"></span>
            </div>
            '''

            refund_rows = []

            total_refunded_html = f'''
            <div class="total-row discount-row">
                <span class="total-label">REFUNDED</span>
                <span class="total-value">-{BookingPDFService.format_currency(total_refunded)}</span>
            </div>
            ''' if total_refunded > 0 else ''

            payments_html = f'''
            <div class="total-row subtotal-row status-header">
                <span class="total-value"></span>
            </div>
            <div class="total-row paid-amount-row">
                <span class="total-label">PAID AMOUNT</span>
                <span class="total-value">{BookingPDFService.format_currency(paid_with_security)}</span>
            </div>
            {cancelled_banner_html}
            {''.join(refund_rows)}
            {total_refunded_html}
            '''
            balance_html = ''
        else:
            security_status_rows = []
            if security > 0 and security_amount_is_paid:
                security_status_rows.append(f'''
                <div class="total-row discount-row">
                    <span class="total-label">SECURITY COLLECTED</span>
                    <span class="total-value">{BookingPDFService.format_currency(collected_security)}</span>
                </div>
                ''')
                if security_deduction > 0:
                    security_status_rows.append(f'''
                    <div class="total-row discount-row">
                        <span class="total-label">DEDUCTION </span>
                        <span class="total-value">{BookingPDFService.format_currency(security_deduction)}</span>
                    </div>
                    ''')
                security_status_rows.append(f'''
                <div class="total-row discount-row">
                    <span class="total-label">REFUNDED</span>
                    <span class="total-value">{BookingPDFService.format_currency(security_refund)}</span>
                </div>
                ''')

            payments_html = f'''
            <div class="total-row subtotal-row status-header">
                <span class="total-value"></span>
            </div>
            <div class="total-row paid-amount-row">
                <span class="total-label">PAID AMOUNT</span>
                <span class="total-value">{BookingPDFService.format_currency(paid_with_security)}</span>
            </div>
            <div class="total-row balance-due-row">
                <span class="total-label">BALANCE DUE</span>
                <span class="total-value">{BookingPDFService.format_currency(balance)}</span>
            </div>
            {('<div class="total-row subtotal-row status-header"><span class="total-value"></span></div>' + ''.join(security_status_rows)) if security_status_rows else ''}
            '''
            balance_html = ''

        # Build booking info HTML
        booking_info_html = ''
        if booking_info:
            booking_info_html = ''.join([f'<div class="order-info-item">{info}</div>' for info in booking_info])
        
        # Build terms HTML
        terms_html = ''
        if terms:
            terms_html = ''.join([f'<div>- {term}</div>' for term in terms])

        shop_details_html = BookingPDFService.build_shop_details_html(shop)

        # Build CSS with background images in @page rules for proper multi-page support
        extra_css = ""
        if first_page_bg:
            extra_css += f"""
        @page :first {{
            background: url('{first_page_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """
        if later_pages_bg:
            extra_css += f"""
        @page {{
            background: url('{later_pages_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """
        
        # Build measurements additional page (for Dress category)
        try:
            dress_sections = []  # list of (heading, rows)
            person_sections = []  # new per-person format: list of (heading, columns, rows)

            for item in items:
                try:
                    variant = getattr(item, 'product_variant', None)
                    product = getattr(variant, 'product', None) if variant else None
                    shop_service = getattr(product, 'shop_service', None) if product else None
                    general_service = getattr(shop_service, 'general_service', None) if shop_service else None
                    main_cat = getattr(general_service, 'main_category', None) if general_service else None
                    main_name = (getattr(main_cat, 'name', '') or '').lower()

                    measurements = getattr(item, 'measurements', None)

                    # Skip if not a 'dress' category or measurements empty
                    is_dress = ('dress' in main_name) if main_name else False
                    rows = []
                    # New per-person entries ({"name": <person>, "value": {...}})
                    # get their own table; every other entry follows the legacy
                    # path unchanged. A list can mix both (legacy rows plus a
                    # person entry), so split per entry rather than per list.
                    person_entries = []
                    legacy_measurements = measurements
                    if BookingPDFService._is_person_measurements(measurements):
                        person_entries = [
                            m for m in measurements
                            if BookingPDFService._is_person_measurements([m])
                        ]
                        legacy_measurements = [
                            m for m in measurements
                            if not BookingPDFService._is_person_measurements([m])
                        ]

                    if legacy_measurements:
                        # dict -> rows
                        if isinstance(legacy_measurements, dict):
                            for k, v in legacy_measurements.items():
                                if k is not None and v not in (None, ''):
                                    rows.append({'name': str(k), 'value': str(v)})
                        # list of dicts -> rows
                        elif isinstance(legacy_measurements, list):
                            for m in legacy_measurements:
                                if isinstance(m, dict):
                                    name = m.get('name')
                                    value = m.get('value')
                                    if name and value not in (None, ''):
                                        rows.append({'name': str(name), 'value': str(value)})

                    if is_dress and (rows or person_entries):
                        product_name = ''
                        try:
                            product_name = BookingPDFService.safe_get(product, 'name', '') if product else ''
                            if not product_name:
                                product_name = BookingPDFService.safe_get(item, 'product_name', 'Unknown Product')
                        except Exception:
                            product_name = BookingPDFService.safe_get(item, 'product_name', 'Unknown Product')

                        variant_attr = ''
                        try:
                            variant_attr = BookingPDFService.safe_get(variant, 'attribute', '') if variant else ''
                            if not variant_attr:
                                variant_attr = BookingPDFService.safe_get(item, 'variant_attribute', '')
                        except Exception:
                            variant_attr = BookingPDFService.safe_get(item, 'variant_attribute', '')

                        heading = product_name
                        if variant_attr:
                            heading += f" - {variant_attr}"

                        if rows:
                            dress_sections.append((heading, rows))
                        if person_entries:
                            columns, person_rows = BookingPDFService._build_person_measurement_table(person_entries)
                            if person_rows:
                                person_sections.append((heading, columns, person_rows))
                except Exception as e:
                    logger.warning(f"Error processing measurements section for item: {e}")

            _meas_pickup_date = BookingPDFService.safe_get(transaction, 'pickup_date')
            _meas_pickup_str = BookingPDFService.format_date(_meas_pickup_date) if _meas_pickup_date else ''
            _left_parts = []
            if invoice_no:
                _left_parts.append(f'<span>Invoice: <strong>{invoice_no}</strong></span>')
            if _meas_pickup_str:
                _left_parts.append(f'<span>Pickup: {_meas_pickup_str}</span>')
            _right_parts = []
            if client_name:
                _right_parts.append(f'<strong style="font-size:12pt;">{client_name}</strong>')
            if client_phone:
                _right_parts.append(f'<span>{client_phone}</span>')
            _left_html = '<br/>'.join(_left_parts)
            _right_html = '<br/>'.join(_right_parts)
            booking_client_info = (
                f'<table style="width:100%;border:none;background:transparent;">'
                f'<tr style="background:transparent;">'
                f'<td style="width:50%;border:none;vertical-align:middle;text-align:left;padding:0;">{_left_html}</td>'
                f'<td style="width:50%;border:none;vertical-align:middle;text-align:right;padding:0;">{_right_html}</td>'
                f'</tr></table>'
            ) if (_left_html or _right_html) else ''
            # measurements_page_html = BasePDFService._build_measurements_page_html(
            #     dress_sections, booking_client_info
            # )
            measurements_page_html = BasePDFService._build_measurements_page_html(
                dress_sections, booking_client_info, staff_name=staff_name,
                person_sections=person_sections,
            )
        except Exception as e:
            logger.error(f"Failed building measurements page: {e}", exc_info=True)
            measurements_page_html = ''

        # Terms & Conditions placement is decided by actually rendering the
        # main content and the terms block and measuring where they land —
        # see BasePDFService._resolve_terms_placement. Layout is always
        # block mode now (the flex/single-page special case was retired: it
        # was a second layout mode that could disagree with the one terms
        # placement was decided against — the exact bug measurements pages
        # used to trigger).
        main_content_inner_html = f"""
                    <div class="header">
                        {f'<img src="{shop_logo}" class="logo" />' if shop_logo else '<div class="empty-logo">No Logo</div>'}
                        <div class="shop-info">
                            <div class="shop-name">{shop_name}</div>
                            <div class="shop-details">
                                {shop_details_html}
                            </div>
                        </div>
                    </div>

                    <div class="invoice-section">
                        <div class="invoice-info">
                            <div class="invoice-row">
                                <span class="invoice-label">INVOICE ID:</span>
                                <span class="invoice-value">{invoice_no}</span>
                            </div>
                            <div class="invoice-row">
                                <span class="invoice-label">DATE:</span>
                                <span class="invoice-value">{invoice_date_str}</span>
                            </div>
                            {staff_row_html}
                        </div>
                        <div class="bill-to">
                            <div class="section-title">BILL TO</div>
                            <div class="bill-to-content">
                                <div class="bill-to-name" style="font-family: {client_name_font}; font-weight: {client_name_weight};">{client_name}</div>
                                <div class="bill-to-phone">{client_phone}</div>
                                <div class="bill-to-address" style="font-family: {client_address_font}; font-weight: {client_address_weight};">{client_address}</div>
                            </div>
                        </div>
                    </div>

                    <table>
                        <thead>
                            <tr>
                                <th style="width: 40px;" class="text-center">NO</th>
                                <th>ITEM</th>
                                <th style="width: 60px;" class="text-center">QTY</th>
                                <th style="width: 100px;" class="text-right">AMOUNT</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>

                    <div class="totals-section">
                        <div class="order-info">
                            <div class="order-info-title">BOOKING <span class="highlight">INFO:</span></div>
                            {booking_info_html}
                            {payment_history_html}
                        </div>
                        <div class="totals">

                            <div class="total-row subtotal-row">
                                <span class="total-label">{"ROOM PRICE" if is_room_booking else "PRODUCT COST"}</span>
                                <span class="total-value">{BookingPDFService.format_currency(sub_total)}</span>
                            </div>
                            {additional_html}
                            {discount_html}
                            {security_html}
                            {tax_html}
                            {total_payable_html}
                            {inclusive_tax_note_html}
                            {payments_html}
                            {balance_html}
                        </div>
                    </div>
        """

        footer_content_inner_html = f"""
                    <div class="terms">
                        <div class="terms-title">
                            TERMS AND <span class="highlight">CONDITIONS</span>
                        </div>
                        <div class="terms-list">
                            {terms_html}
                        </div>
                    </div>
        """

        measure_css = BasePDFService.get_font_css() + "\n" + BasePDFService.get_base_css(first_page=True, multi_page=True)
        footer_page_break_style, terms_spacer_html = BasePDFService._resolve_terms_placement(
            main_content_inner_html, footer_content_inner_html, measure_css
        )

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body>
            <div class="content">
                <div class="main-content">
                    {main_content_inner_html}
                </div>

                {terms_spacer_html}
                <div class="footer-content" style="{footer_page_break_style}">
                    {footer_content_inner_html}
                </div>
                {measurements_page_html}
            </div>
        </body>
        </html>
        """

        try:
            pdf_bytes = BookingPDFService.generate_pdf(html, first_page=True, extra_css=extra_css, multi_page=True)
            return base64.b64encode(pdf_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to generate PDF: {e}", exc_info=True)
            raise
    

# ============================================================================
# TAILOR ORDER PDF SERVICE
# ============================================================================
class TailorOrderPDFService(BasePDFService):
    """PDF service for tailor order invoice documents."""

    @staticmethod
    def _resolve_security_is_paid(tailor_order) -> bool:
        """
        Resolve whether security has been collected for this tailor order.

        Resolution order:
        1. If a TailorOrderSecurityPayment record exists, use the latest row's is_paid flag.
        2. If NO records exist, default to True (backward-compatible: security was historically
           always added to the paid amount without a separate payment record).
        """
        try:
            security_payments_rel = getattr(tailor_order, 'security_payments', None)
            if security_payments_rel is None:
                return True  # No relation at all → treat as paid (backward compatible)
            latest = security_payments_rel.all().order_by('-created_at').first()
            if latest is None:
                return True  # No payment records → treat as paid (backward compatible)
            return bool(latest.is_paid)
        except Exception as e:
            logger.warning(f"Failed to resolve security payment status for tailor order invoice: {e}")
            return True  # Safe default: include security in paid amount on error

    @staticmethod
    def validate_data(shop, tailor_order, items):
        errors = []
        if not shop:
            errors.append("Shop object is required")
        elif not TailorOrderPDFService.safe_get(shop, 'name'):
            errors.append("Shop name is required")
        if not tailor_order:
            errors.append("Tailor order object is required")
        if not items or len(list(items)) == 0:
            errors.append("At least one tailor order item is required")
        if errors:
            error_msg = "; ".join(errors)
            logger.error(f"Tailor order data validation failed: {error_msg}")
            raise ValueError(f"Invalid tailor order data: {error_msg}")

    @staticmethod
    def generate_tailor_order_pdf(shop, client, tailor_order, items):
        """Generate tailor order invoice PDF."""
        items = list(items)  # materialise queryset once

        # Customized invoice template (invoice_templates app) when switched on
        # for this shop; None -> the existing implementation below runs as-is.
        from invoice_templates.v3.dispatcher import try_render_customized
        customized = try_render_customized("tailor_order", shop=shop, client=client, obj=tailor_order, items=items)
        if customized is not None:
            return customized

        TailorOrderPDFService.validate_data(shop, tailor_order, items)

        # SVG backgrounds
        first_page_bg = get_svg_background("images/invoice_pdf_layout.svg")
        later_pages_bg = get_svg_background("images/other_pages.svg")

        # ── Shop header ──────────────────────────────────────────────────────
        shop_name = TailorOrderPDFService.split_text_by_words(
            TailorOrderPDFService.safe_get(shop, 'name', '')
        )
        shop_address = TailorOrderPDFService.safe_get(shop, 'address', '')
        shop_city    = TailorOrderPDFService.safe_get(shop, 'city', '')
        shop_pincode = TailorOrderPDFService.safe_get(shop, 'pincode', '')
        shop_phone   = TailorOrderPDFService.safe_get(shop, 'phone', '')
        shop_phone2  = TailorOrderPDFService.safe_get(shop, 'phone2', '')
        shop_email   = TailorOrderPDFService.safe_get(shop, 'email', '')
        shop_logo    = TailorOrderPDFService.download_image(TailorOrderPDFService.safe_get(shop, 'img'))
        shop_terms   = TailorOrderPDFService.safe_get(shop, 'terms_and_conditions', [])

        location_parts = [p for p in [shop_city] if p]
        location_text  = ", ".join(location_parts)
        if shop_pincode:
            location_text = f"{location_text} - {shop_pincode}" if location_text else shop_pincode

        shop_details_html = TailorOrderPDFService.build_shop_details_html(shop)

        # ── Invoice header ───────────────────────────────────────────────────
        invoice_no       = TailorOrderPDFService.safe_get(tailor_order, 'shop_tailor_id') or "N/A"
        invoice_date     = TailorOrderPDFService.safe_get(tailor_order, 'created_at')
        invoice_date_str = TailorOrderPDFService.format_datetime(invoice_date)
        staff_name       = TailorOrderPDFService.get_staff_name(tailor_order)
        staff_row_html   = TailorOrderPDFService.build_staff_row_html(staff_name)

        # ── Client info ──────────────────────────────────────────────────────
        client_name  = TailorOrderPDFService.safe_get(client, 'client_name', '') if client else ''
        client_phone = (
            TailorOrderPDFService.safe_get(client, 'phone_1_e164', '') if client else ''
        ) or TailorOrderPDFService.safe_get(tailor_order, 'client_phone_number', '')

        client_name_font   = TailorOrderPDFService.get_font_family(client_name, True)
        client_name_weight = TailorOrderPDFService.get_font_weight(client_name, True)

        # ── Parallel image download ──────────────────────────────────────────
        # Fallback priority: item.reference_image → product.thumbnail_image → product.image
        image_urls = []
        for item in items:
            img_url = TailorOrderPDFService.safe_get(item, 'reference_image', None)  # Step 1: item-level image
            if not img_url:
                try:
                    pv = item.created_product_variant
                    if pv and pv.product:
                        img_url = pv.product.thumbnail_image or pv.product.image     # Steps 2 & 3
                except Exception:
                    pass
            if img_url:
                image_urls.append(img_url)

        logger.info(f"Downloading {len(image_urls)} images in parallel for tailor order PDF")
        image_results = TailorOrderPDFService.download_images_parallel(image_urls) if image_urls else {}

        # ── Items table rows ─────────────────────────────────────────────────
        items_html = ''
        item_texts = []
        for idx, item in enumerate(items, 1):
            product_name = TailorOrderPDFService.safe_get(item, 'product_name', 'Unknown Product')
            product_type = TailorOrderPDFService.safe_get(item, 'product_type', '')
            item_texts.append(f"{product_name} {product_type}")
            quantity     = TailorOrderPDFService.safe_get(item, 'quantity', 0)
            amount       = TailorOrderPDFService.format_currency(
                TailorOrderPDFService.safe_get(item, 'customer_item_charge', 0)
            )

            # Same fallback chain as the collection loop above
            img_url = TailorOrderPDFService.safe_get(item, 'reference_image', None)
            if not img_url:
                try:
                    pv = item.created_product_variant
                    if pv and pv.product:
                        img_url = pv.product.thumbnail_image or pv.product.image
                except Exception:
                    pass
            item_image = image_results.get(img_url) if img_url else None

            image_html = '<div class="empty-image">No Image</div>'
            if item_image:
                try:
                    image_url = item_image.strip()
                    if image_url.startswith('data:image'):
                        image_html = f'<img src="{image_url}" class="item-image" alt="{product_name}" />'
                    else:
                        image_html = '<div class="empty-image">Invalid Image</div>'
                except Exception:
                    image_html = '<div class="empty-image">Error</div>'

            name_font   = TailorOrderPDFService.get_font_family(product_name, True)
            name_weight = TailorOrderPDFService.get_font_weight(product_name, True)

            type_html = f'<div class="item-meta">{product_type}</div>' if product_type else ''

            items_html += f'''
        <tr>
            <td class="text-center">{idx}</td>
            <td>
                <div class="item-row">
                    {image_html}
                    <div class="item-details">
                        <div class="item-name" style="font-family: {name_font}; font-weight: {name_weight};">{product_name}</div>
                        {type_html}
                    </div>
                </div>
            </td>
            <td class="text-center">{quantity}</td>
            <td class="amount-cell">{amount}</td>
        </tr>
        '''

        # ── Totals ───────────────────────────────────────────────────────────
        try:
            product_total = sum(
                Decimal(str(TailorOrderPDFService.safe_get(item, 'customer_item_charge', 0) or 0))
                for item in items
            )
            additional_total = Decimal(0)
            additional_charges = []
            charges = TailorOrderPDFService.safe_get(tailor_order, 'additional_charges')
            if charges:
                try:
                    charge_list = list(charges.all()) if hasattr(charges, 'all') else list(charges)
                    for charge in charge_list:
                        amt = Decimal(str(TailorOrderPDFService.safe_get(charge, 'amount', 0)))
                        additional_total += amt
                        additional_charges.append({
                            'name': TailorOrderPDFService.safe_get(charge, 'name', 'Additional Charge'),
                            'amount': TailorOrderPDFService.format_currency(amt)
                        })
                except Exception as e:
                    logger.warning(f"Failed to calculate additional charges for tailor order: {e}")
                    additional_total = Decimal(
                        str(TailorOrderPDFService.safe_get(tailor_order, 'total_additional_charges', 0) or 0)
                    )
            else:
                # charges attribute not present — fall back to stored aggregate field
                additional_total = Decimal(
                    str(TailorOrderPDFService.safe_get(tailor_order, 'total_additional_charges', 0) or 0)
                )
            discount = Decimal(
                str(TailorOrderPDFService.safe_get(tailor_order, 'discount_amount', 0) or 0)
            )
            paid     = Decimal(str(TailorOrderPDFService.safe_get(tailor_order, 'paid_amount', 0) or 0))
            security = Decimal(str(TailorOrderPDFService.safe_get(tailor_order, 'security_amount', 0) or 0))
            is_tax_applied = bool(TailorOrderPDFService.safe_get(tailor_order, 'is_tax_applied', False))
            tax_name = TailorOrderPDFService.safe_get(tailor_order, 'tax_name', '') or 'GST'
            tax_rate = Decimal(str(TailorOrderPDFService.safe_get(tailor_order, 'tax_rate', 0)))
            tax_amount = Decimal(str(TailorOrderPDFService.safe_get(tailor_order, 'tax_amount', 0)))
            tax_calculation_type = TailorOrderPDFService.safe_get(tailor_order, 'tax_calculation_type', '') or ''
            # Inclusive tax: product_total (sum of raw, unmutated
            # customer_item_charge) already represents what the customer
            # pays - tax_amount is only an extracted breakdown, not an
            # addend. Exclusive tax: tax_amount is genuinely additive.
            is_inclusive_tax = is_tax_applied and tax_calculation_type == 'inclusive'
        except (ValueError, InvalidOperation) as e:
            logger.error(f"Failed to parse decimal values for tailor order: {e}")
            raise ValueError(f"Invalid decimal values in tailor order: {e}")

        # Only add tax_amount for Exclusive tax. For Inclusive tax, adding
        # it here would double-count it, since product_total already
        # reflects the tax-inclusive amount the customer pays.
        grand_total = product_total + additional_total + security - discount + (
            Decimal('0') if is_inclusive_tax else tax_amount
        )

        # Security: only include in paid amount if the security payment is marked as paid
        security_is_paid = TailorOrderPDFService._resolve_security_is_paid(tailor_order)
        collected_security = security if security_is_paid else Decimal('0')
        paid_with_security = paid + collected_security

        # Cancellation flag
        is_cancelled = str(getattr(tailor_order, 'status', '')).strip().lower() == 'cancelled'

        # Refund / security adjustment lookups
        security_refund = Decimal('0')
        charges_refund = Decimal('0')
        try:
            from tailored.models import TailorOrderRefund, TailorOrderSecurityDepositAdjustment
            order_id = tailor_order.id
            if order_id:
                security_refund = Decimal(str(
                    TailorOrderSecurityDepositAdjustment.objects.filter(
                        order_id=order_id, adjustment_type='refunded'
                    ).aggregate(total=Sum('amount'))['total'] or 0
                ))
                charges_refund = Decimal(str(
                    TailorOrderRefund.objects.filter(
                        order_id=order_id
                    ).aggregate(total=Sum('refunded_amount'))['total'] or 0
                ))
        except Exception as e:
            logger.warning(f"Failed to calculate refund details for tailor order invoice: {e}")

        total_refunded = charges_refund + security_refund
        balance = max(grand_total - paid_with_security, Decimal('0'))

        product_total_html = f'''
        <div class="total-row discount-row">
            <span class="total-label">PRODUCT TOTAL</span>
            <span class="total-value">{TailorOrderPDFService.format_currency(product_total)}</span>
        </div>
        '''

        # Individual charge rows — one per named charge, no summary total
        additional_total_html = ''.join([
            f'''
        <div class="total-row discount-row">
            <span class="total-label">{charge["name"].upper()}</span>
            <span class="total-value">{charge["amount"]}</span>
        </div>
        '''
            for charge in additional_charges
        ])

        discount_html = f'''
        <div class="total-row discount-row">
            <span class="total-label">DISCOUNT</span>
            <span class="total-value">-{TailorOrderPDFService.format_currency(discount)}</span>
        </div>
        ''' if discount > 0 else ''

        # Security line item — always shown when security_amount > 0, regardless of paid status
        security_html = f'''
        <div class="total-row discount-row section-gap">
            <span class="total-label">SECURITY</span>
            <span class="total-value">{TailorOrderPDFService.format_currency(security)}</span>
        </div>
        ''' if security > 0 else ''

        # GST breakdown row: only shown for Exclusive tax, where it's a
        # genuine additive line. For Inclusive tax it's already embedded in
        # the product total above, so showing it again here would look like
        # a duplicate/second tax charge - an inclusive_tax_note_html is
        # shown near TOTAL PAYABLE instead.
        tax_html = TailorOrderPDFService.build_tax_rows_html(
            tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax,
            TailorOrderPDFService.format_currency,
        )

        inclusive_tax_note_html = '''
        <div style="font-size:7.5pt;color:#6B7280;text-align:right;margin-top:2px;">(Inclusive of all taxes.)</div>
        ''' if is_inclusive_tax else ''

        # Build payments_html based on cancellation and security status
        if is_cancelled:
            cancelled_banner_html = '''
            <div class="total-row cancelled-row">
                <span class="total-label">ORDER CANCELLED</span>
                <span class="total-value"></span>
            </div>
            '''
            total_refunded_html = f'''
            <div class="total-row discount-row">
                <span class="total-label">REFUNDED</span>
                <span class="total-value">-{TailorOrderPDFService.format_currency(total_refunded)}</span>
            </div>
            ''' if total_refunded > 0 else ''

            payments_html = f'''
            <div class="total-row subtotal-row status-header">
                <span class="total-value"></span>
            </div>
            <div class="total-row paid-amount-row">
                <span class="total-label">PAID AMOUNT</span>
                <span class="total-value">{TailorOrderPDFService.format_currency(paid_with_security)}</span>
            </div>
            {cancelled_banner_html}
            {total_refunded_html}
            '''
        else:
            security_status_rows = []
            if security > 0:
                # SECURITY COLLECTED — actual amount if paid, ₹0 if not yet collected
                security_status_rows.append(f'''
                <div class="total-row discount-row">
                    <span class="total-label">SECURITY COLLECTED</span>
                    <span class="total-value">{TailorOrderPDFService.format_currency(collected_security)}</span>
                </div>
                ''')
                # SECURITY REFUNDED — total across all refund entries (₹0 if none yet)
                security_status_rows.append(f'''
                <div class="total-row discount-row">
                    <span class="total-label">SECURITY REFUNDED</span>
                    <span class="total-value">{TailorOrderPDFService.format_currency(security_refund)}</span>
                </div>
                ''')

            payments_html = f'''
            <div class="total-row subtotal-row status-header">
                <span class="total-value"></span>
            </div>
            <div class="total-row paid-amount-row">
                <span class="total-label">PAID AMOUNT</span>
                <span class="total-value">{TailorOrderPDFService.format_currency(paid_with_security)}</span>
            </div>
            <div class="total-row balance-due-row">
                <span class="total-label">BALANCE DUE</span>
                <span class="total-value">{TailorOrderPDFService.format_currency(balance)}</span>
            </div>
            {('<div class="total-row subtotal-row status-header"><span class="total-value"></span></div>' + ''.join(security_status_rows)) if security_status_rows else ''}
            '''

        # ── Work info ────────────────────────────────────────────────────────
        order_type  = TailorOrderPDFService.safe_get(tailor_order, 'customer_order_type', '')
        pickup_date = TailorOrderPDFService.safe_get(tailor_order, 'pickup_date')
        return_date = TailorOrderPDFService.safe_get(tailor_order, 'return_date')

        work_info_items = []
        if pickup_date:
            work_info_items.append(f"Pickup: {TailorOrderPDFService.format_date(pickup_date)}")
        if order_type == 'rental' and return_date:
            work_info_items.append(f"Return: {TailorOrderPDFService.format_date(return_date)}")

        work_info_html = ''.join(
            f'<div class="order-info-item">{info}</div>' for info in work_info_items
        )

        # ── Payment + refund history (merged, chronological) ─────────────────
        # NOTE: Temporarily disabled — payment history not shown in PDF currently.
        # tailor_payment_history_rows = []
        # try:
        #     from tailored.models import CustomerPayment, TailorOrderRefund
        #     order_id = TailorOrderPDFService.safe_get(tailor_order, 'id')
        #     if order_id:
        #         combined = []
        #         for p in CustomerPayment.objects.filter(order_id=order_id):
        #             combined.append({
        #                 'amount':    TailorOrderPDFService.format_currency(p.payment_amount),
        #                 'method':    (p.payment_method or '').upper(),
        #                 'date':      TailorOrderPDFService.format_datetime(p.created_at),
        #                 'sort_key':  p.created_at,
        #                 'is_refund': False,
        #             })
        #         for r in TailorOrderRefund.objects.filter(order_id=order_id):
        #             combined.append({
        #                 'amount':    TailorOrderPDFService.format_currency(r.refunded_amount),
        #                 'method':    (r.refund_method or '').upper(),
        #                 'date':      TailorOrderPDFService.format_datetime(r.created_at),
        #                 'sort_key':  r.created_at,
        #                 'is_refund': True,
        #             })
        #         tailor_payment_history_rows = sorted(combined, key=lambda x: x['sort_key'])
        # except Exception as e:
        #     logger.warning(f"Failed to fetch payment history for tailor order PDF: {e}")
        # tailor_payment_history_html = _build_payment_history_html(tailor_payment_history_rows)
        tailor_payment_history_html = ''

        # ── Terms ────────────────────────────────────────────────────────────
        default_terms = DEFAULT_TAILOR_ORDER_TERMS
        terms = (
            shop_terms
            if (shop_terms and isinstance(shop_terms, list) and len(shop_terms) > 0)
            else default_terms
        )
        terms_html = ''.join(f'<div>- {term}</div>' for term in terms)

        # ── Measurements page ────────────────────────────────────────────────
        try:
            meas_sections = []
            for item in items:
                measurements = getattr(item, 'measurements', None)
                if not measurements:
                    continue
                rows = []
                if isinstance(measurements, dict):
                    for k, v in measurements.items():
                        if k is not None and v not in (None, ''):
                            rows.append({'name': str(k), 'value': str(v)})
                elif isinstance(measurements, list):
                    for m in measurements:
                        if isinstance(m, dict):
                            name_m = m.get('name')
                            value_m = m.get('value')
                            if name_m and value_m not in (None, ''):
                                rows.append({'name': str(name_m), 'value': str(value_m)})
                if not rows:
                    continue
                product_name_m = TailorOrderPDFService.safe_get(item, 'product_name', 'Unknown Product')
                product_type_m = TailorOrderPDFService.safe_get(item, 'product_type', '')
                heading = product_name_m
                if product_type_m:
                    heading += f" - {product_type_m}"
                meas_sections.append((heading, rows))

            _meas_tailor_pickup_str = TailorOrderPDFService.format_date(pickup_date) if pickup_date else ''
            _t_left_parts = []
            if invoice_no:
                _t_left_parts.append(f'<span>Invoice: <strong>{invoice_no}</strong></span>')
            if _meas_tailor_pickup_str:
                _t_left_parts.append(f'<span>Pickup: {_meas_tailor_pickup_str}</span>')
            _t_right_parts = []
            if client_name:
                _t_right_parts.append(f'<strong style="font-size:12pt;">{client_name}</strong>')
            if client_phone:
                _t_right_parts.append(f'<span>{client_phone}</span>')
            _t_left_html = '<br/>'.join(_t_left_parts)
            _t_right_html = '<br/>'.join(_t_right_parts)
            meas_client_info = (
                f'<table style="width:100%;border:none;background:transparent;">'
                f'<tr style="background:transparent;">'
                f'<td style="width:50%;border:none;vertical-align:middle;text-align:left;padding:0;">{_t_left_html}</td>'
                f'<td style="width:50%;border:none;vertical-align:middle;text-align:right;padding:0;">{_t_right_html}</td>'
                f'</tr></table>'
            ) if (_t_left_html or _t_right_html) else ''            
            # measurements_page_html = BasePDFService._build_measurements_page_html(
            #     meas_sections, meas_client_info
            # )
            measurements_page_html = BasePDFService._build_measurements_page_html(
                meas_sections, meas_client_info, staff_name=staff_name
            )
        except Exception as e:
            logger.error(f"Failed building tailor order measurements page: {e}", exc_info=True)
            measurements_page_html = ''

        # ── CSS backgrounds ──────────────────────────────────────────────────
        extra_css = ""
        if first_page_bg:
            extra_css += f"""
        @page :first {{
            background: url('{first_page_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """
        if later_pages_bg:
            extra_css += f"""
        @page {{
            background: url('{later_pages_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """

        # ── Assemble HTML ────────────────────────────────────────────────────
        main_content_inner_html = f"""
                    <div class="header">
                        {f'<img src="{shop_logo}" class="logo" />' if shop_logo else '<div class="empty-logo">No Logo</div>'}
                        <div class="shop-info">
                            <div class="shop-name">{shop_name}</div>
                            <div class="shop-details">{shop_details_html}</div>
                        </div>
                    </div>

                    <div class="invoice-section">
                        <div class="invoice-info">
                            <div class="invoice-row">
                                <span class="invoice-label">INVOICE ID:</span>
                                <span class="invoice-value">{invoice_no}</span>
                            </div>
                            <div class="invoice-row">
                                <span class="invoice-label">DATE:</span>
                                <span class="invoice-value">{invoice_date_str}</span>
                            </div>
                            {staff_row_html}
                        </div>
                        <div class="bill-to">
                            <div class="section-title">BILL TO</div>
                            <div class="bill-to-content">
                                <div class="bill-to-name" style="font-family: {client_name_font}; font-weight: {client_name_weight};">{client_name}</div>
                                <div class="bill-to-phone">{client_phone}</div>
                            </div>
                        </div>
                    </div>

                    <table>
                        <thead>
                            <tr>
                                <th style="width: 40px;" class="text-center">NO</th>
                                <th>ITEM</th>
                                <th style="width: 60px;" class="text-center">QTY</th>
                                <th style="width: 100px;" class="text-right">AMOUNT</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>

                    <div class="totals-section">
                        <div class="order-info">
                            <div class="order-info-title">WORK <span class="highlight">INFO:</span></div>
                            {work_info_html}
                            {tailor_payment_history_html}
                        </div>
                        <div class="totals">
                            {product_total_html}
                            {additional_total_html}
                            {discount_html}
                            {security_html}
                            {tax_html}
                            <div class="total-row grand-total-row">
                                <span class="total-label">TOTAL PAYABLE</span>
                                <span class="total-value">{TailorOrderPDFService.format_currency(grand_total)}</span>
                            </div>
                            {inclusive_tax_note_html}
                            {payments_html}
                        </div>
                    </div>
        """

        footer_content_inner_html = f"""
                    <div class="terms">
                        <div class="terms-title">
                            TERMS AND <span class="highlight">CONDITIONS</span>
                        </div>
                        <div class="terms-list">
                            {terms_html}
                        </div>
                    </div>
        """

        measure_css = BasePDFService.get_font_css() + "\n" + BasePDFService.get_base_css(first_page=True, multi_page=True)
        footer_page_break_style, terms_spacer_html = BasePDFService._resolve_terms_placement(
            main_content_inner_html, footer_content_inner_html, measure_css
        )

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body>
            <div class="content">
                <div class="main-content">
                    {main_content_inner_html}
                </div>

                {terms_spacer_html}
                <div class="footer-content" style="{footer_page_break_style}">
                    {footer_content_inner_html}
                </div>
                {measurements_page_html}
            </div>
        </body>
        </html>
        """

        try:
            pdf_bytes = TailorOrderPDFService.generate_pdf(html, first_page=True, extra_css=extra_css, multi_page=True)
            return base64.b64encode(pdf_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to generate tailor order PDF: {e}", exc_info=True)
            raise


# ============================================================================
# SALES PDF SERVICE
# ============================================================================
class SalesPDFService(BasePDFService):
    """PDF service for sales documents."""
    
    @staticmethod
    def validate_data(shop, client, sale, items):
        """Validate required fields matching old validation."""
        errors = []
        
        if not shop:
            errors.append("Shop object is required")
        elif not SalesPDFService.safe_get(shop, 'name'):
            errors.append("Shop name is required")
        
        if not sale:
            errors.append("Sale object is required")
        
        if not items or len(items) == 0:
            errors.append("At least one sale item is required")
        
        if errors:
            error_msg = "; ".join(errors)
            logger.error(f"Sale data validation failed: {error_msg}")
            raise ValueError(f"Invalid sale data: {error_msg}")
    
    @staticmethod
    def get_service_category(item):
        """Get main service category for an item."""
        try:
            product_variant = SalesPDFService.safe_get(item, 'product_variant')
            if product_variant:
                product = SalesPDFService.safe_get(product_variant, 'product')
                if product:
                    shop_service = SalesPDFService.safe_get(product, 'shop_service')
                    if shop_service:
                        general_service = SalesPDFService.safe_get(shop_service, 'general_service')
                        if general_service:
                            main_category = SalesPDFService.safe_get(general_service, 'main_category')
                            if main_category:
                                return SalesPDFService.safe_get(main_category, 'name', '').lower()
        except Exception as e:
            logger.warning(f"Failed to get main service category for sale item: {e}")
        return None
    
    @staticmethod
    def build_item_details(item):
        """Build item details based on category."""
        # Get from product variant if available
        product_variant = SalesPDFService.safe_get(item, 'product_variant')
        product = None
        if product_variant:
            product = SalesPDFService.safe_get(product_variant, 'product')
        
        product_name = ""
        product_category = ""
        product_model = ""
        product_color = ""
        variant_attribute = ""
        
        if product:
            product_name = SalesPDFService.safe_get(product, 'name', 'Unknown Product')
            product_category = SalesPDFService.safe_get(product, 'category', '')
            product_model = SalesPDFService.safe_get(product, 'model', '')
            product_color = SalesPDFService.safe_get(product, 'color', '')
        
        if product_variant:
            variant_attribute = SalesPDFService.safe_get(product_variant, 'attribute', '')
        
        main_category = SalesPDFService.get_service_category(item)
        
        details = []
        
        if main_category and ('dress' in main_category or 'costume' in main_category):
            if variant_attribute:
                details.append(f"Size: {variant_attribute}")
            if product_color:
                details.append(f"Color: {product_color}")
        elif main_category and 'vehicle' in main_category:
            if product_model:
                details.append(f"Model: {product_model}")
            if product_category:
                details.append(f"Brand: {product_category}")
        elif main_category and 'equipment' in main_category:
            if product_model:
                details.append(f"Model: {product_model}")
            if product_category:
                details.append(f"Category: {product_category}")
        elif main_category and 'gadget' in main_category:
            if product_model:
                details.append(f"Model: {product_model}")
            if product_category:
                details.append(f"Serial No: {product_category}")
        else:
            if product_color:
                details.append(f"Color: {product_color}")
            if product_category:
                details.append(f"Category: {product_category}")
        
        # Fallback to direct item attributes
        if not product_name:
            product_name = SalesPDFService.safe_get(item, 'product_name', 'Unknown Product')
        if not product_color:
            product_color = SalesPDFService.safe_get(item, 'color', '')
        if not product_category:
            product_category = SalesPDFService.safe_get(item, 'category', '')
        if not product_model:
            product_model = SalesPDFService.safe_get(item, 'model', '')
        if not variant_attribute:
            variant_attribute = SalesPDFService.safe_get(item, 'variant_attribute', '')
        
        # Add any remaining details
        if not details:
            if variant_attribute:
                details.append(f"Size: {variant_attribute}")
            if product_color:
                details.append(f"Color: {product_color}")
            if product_category:
                details.append(f"Category: {product_category}")
            if product_model:
                details.append(f"Model: {product_model}")
        
        return product_name, details
    
    @staticmethod
    def generate_sale_pdf(shop, client, sale, items, invoice_prefix="SAL"):
        """Generate sale PDF matching old ReportLab design."""
        # Customized invoice template (invoice_templates app) when switched on
        # for this shop; None -> the existing implementation below runs as-is.
        from invoice_templates.v3.dispatcher import try_render_customized
        customized = try_render_customized("sale", shop=shop, client=client, obj=sale, items=items)
        if customized is not None:
            return customized

        SalesPDFService.validate_data(shop, client, sale, items)
        
        # Get SVG backgrounds
        first_page_bg = get_svg_background("images/invoice_pdf_layout.svg")
        later_pages_bg = get_svg_background("images/other_pages.svg")
        
        # Prepare shop data matching old format
        # shop_name = SalesPDFService.safe_get(shop, 'name', '')
        shop_name = BookingPDFService.split_text_by_words(BookingPDFService.safe_get(shop, 'name', ''))
        shop_address = SalesPDFService.safe_get(shop, 'address', '')
        shop_city = SalesPDFService.safe_get(shop, 'city', '')
        # shop_state = SalesPDFService.safe_get(shop, 'state', '')
        shop_pincode = SalesPDFService.safe_get(shop, 'pincode', '')
        shop_phone = SalesPDFService.safe_get(shop, 'phone', '')
        shop_phone2 = SalesPDFService.safe_get(shop, 'phone2', '')
        shop_email = SalesPDFService.safe_get(shop, 'email', '')
        shop_logo = SalesPDFService.download_image(SalesPDFService.safe_get(shop, 'img'))
        shop_terms = SalesPDFService.safe_get(shop, 'terms_and_conditions', [])
        
        # Build location text like old version
        # location_parts = [p for p in [shop_city, shop_state] if p]
        location_parts = [p for p in [shop_city] if p]
        location_text = ", ".join(location_parts)
        # if shop_pincode:
        #     location_text = f"{location_text} - {shop_pincode}" if location_text else shop_pincode
        
        # Prepare invoice data
        invoice_no = SalesPDFService.safe_get(sale, 'shop_sale_id', 'N/A')
        invoice_date = SalesPDFService.safe_get(sale, 'created_at')
        invoice_date_str = SalesPDFService.format_datetime(invoice_date)
        staff_name = SalesPDFService.get_staff_name(sale)
        staff_row_html = SalesPDFService.build_staff_row_html(staff_name)
        sale_date = SalesPDFService.safe_get(sale, 'sale_date')
        sale_date_str = SalesPDFService.format_date(sale_date)

        # Prepare client data - use phone number from sale model
        client_phone = SalesPDFService.safe_get(sale, 'client_phone_e164', '') or \
                        SalesPDFService.safe_get(sale, 'client_phone', '')         
        client_address = SalesPDFService.safe_get(sale, 'address') or \
                        (SalesPDFService.safe_get(client, 'address', '') if client else '')
        
        # Get font families for client data
        client_phone_font = SalesPDFService.get_font_family(client_phone, True)
        client_phone_weight = SalesPDFService.get_font_weight(client_phone, True)
        client_address_font = SalesPDFService.get_font_family(client_address)
        client_address_weight = SalesPDFService.get_font_weight(client_address)
        
        # Collect all image URLs for parallel download (performance optimization)
        image_urls = []
        for item in items:
            try:
                if hasattr(item, 'product_variant') and item.product_variant:
                    product = item.product_variant.product
                    if product:
                        img_url = product.thumbnail_image or product.image
                        if img_url:
                            image_urls.append(img_url)
            except AttributeError:
                pass
        
        # Download all images in parallel - significant performance boost for multi-item PDFs
        logger.info(f"Downloading {len(image_urls)} images in parallel for sale PDF")
        image_results = SalesPDFService.download_images_parallel(image_urls) if image_urls else {}
        
        # Prepare items with pre-downloaded images
        items_data = []
        for idx, item in enumerate(items, 1):
            name, details = SalesPDFService.build_item_details(item)
            
            # Get pre-downloaded image from parallel results
            img_url = None
            try:
                if hasattr(item, 'product_variant') and item.product_variant:
                    product = item.product_variant.product
                    if product:
                        img_url = product.thumbnail_image or product.image
            except AttributeError as e:
                logger.warning(f"Error accessing product fields for sale item {idx}: {e}")
            
            item_image = image_results.get(img_url) if img_url else None
            
            if img_url and not item_image:
                logger.warning(f"Failed to load image for sale item {idx}: {name} from {img_url}")
            elif not img_url:
                logger.debug(f"No image URL found for sale item {idx}: {name}")
            
            price = SalesPDFService.safe_get(item, 'price', 0)
            quantity = SalesPDFService.safe_get(item, 'quantity', 0)
            subtotal = SalesPDFService.safe_get(item, 'subtotal', 0)
            
            # Get font for item name
            item_name_font = SalesPDFService.get_font_family(name, True)
            item_name_weight = SalesPDFService.get_font_weight(name, True)
            
            items_data.append({
                'no': idx,
                'image': item_image,
                'name': name,
                'name_font': item_name_font,
                'name_weight': item_name_weight,
                'details': details,
                'price': SalesPDFService.format_currency(price),
                'quantity': quantity,
                'subtotal': SalesPDFService.format_currency(subtotal)
            })
        
        # Prepare totals matching old calculation logic
        try:
            total = Decimal(str(SalesPDFService.safe_get(sale, 'total_amount', 0)))
            # Sales model uses 'discount' field, not 'discount_amount'
            discount = Decimal(str(SalesPDFService.safe_get(sale, 'discount', 0)))
            # Sales model uses 'paid_amount' field
            paid = Decimal(str(SalesPDFService.safe_get(sale, 'paid_amount', 0)))
            is_tax_applied = bool(SalesPDFService.safe_get(sale, 'is_tax_applied', False))
            tax_name = SalesPDFService.safe_get(sale, 'tax_name', '') or 'GST'
            tax_rate = Decimal(str(SalesPDFService.safe_get(sale, 'tax_rate', 0)))
            tax_amount = Decimal(str(SalesPDFService.safe_get(sale, 'tax_amount', 0)))
            tax_calculation_type = SalesPDFService.safe_get(sale, 'tax_calculation_type', '') or ''
            # Inclusive tax: total_amount is the raw, tax-embedded figure
            # (tax was never added on top of it) - tax_amount is only an
            # extracted breakdown of a portion of that same total, not an
            # addend. Exclusive tax: total_amount already has tax_amount
            # added on top.
            is_inclusive_tax = is_tax_applied and tax_calculation_type == 'inclusive'
        except (ValueError, InvalidOperation) as e:
            logger.error(f"Failed to parse decimal values: {e}")
            raise ValueError(f"Invalid decimal values in sale: {e}")
        
        # Calculate additional charges
        additional_total = Decimal(0)
        additional_charges = []
        charges = SalesPDFService.safe_get(sale, 'additional_charges')
        if charges:
            try:
                for charge in charges.all():
                    amount = Decimal(str(SalesPDFService.safe_get(charge, 'amount', 0)))
                    additional_total += amount
                    additional_charges.append({
                        'name': SalesPDFService.safe_get(charge, 'name', 'Additional Charge'),
                        'amount': SalesPDFService.format_currency(amount)
                    })
            except Exception as e:
                logger.warning(f"Failed to calculate additional charges: {e}")
        
        grand_total = total - discount
        # Only subtract tax_amount for Exclusive tax (where it was genuinely
        # added on top of total). For Inclusive tax, total is already the
        # raw, tax-embedded figure, so subtracting tax_amount here would
        # under-report the item subtotal by the tax portion.
        sub_total = total - additional_total - (Decimal(0) if is_inclusive_tax else tax_amount)
        balance = grand_total - paid
        
        # Get payment method
        payment_method = "Cash"
        try:
            payments = SalesPDFService.safe_get(sale, 'payments')
            if payments and hasattr(payments, 'first') and payments.first():
                payment = payments.first()
                payment_method = SalesPDFService.safe_get(payment, 'get_payment_method_display', lambda: 'Cash')()
        except Exception as e:
            logger.warning(f"Failed to get payment method: {e}")
        
        # Default terms for sales matching old version
        default_terms = DEFAULT_SALE_TERMS
        
        terms = shop_terms if (shop_terms and isinstance(shop_terms, list) and len(shop_terms) > 0) else default_terms
        
        # Build HTML with old styling
        items_html = ''
        for item in items_data:
            image_html = '<div class="empty-image">No Image</div>'
            if item['image']:
                # Ensure image is properly formatted
                try:
                    # Clean up the data URL if needed
                    image_url = item['image'].strip()
                    if image_url.startswith('data:image'):
                        image_html = f'<img src="{image_url}" class="item-image" alt="{item["name"]}" />'
                        logger.info(f"Image loaded successfully for sale item {item['no']}")
                    else:
                        logger.warning(f"Invalid image format for sale item {item['no']}: {image_url[:50]}...")
                        image_html = '<div class="empty-image">Invalid Image</div>'
                except Exception as e:
                    logger.warning(f"Error formatting image HTML for sale item {item['no']}: {e}")
                    image_html = '<div class="empty-image">Error</div>'
            
            # Build details HTML
            details_html = ''
            if item['details']:
                details_html = ''.join([f'<div class="item-meta">{detail}</div>' for detail in item['details']])
            
            items_html += f'''
        <tr>
            <td class="text-center">{item['no']}</td>
            <td>
                <div class="item-row">
                    {image_html}
                    <div class="item-details">
                        <div class="item-name" style="font-family: {item['name_font']}; font-weight: {item['name_weight']};">{item['name']}</div>
                        {details_html}
                    </div>
                </div>
            </td>
            <td class="text-right">{item['price']}</td>
            <td class="text-center">{item['quantity']}</td>
            <td class="amount-cell">{item['subtotal']}</td>
        </tr>
        '''
        
        additional_html = ''.join([f'''
        <div class="total-row discount-row">
            <span class="total-label">{charge["name"].upper()}</span>
            <span class="total-value">{charge["amount"]}</span>
        </div>
        ''' for charge in additional_charges])
        
        discount_html = f'''
        <div class="total-row discount-row">
            <span class="total-label">DISCOUNT</span>
            <span class="total-value">{SalesPDFService.format_currency(discount)}</span>
        </div>
        ''' if discount > 0 else ''

        # GST breakdown row: only shown for Exclusive tax, where it's a
        # genuine additive line. For Inclusive tax it's already embedded in
        # the sub total above, so showing it again here would look like a
        # duplicate/second tax charge - an inclusive_tax_note_html is shown
        # near GRAND TOTAL instead.
        tax_html = SalesPDFService.build_tax_rows_html(
            tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax,
            SalesPDFService.format_currency,
        )

        inclusive_tax_note_html = '''
        <div style="font-size:7.5pt;color:#6B7280;text-align:right;margin-top:2px;">(Inclusive of all taxes.)</div>
        ''' if is_inclusive_tax else ''

        balance_html = f'''
        <div class="total-row balance-due-row">
            <span class="total-label">BALANCE DUE</span>
            <span class="total-value">{SalesPDFService.format_currency(balance)}</span>
        </div>
        ''' if balance > 0 else ''

        # Build terms HTML
        terms_html = ''
        if terms:
            terms_html = ''.join([f'<div>- {term}</div>' for term in terms])

        shop_details_html = SalesPDFService.build_shop_details_html(shop)

        # Build CSS with background images in @page rules
        extra_css = ""
        if first_page_bg:
            extra_css += f"""
        @page :first {{
            background: url('{first_page_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """
        if later_pages_bg:
            extra_css += f"""
        @page {{
            background: url('{later_pages_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """

        # Terms & Conditions placement is decided by actually rendering the
        # main content and the terms block and measuring where they land —
        # see BasePDFService._resolve_terms_placement. Layout is always
        # block mode (matching Booking/Tailor Order).
        main_content_inner_html = f"""
                    <div class="header">
                        {f'<img src="{shop_logo}" class="logo" />' if shop_logo else '<div class="empty-logo">No Logo</div>'}
                        <div class="shop-info">
                            <div class="shop-name">{shop_name}</div>
                            <div class="shop-details">
                                {shop_details_html}
                            </div>
                        </div>
                    </div>

                    <div class="invoice-section">
                        <div class="invoice-info">
                            <div class="invoice-row">
                                <span class="invoice-label">INVOICE ID:</span>
                                <span class="invoice-value">{invoice_no}</span>
                            </div>
                            <div class="invoice-row">
                                <span class="invoice-label">DATE:</span>
                                <span class="invoice-value">{invoice_date_str}</span>
                            </div>
                            {staff_row_html}
                        </div>
                        <div class="bill-to">
                            <div class="section-title">BILL TO</div>
                            <div class="bill-to-content">
                                <div class="bill-to-phone" style="font-family: {client_phone_font}; font-weight: {client_phone_weight};">{client_phone}</div>
                                <div class="bill-to-address" style="font-family: {client_address_font}; font-weight: {client_address_weight};">{client_address}</div>
                            </div>
                        </div>
                    </div>

                    <table>
                        <thead>
                            <tr>
                                <th style="width: 40px;" class="text-center">NO</th>
                                <th>ITEM</th>
                                <th style="width: 80px;" class="text-right">PRICE</th>
                                <th style="width: 60px;" class="text-center">QTY</th>
                                <th style="width: 100px;" class="text-right">TOTAL</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>

                    <div class="totals-section">
                        <div class="order-info">
                            <div class="order-info-title">SALE <span class="highlight">INFO:</span></div>
                            <div class="order-info-item">Date: {sale_date_str}</div>
                            <div class="order-info-item">Payment Method: {payment_method}</div>
                        </div>
                        <div class="totals">
                            <div class="total-row subtotal-row">
                                <span class="total-label">SUB TOTAL</span>
                                <span class="total-value">{SalesPDFService.format_currency(sub_total)}</span>
                            </div>
                            {additional_html}
                            {discount_html}
                            {tax_html}
                            <div class="total-row grand-total-row">
                                <span class="total-label">GRAND TOTAL</span>
                                <span class="total-value">{SalesPDFService.format_currency(grand_total)}</span>
                            </div>
                            {inclusive_tax_note_html}
                            <div class="total-row paid-amount-row">
                                <span class="total-label">PAID AMOUNT</span>
                                <span class="total-value">{SalesPDFService.format_currency(paid)}</span>
                            </div>
                            {balance_html}
                        </div>
                    </div>
        """

        footer_content_inner_html = f"""
                    <div class="terms">
                        <div class="terms-title">
                            TERMS AND <span class="highlight">CONDITIONS</span>
                        </div>
                        <div class="terms-list">
                            {terms_html}
                        </div>
                    </div>
        """

        measure_css = SalesPDFService.get_font_css() + "\n" + SalesPDFService.get_base_css(first_page=True, multi_page=True)
        footer_page_break_style, terms_spacer_html = BasePDFService._resolve_terms_placement(
            main_content_inner_html, footer_content_inner_html, measure_css
        )

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body>
            <div class="content">
                <div class="main-content">
                    {main_content_inner_html}
                </div>

                {terms_spacer_html}
                <div class="footer-content" style="{footer_page_break_style}">
                    {footer_content_inner_html}
                </div>
            </div>
        </body>
        </html>
        """

        try:
            pdf_bytes = SalesPDFService.generate_pdf(html, first_page=True, extra_css=extra_css, multi_page=True)
            return base64.b64encode(pdf_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to generate PDF: {e}", exc_info=True)
            raise
