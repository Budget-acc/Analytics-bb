from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.api.admin_v1.serializers import BugReportAdminListSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="List bug reports",
    description="Returns bug reports with optional text, status, reporter, shop, and date range filters.",
    parameters=[
        OpenApiParameter("q", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search title/description."),
        OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Bug status filter."),
        OpenApiParameter("reporter", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Reporter id/phone/name filter."),
        OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Shop id filter."),
        OpenApiParameter("start_date", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False, description="Start date YYYY-MM-DD."),
        OpenApiParameter("end_date", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False, description="End date YYYY-MM-DD."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated bug report list."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Invalid shop id.\n"
                "- Invalid start date format.\n"
                "- Invalid end date format.\n"
                "- Invalid shop_id\n"
                "- Invalid date format. Use YYYY-MM-DD"
            )
        ),
        500: OpenApiResponse(description="Exact message: Unable to fetch bug reports. Please try again later."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 7, "title": "Crash on save", "status": "OPEN"}]}, response_only=True, status_codes=["200"]),
    ],
)
def list(self, request):
    try:
        qs = self.get_queryset().order_by("-created_at")

        q = (request.query_params.get("q") or "").strip()
        status_param = (request.query_params.get("status") or "").strip()
        reporter = (request.query_params.get("reporter") or "").strip()
        shop_id = request.query_params.get("shop_id")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        filters = Q()
        if q:
            filters &= (Q(title__icontains=q) | Q(description__icontains=q))

        if status_param:
            filters &= Q(status__iexact=status_param)

        if reporter:
            if reporter.isdigit():
                filters &= (Q(reporter_id=int(reporter)) | Q(reporter__phone__icontains=reporter) | Q(reporter__full_name__icontains=reporter))
            else:
                filters &= (Q(reporter__phone__icontains=reporter) | Q(reporter__full_name__icontains=reporter))

        if shop_id:
            try:
                filters &= Q(shop_id=int(shop_id))
            except ValueError:
                return self.validation_error_response(
                    errors={"shop_id": ["Invalid shop_id"]},
                    message="Invalid shop id."
                )

        if start_date:
            try:
                filters &= Q(created_at__date__gte=start_date)
            except Exception:
                return self.validation_error_response(
                    errors={"start_date": ["Invalid date format. Use YYYY-MM-DD"]},
                    message="Invalid start date format."
                )
        if end_date:
            try:
                filters &= Q(created_at__date__lte=end_date)
            except Exception:
                return self.validation_error_response(
                    errors={"end_date": ["Invalid date format. Use YYYY-MM-DD"]},
                    message="Invalid end date format."
                )

        qs = qs.filter(filters)

        # Pagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is None:
            serializer = BugReportAdminListSerializer(qs, many=True)
            return Response({"data": serializer.data})

        serializer = BugReportAdminListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.exception("Failed to list bug reports.")
        return self.error_response(
            message="Unable to fetch bug reports. Please try again later.",
            dev_message=str(e)
        )
