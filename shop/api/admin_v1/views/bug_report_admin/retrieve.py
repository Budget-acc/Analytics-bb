from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import BugReport
from shop.api.admin_v1.serializers import BugReportAdminDetailSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Retrieve bug report",
    description="Returns detailed bug report by id.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Bug report id.")],
    responses={
        200: OpenApiResponse(description="Bug report detail response."),
        404: OpenApiResponse(description="Exact message: Bug report {pk} not found."),
        500: OpenApiResponse(description="Exact message: Unable to retrieve the bug report. Please try again later."),
    },
    examples=[
        OpenApiExample("Success response", value={"id": 7, "title": "Crash on save", "description": "...", "status": "OPEN"}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found response", value={"status": "not_found", "message": "Bug report 7 not found.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)
def retrieve(self, request, pk=None):
    try:
        try:
            bug = self.get_queryset().get(pk=pk)
        except BugReport.DoesNotExist:
            return self.not_found_response(resource="BugReport", message=f"Bug report {pk} not found.")

        serializer = BugReportAdminDetailSerializer(bug)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Failed to retrieve bug report {pk}:{str(e)}")
        return self.error_response(
            message="Unable to retrieve the bug report. Please try again later.",
            dev_message=str(e)
        )
