from django.urls import path
from .views import (
    ShopExpenseGroupedAPIView,
    ShopTransactionHistoryAPIView,
    ExportTransactionsPDFView,
    ExportTransactionsExcelView,
)

urlpatterns = [
    path("wallet/expenses/", ShopExpenseGroupedAPIView.as_view(), name="shop-expense-list"),
    path('transaction-history/', ShopTransactionHistoryAPIView.as_view(), name='shop-transaction-history'),
    path('export-transactions/pdf/', ExportTransactionsPDFView.as_view(), name='export-transactions-pdf'),
    path('export-transactions/excel/', ExportTransactionsExcelView.as_view(), name='export-transactions-excel'),
]
