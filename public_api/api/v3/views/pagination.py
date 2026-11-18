from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from ERP.utils.response_utils import ResponseStatus, ResponseMessage


class ProductCursorPagination(CursorPagination):
    page_size = 15
    page_size_query_param = 'page_size'
    max_page_size = 100
    ordering = "id"

    def get_paginated_response(self, data):
        return Response({
            "status": ResponseStatus.SUCCESS.value,
            "message": ResponseMessage.SUCCESS_RETRIEVED,
            "data": {
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "page_size": self.get_page_size(self.request),
                "data": data
            }
        })
