from .base import TrendingInsightsViewSetBase
from .retrieve import TrendingInsightsRetrieveMixin
from .trending_products import TrendingProductsRetrieveMixin
from .trending_color_category import TrendingColorCategoryRetrieveMixin
from .service_revenue_analytics import ServiceRevenueAnalyticsRetrieveMixin
from .unused_products import UnusedProductsRetrieveMixin
from .unused_products_excel import UnusedProductsExcelReportMixin, UnusedProductsExcelStatusMixin


class TrendingInsightsViewSet(TrendingInsightsRetrieveMixin, TrendingInsightsViewSetBase):
    """
    Combined trending insights: top 5 trending products, monthly transaction
    velocity, service-based revenue and staff performance - across Booking
    and Sale data - for a selected month.

    See TrendingInsightsRetrieveMixin.get() (retrieve.py) for the endpoint's
    Swagger documentation and implementation.
    """


class TrendingProductsViewSet(TrendingProductsRetrieveMixin, TrendingInsightsViewSetBase):
    """
    Paginated "View More" trending products endpoint, reusing the same
    calculation function as the trending insights endpoint with a larger page size.

    See TrendingProductsRetrieveMixin.get() (trending_products.py) for the
    endpoint's Swagger documentation and implementation.
    """


class TrendingColorCategoryViewSet(TrendingColorCategoryRetrieveMixin, TrendingInsightsViewSetBase):
    """
    Top trending Color and top trending Category (each with a breakdown of
    the other attribute within it) across Booking and Sale data, for a
    selected month.

    See TrendingColorCategoryRetrieveMixin.get() (trending_color_category.py)
    for the endpoint's Swagger documentation and implementation.
    """


class ServiceRevenueAnalyticsViewSet(ServiceRevenueAnalyticsRetrieveMixin, TrendingInsightsViewSetBase):
    """
    Per-service revenue with a product-level breakdown (top 2 products by
    revenue + an "Others" bucket) across Booking and Sale data, for a
    selected month.

    See ServiceRevenueAnalyticsRetrieveMixin.get() (service_revenue_analytics.py)
    for the endpoint's Swagger documentation and implementation.
    """


class UnusedProductsViewSet(UnusedProductsRetrieveMixin, TrendingInsightsViewSetBase):
    """
    Unused (0 orders) and low-usage (1-5 orders) products with their order
    count and last ordered date, for a requested date range (max 180 days).

    See UnusedProductsRetrieveMixin.get() (unused_products.py) for the
    endpoint's Swagger documentation and implementation.
    """


class UnusedProductsExcelReportViewSet(UnusedProductsExcelReportMixin, TrendingInsightsViewSetBase):
    """
    Starts background generation of the unused/low-usage products Excel
    report (same calculation as UnusedProductsViewSet).

    See UnusedProductsExcelReportMixin.get() (unused_products_excel.py).
    """


class UnusedProductsExcelStatusViewSet(UnusedProductsExcelStatusMixin, TrendingInsightsViewSetBase):
    """
    Status / download link of an unused/low-usage products Excel report task.

    See UnusedProductsExcelStatusMixin.get() (unused_products_excel.py).
    """


__all__ = [
    "TrendingInsightsViewSet",
    "TrendingProductsViewSet",
    "TrendingColorCategoryViewSet",
    "ServiceRevenueAnalyticsViewSet",
    "UnusedProductsViewSet",
    "UnusedProductsExcelReportViewSet",
    "UnusedProductsExcelStatusViewSet",
]
