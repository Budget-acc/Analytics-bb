from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ExpensesViewSet,
    ProductVariantExpensesViewSet,
    ShopExpenseAPIView,
    DaywiseShopSummaryAPIView,
    ShopTransactionHistoryAPIView,
    ExportTransactionsExcelView,
    ExportTransactionsPDFView,
    ShopCategoryViewSet,
)

router = DefaultRouter()
router.register('expenses', ExpensesViewSet, basename='expenses')
router.register('variant-expenses', ProductVariantExpensesViewSet, basename='variant-expenses')
router.register('shop-categories', ShopCategoryViewSet, basename='shop-categories')

urlpatterns = [
    path('', include(router.urls)),
    path("wallet/expenses/", ShopExpenseAPIView.as_view(), name="shop-expense-list"),
    path('wallet/daywise-summary/', DaywiseShopSummaryAPIView.as_view(), name="day-wise-summary"),
    path('transaction-history/', ShopTransactionHistoryAPIView.as_view(), name='shop-transaction-history'),
    path('export-transactions/pdf/', ExportTransactionsPDFView.as_view(), name='export-transactions-pdf'),
    path('export-transactions/excel/', ExportTransactionsExcelView.as_view(), name='export-transactions-excel'),
]
