from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from ERP.utils.response_utils import ResponseStatus, ResponseMessage


class DayGroupedPagination(PageNumberPagination):
    page_size = 6

    def __init__(self):
        super().__init__()
        self.day_groups = []
        self.total_days = 0
        self.count = 0
        self.page = 1
        self.num_pages = 0

    def paginate_day_groups(self, day_groups, request):
        """
        Paginate by day groups, ensuring no day is split across pages
        """
        self.request = request
        self.day_groups = list(day_groups.keys())
        self.total_days = len(self.day_groups)

        # Get page number from request
        page_number = request.query_params.get(self.page_query_param, 1)
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            page_number = 1

        # Calculate start and end indices for this page
        start_index = (page_number - 1) * self.page_size
        end_index = start_index + self.page_size

        # Get the day keys for this page
        page_day_keys = self.day_groups[start_index:end_index]

        if not page_day_keys:
            return None

        # Build the page data with complete day groups
        page_data = OrderedDict()
        for day_key in page_day_keys:
            page_data[day_key] = day_groups[day_key]

        # Store pagination info
        self.count = self.total_days
        self.page = page_number
        self.num_pages = (self.total_days + self.page_size - 1) // self.page_size

        return page_data

    def get_next_link(self):
        if self.page >= self.num_pages:
            return None
        next_page = self.page + 1
        return self.request.build_absolute_uri().split('?')[0] + f'?page={next_page}'

    def get_previous_link(self):
        if self.page <= 1:
            return None
        prev_page = self.page - 1
        return self.request.build_absolute_uri().split('?')[0] + f'?page={prev_page}'

    def get_paginated_response(self, data):
        """
        Return paginated response with day-grouped data
        """
        response_data = OrderedDict([
            ("status", ResponseStatus.SUCCESS.value),
            ("message", ResponseMessage.SUCCESS_RETRIEVED),
            ("data", {
                "count": self.count,
                "total_pages": self.num_pages,
                "current_page": self.page,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "data": data
            })
        ])
        return Response(response_data)
