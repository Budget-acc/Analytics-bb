from .base import TaxReportViewSetBase
from .list import TaxReportListMixin


class TaxReportViewSet(TaxReportListMixin, TaxReportViewSetBase):
    """
    Consolidated tax report endpoint for finance app.
    Merges tax-applicable Bookings, Sales and Tailor (custom work) Orders
    into a single date-ordered list.
    """


__all__ = ["TaxReportViewSet"]
