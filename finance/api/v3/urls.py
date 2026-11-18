from django.urls import path, include
from rest_framework.routers import DefaultRouter

from finance.api.v3.views import (
    AccountViewSet,
    DailySummaryViewSet,
    FinancialDashboardViewSet,
    ManualIncomeViewSet,
    AccountTransferViewSet,
    InvestmentViewSet,
    WithdrawalViewSet,
    FinanceTransactionsViewSet,
    TaxReportViewSet,
)

router = DefaultRouter()
router.register(r"accounts", AccountViewSet, basename="account")
router.register(r"manual-incomes", ManualIncomeViewSet, basename="manual-income")
router.register(r"account-transfers", AccountTransferViewSet, basename="account-transfer")
router.register(r"investments", InvestmentViewSet, basename="investment")
router.register(r"withdrawals", WithdrawalViewSet, basename="withdrawal")
router.register(r"transactions", FinanceTransactionsViewSet, basename="finance-transaction")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "daily-summary/",
        DailySummaryViewSet.as_view({"get": "list"}),
        name="daily-summary-list",
    ),
    path("financial-dashboard/", FinancialDashboardViewSet.as_view(), name="financial-dashboard"),
    path("tax-report/", TaxReportViewSet.as_view(), name="tax-report"),
]
