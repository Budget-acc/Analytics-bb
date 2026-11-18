from django.urls import path

from .views import (
    PDFStatusView, ExportTransactionsPDFView, ExportLedgerPDFView,
    ExportAccountHistoryPDFView, ExportTaxReportPDFView, ExportClientDetailsPDFView,
    ExportBookingListPDFView,
)

urlpatterns = [
    path('status/<str:task_id>/', PDFStatusView.as_view(), name='pdf-status'),
    path('status/', PDFStatusView.as_view(), name='pdf-status-query'),
    path('transactions/export/pdf/', ExportTransactionsPDFView.as_view(), name='export-transactions-pdf'),
    path('ledger/export/pdf/', ExportLedgerPDFView.as_view(), name='export-ledger-pdf'),
    path('bookings/export/pdf/', ExportBookingListPDFView.as_view(), name='export-booking-list-pdf'),
    path('account/export/pdf/<int:account_id>/', ExportAccountHistoryPDFView.as_view(), name='export-account-pdf'),
    path('tax-report/export/pdf/', ExportTaxReportPDFView.as_view(), name='export-tax-report-pdf'),
    path('clients/export/pdf/', ExportClientDetailsPDFView.as_view(), name='export-client-details-pdf'),
]
