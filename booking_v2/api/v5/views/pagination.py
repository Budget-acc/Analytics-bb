# booking_v2/api/v5/views/pagination.py
from rest_framework.pagination import PageNumberPagination
from collections import OrderedDict
from rest_framework.response import Response
from ERP.utils.response_utils import ResponseStatus, ResponseMessage


class DayGroupedPagination(PageNumberPagination):
    page_size = 6

    def paginate_day_grouped(self, grouped_data, request):
        """
        Custom pagination method for day-grouped data
        """
        self.request = request
        self.grouped_keys = list(grouped_data.keys())
        self.count = len(self.grouped_keys)

        page = self.paginate_queryset(self.grouped_keys, request)
        if page is not None:
            page_group = OrderedDict()
            for date in page:
                page_group[date] = grouped_data[date]
            return page_group
        return grouped_data

    def get_paginated_response(self, data):
        """
        Override to ensure proper field order and include status/message
        """
        paginated_data = OrderedDict(data)
        if hasattr(self, "total_pending_amount"):
            paginated_data = OrderedDict(
                [("total_pending_amount", self.total_pending_amount), *paginated_data.items()]
            )
        if hasattr(self, "total_pending_breakdown"):
            items = list(paginated_data.items())
            insert_at = next(
                (i + 1 for i, (k, _) in enumerate(items) if k == "total_pending_amount"),
                1,
            )
            items.insert(insert_at, ("total_pending_breakdown", self.total_pending_breakdown))
            paginated_data = OrderedDict(items)

        response_data = OrderedDict([
            ("status", ResponseStatus.SUCCESS.value),
            ("message", ResponseMessage.SUCCESS_RETRIEVED),
            ("data", {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "data": paginated_data,
            })
        ])
        return Response(response_data)
