import logging

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from pdf_service.models import PDFTask
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .throttles import PDFStatusCheckThrottle

logger = logging.getLogger("api_requests")


class PDFStatusView(StandardResponseMixin, APIView):
    """Unified PDF status endpoint for all PDF types."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [PDFStatusCheckThrottle]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]    

    @extend_schema(
        tags=["PDF Management V3"],
        summary="Check PDF task status",
        description=(
            "Returns the current state of a PDF generation task. "
            "Supports path-based task_id and query-param task_id lookup."
        ),
        parameters=[
            OpenApiParameter(
                name="task_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=False,
                description="Task id in URL path. Use this in /status/{task_id}/.",
            ),
            OpenApiParameter(
                name="task_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Task id as query parameter. Use this in /status/?task_id={id}.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Status payload with task metadata and state-specific fields."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- task_id is required.\n"
                    "- This field is required."
                )
            ),
            404: OpenApiResponse(description="Exact message: Task not found."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve PDF status."),
        },
        examples=[
            OpenApiExample(
                "Request example - path",
                description="GET /api/v3/pdf/status/4f95d2c2-5d35-438e-b74b-6f31eabce830/",
                value={"task_id": "4f95d2c2-5d35-438e-b74b-6f31eabce830"},
                request_only=True,
            ),
            OpenApiExample(
                "Request example - query",
                description="GET /api/v3/pdf/status/?task_id=4f95d2c2-5d35-438e-b74b-6f31eabce830",
                value={"task_id": "4f95d2c2-5d35-438e-b74b-6f31eabce830"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response - completed",
                value={
                    "status": "success",
                    "message": "Transaction Report generated successfully.",
                    "data": {
                        "task_id": "4f95d2c2-5d35-438e-b74b-6f31eabce830",
                        "pdf_type": "transaction_report",
                        "status": "completed",
                        "elapsed_seconds": 14,
                        "created_at": "2026-05-04T12:42:00+05:30",
                        "download_url": "https://example-bucket.s3.amazonaws.com/reports/txn.pdf",
                        "expires_at": "2026-05-05T12:42:14+05:30",
                        "url_expired": False,
                        "generation_time": "2.13s",
                        "file_size_kb": 428.21,
                        "record_count": 126,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Success response - in progress",
                value={
                    "status": "success",
                    "message": "Ledger Book generation in progress.",
                    "data": {
                        "task_id": "4f95d2c2-5d35-438e-b74b-6f31eabce830",
                        "pdf_type": "ledger_book",
                        "status": "processing",
                        "elapsed_seconds": 22,
                        "created_at": "2026-05-04T12:45:00+05:30",
                        "progress": "Processing data...",
                        "estimated_time": "5s",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Success response - failed state",
                value={
                    "status": "success",
                    "message": "Transaction Report generation failed.",
                    "data": {
                        "task_id": "4f95d2c2-5d35-438e-b74b-6f31eabce830",
                        "pdf_type": "transaction_report",
                        "status": "failed",
                        "elapsed_seconds": 8,
                        "created_at": "2026-05-04T12:45:00+05:30",
                        "error": "S3 upload failed",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing task_id response",
                value={
                    "status": "validation_error",
                    "message": "task_id is required.",
                    "data": {
                        "validation_errors": {
                            "task_id": ["This field is required."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Task not found response",
                value={
                    "status": "not_found",
                    "message": "Task not found.",
                    "data": None,
                },
                response_only=True,
                status_codes=["404"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to retrieve PDF status.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def get(self, request, task_id=None):
        """
        Check PDF generation status for any PDF type.

        Query params:
            - task_id: UUID of the PDF generation task
        """
        try:
            if not task_id:
                task_id = request.query_params.get('task_id')

            if not task_id:
                return self.validation_error_response(
                    errors={"task_id": ["This field is required."]},
                    message="task_id is required."
                )

            try:
                pdf_task = PDFTask.objects.get(task_id=task_id)
            except PDFTask.DoesNotExist:
                return self.not_found_response(
                    resource="PDFTask",
                    message="Task not found."
                )

            # Calculate elapsed time
            elapsed = (timezone.now() - pdf_task.created_at).total_seconds()

            # Base response
            response_data = {
                'task_id': task_id,
                'pdf_type': pdf_task.pdf_type,
                'status': pdf_task.status,
                'elapsed_seconds': int(elapsed),
                'created_at': pdf_task.created_at.isoformat(),
            }

            if pdf_task.status == 'completed':
                # Check if URL has expired
                url_expired = False
                if pdf_task.url_expires_at and pdf_task.url_expires_at < timezone.now():
                    url_expired = True
                    logger.warning(f"[PDF Status] URL expired for task {task_id}")

                response_data.update({
                    'download_url': pdf_task.download_url if not url_expired else None,
                    'expires_at': pdf_task.url_expires_at.isoformat() if pdf_task.url_expires_at else None,
                    'url_expired': url_expired,
                    'generation_time': f"{pdf_task.actual_generation_time:.2f}s" if pdf_task.actual_generation_time else None,
                    'file_size_kb': round(pdf_task.file_size_bytes / 1024, 2) if pdf_task.file_size_bytes else None,
                    'record_count': pdf_task.estimated_count,
                })

                if url_expired:
                    message = f"{pdf_task.get_pdf_type_display()} download link has expired. Please generate a new PDF."
                else:
                    message = f"{pdf_task.get_pdf_type_display()} generated successfully."

            elif pdf_task.status in ['pending', 'processing']:
                # Progress estimation
                if elapsed < 10:
                    progress_msg = 'Initializing...'
                elif elapsed < 30:
                    progress_msg = 'Processing data...'
                elif elapsed < 60:
                    progress_msg = 'Generating PDF...'
                else:
                    progress_msg = 'Taking longer than expected...'

                response_data['progress'] = progress_msg
                response_data['estimated_time'] = pdf_task.estimated_time

                message = f"{pdf_task.get_pdf_type_display()} generation in progress."

            elif pdf_task.status == 'failed':
                response_data['error'] = pdf_task.error_message
                message = f"{pdf_task.get_pdf_type_display()} generation failed."

            else:  # expired
                message = "PDF generation task has expired."

            return self.success_response(
                data=response_data,
                message=message
            )

        except Exception as e:
            logger.error(f"[PDF Status] Error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve PDF status.",
                dev_message=str(e)
            )
