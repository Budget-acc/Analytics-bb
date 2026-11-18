from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from .response_utils import ResponseStatus, ResponseMessage


class StandardPageNumberPagination(PageNumberPagination):
    page_size = 15
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            "status": ResponseStatus.SUCCESS.value,
            "message": ResponseMessage.SUCCESS_RETRIEVED,
            # pagination fields moved up one level
            "data": {
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "count": self.page.paginator.count,
                "page_size": self.get_page_size(self.request),
                "current_page": self.page.number,
                "total_pages": self.page.paginator.num_pages,
                "start_index": self.page.start_index(),
                "end_index": self.page.end_index(),
                "data": data
            }
        })
