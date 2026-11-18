from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomInvoiceViewSet, InvoicePreferenceViewSet, InvoiceTemplateViewSet, TemplateShareViewSet

router = DefaultRouter()
router.register(r"templates", InvoiceTemplateViewSet, basename="invoice-templates")
router.register(r"custom-invoices", CustomInvoiceViewSet, basename="custom-invoices")
router.register(r"preference", InvoicePreferenceViewSet, basename="invoice-preference")
router.register(r"share", TemplateShareViewSet, basename="invoice-template-share")

urlpatterns = [
    path("", include(router.urls)),
]
