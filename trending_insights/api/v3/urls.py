from django.urls import path

from trending_insights.api.v3.views import (
    ServiceRevenueAnalyticsViewSet,
    TrendingColorCategoryViewSet,
    TrendingInsightsViewSet,
    TrendingProductsViewSet,
    UnusedProductsExcelReportViewSet,
    UnusedProductsExcelStatusViewSet,
    UnusedProductsViewSet,
)

urlpatterns = [
    path("analytics/", TrendingInsightsViewSet.as_view(), name="trending-insights-analytics"),
    path("trending-products/", TrendingProductsViewSet.as_view(), name="trending-insights-trending-products"),
    path("trending-color-category/", TrendingColorCategoryViewSet.as_view(), name="trending-insights-trending-color-category"),
    path("service-revenue-analytics/", ServiceRevenueAnalyticsViewSet.as_view(), name="trending-insights-service-revenue-analytics"),
    path("unused-products/", UnusedProductsViewSet.as_view(), name="trending-insights-unused-products"),
    path(
        "unused-products/excel-report/",
        UnusedProductsExcelReportViewSet.as_view(),
        name="trending-insights-unused-products-excel-report",
    ),
    path(
        "unused-products/excel-status/<str:task_id>/",
        UnusedProductsExcelStatusViewSet.as_view(),
        name="trending-insights-unused-products-excel-status",
    ),
]
