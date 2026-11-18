from .service_revenue_analytics import get_service_revenue_analytics


def get_service_revenue(shop_id, start, end):
    """
    Dashboard Service Revenue - exactly the same calculation as the Service
    Revenue Analytics API (derived from `get_service_revenue_analytics`, so
    the two can never drift apart), without the product breakdown.

    Completed, non-cancelled Bookings (by `completed_at`) and Sales (by
    `sale_date`); each product's `BookedProductVariant.amount` /
    `SaleItem.subtotal`, less its price-ratio share of the transaction
    discount, plus Exclusive tax at the stored rate (Inclusive tax not added
    again), is attributed to its product's shop_service. Counts are distinct
    bookings / sales per service.
    Completed Tailor Orders appear as one "Custom Work" entry (service_id
    None, booking/sales values 0).

    Returns [{service_id, service_name, booking_count, sales_count,
    booking_revenue, sales_revenue, total_transaction_count, total_revenue}],
    sorted by total_revenue descending (ties by service_name) - the same
    order as the Analytics API.
    """
    services = get_service_revenue_analytics(shop_id, start, end)["services"]
    return [
        {
            "service_id": service["service_id"],
            "service_name": service["service_name"],
            "booking_count": service["booking_count"],
            "sales_count": service["sales_count"],
            "booking_revenue": service["booking_revenue"],
            "sales_revenue": service["sales_revenue"],
            "total_transaction_count": service["total_transaction_count"],
            "total_revenue": service["total_revenue"],
        }
        for service in services
    ]
