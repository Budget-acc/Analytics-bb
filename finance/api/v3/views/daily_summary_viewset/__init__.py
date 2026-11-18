from finance.api.v3.views.daily_summary_viewset.base import DailySummaryViewSetBase
from finance.api.v3.views.daily_summary_viewset.retrieve import DailySummaryRetrieveMixin


class DailySummaryViewSet(
    DailySummaryRetrieveMixin,
    DailySummaryViewSetBase,
):
    """
    DailySummary ViewSet — finance/api/v3/

    Endpoints:
        GET    /finance/v3/daily-summary/    list    — all roles

    Query params:
        date (str, optional) — ISO date YYYY-MM-DD, defaults to today.
                               Must not be in the future.

    Response:
        Aggregated shop-wide opening_balance, closing_balance, money_in, money_out
        for the requested date, plus a per-account breakdown.

    Notes:
        - Data sourced from DailyBalance (pre-computed per ledger write).
        - When no transactions exist for the date, opening balance is carried
          forward from the most recent prior DailyBalance per account.
        - Soft-deleted and inactive accounts are excluded.
        - All queries scoped to shop via self.shop_id resolved in initial().
    """
    pass
