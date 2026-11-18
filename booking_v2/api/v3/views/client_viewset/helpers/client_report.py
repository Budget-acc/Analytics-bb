from booking_v2.models import Client


def client_details_queryset(shop_id, start=None, end=None):
    """
    Clients for the Client Details reports (PDF and Excel): the shop's
    non-deleted clients, newest first (-created_at, then -id for a stable
    order), loading only the display fields.

    Optional created_at range: start (inclusive) to end (exclusive), as
    tz-aware datetimes - e.g. from trending_insights get_date_range().
    """
    qs = (
        Client.objects.filter(shop_id=shop_id, deleted__isnull=True)
        .order_by('-created_at', '-id')
        .only('id', 'client_name', 'phone_1_e164', 'phone_2_e164', 'created_at')
    )
    if start is not None and end is not None:
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    return qs
