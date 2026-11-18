"""
Indian number formatting for generated documents (PDFs and Excel reports).

The Indian system groups the last 3 integer digits, then every 2:
    303900      -> 3,03,900
    1234567.89  -> 12,34,567.89
    10000000    -> 1,00,00,000

Only presentation changes: rounding is exactly Python's f"{value:.Nf}" (the
same the reports used before), so displayed values never differ - only the
comma positions do.
"""
from decimal import Decimal, InvalidOperation

# xlsxwriter `num_format` for Indian grouping. Excel has no native lakh
# grouping, so this is the standard conditional format; cells stay numeric
# (SUM/filters work). Excel allows only two conditions, so negative values of
# a lakh or more fall back to Western grouping in Excel.
INDIAN_EXCEL_NUM_FORMAT = r'[>=10000000]##\,##\,##\,##0.00;[>=100000]##\,##\,##0.00;##,##0.00'


def _group_indian(int_digits: str) -> str:
    if len(int_digits) <= 3:
        return int_digits
    head, tail = int_digits[:-3], int_digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def format_indian_number(value, decimals: int = 2) -> str:
    """'12,34,567.89' for 1234567.89. Raises on non-numeric input (callers keep
    their own None / error fallbacks)."""
    if isinstance(value, (int, float)):
        # Decimal(float) keeps the exact binary value, so rounding matches
        # f"{float:.2f}" (Decimal(str(2.675)) would give 2.68, not 2.67).
        value = Decimal(value)
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    text = f"{value:.{decimals}f}"
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    int_part, _, frac_part = text.partition(".")
    grouped = _group_indian(int_part)
    return f"{sign}{grouped}.{frac_part}" if frac_part else f"{sign}{grouped}"


_RAISE = object()


def format_inr(value, decimals: int = 2, symbol: str = "₹", fallback=_RAISE) -> str:
    """'₹3,03,900' / '₹12,34,567.89'. Negatives keep the reports' existing
    form, symbol first: '₹-1,500.00'. Non-numeric input raises, unless a
    `fallback` is given - then that is returned instead."""
    try:
        return f"{symbol}{format_indian_number(value, decimals)}"
    except (InvalidOperation, ValueError, TypeError):
        if fallback is _RAISE:
            raise
        return fallback


def format_inr_or_raw(value, decimals: int = 2, symbol: str = "₹") -> str:
    """format_inr, but a non-numeric value is shown as before (f"₹{value}")
    instead of raising - for plain-text reports that print raw values."""
    return format_inr(value, decimals, symbol, fallback=f"{symbol}{value}")
