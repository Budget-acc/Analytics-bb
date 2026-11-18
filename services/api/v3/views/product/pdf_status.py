import logging
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.utils import timezone
from services.models import PDFGenerationTask

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Get PDF generation status",
    description="Returns status and output metadata for an async PDF generation task.",
    parameters=[OpenApiParameter("task_id", OpenApiTypes.STR, OpenApiParameter.PATH, required=True, description="PDF task id.")],
    responses={
        200: OpenApiResponse(description="Standard success response with pending/completed/failed task data."),
        400: OpenApiResponse(description="Exact message: task_id is required."),
        404: OpenApiResponse(description="Exact message: Task not found."),
        500: OpenApiResponse(description="Exact message: Unable to retrieve PDF status."),
    },
    examples=[
        OpenApiExample("Completed response", value={"status": "success", "message": "PDF status retrieved successfully.", "data": {"task_id": "abc", "status": "completed", "download_url": "https://..."}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Failed response", value={"status": "success", "message": "PDF status retrieved successfully.", "data": {"task_id": "abc", "status": "failed", "error": "Worker timeout"}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="pdf-status/(?P<task_id>[^/.]+)")
def pdf_status(self, request, task_id=None):
    """
    Check PDF generation status with multi-PDF support.
    """
    try:

        if not task_id:
            return self.validation_error_response(
                errors={"task_id": ["This field is required."]},
                message="task_id is required."
            )

        try:
            pdf_task = PDFGenerationTask.objects.get(task_id=task_id)
        except PDFGenerationTask.DoesNotExist:
            return self.not_found_response(
                resource="PDFGenerationTask",
                message="Task not found."
            )

        elapsed = (timezone.now() - pdf_task.created_at).total_seconds()
        is_multi_pdf = pdf_task.total_chunks and pdf_task.total_chunks > 1

        # Base response
        response_data = {
            'task_id': task_id,
            'status': pdf_task.status,
            'elapsed_seconds': int(elapsed),
            'is_multi_pdf': is_multi_pdf,
            'total_chunks': pdf_task.total_chunks or 1
        }

        if pdf_task.status == 'completed':
            if is_multi_pdf:
                # Multi-PDF response
                chunk_tasks = pdf_task.request_params.get('_chunk_tasks', [])
                completed_pdfs = pdf_task.request_params.get('_completed_pdfs', [])

                response_data.update({
                    'completed_pdfs': completed_pdfs,
                    'total_products': pdf_task.estimated_count,
                    'generation_time': f"{pdf_task.actual_generation_time:.2f}s",
                    'message': f'{len(completed_pdfs)} PDFs generated successfully'
                })
            else:
                # Single PDF response
                response_data.update({
                    'download_url': pdf_task.download_url,
                    'expires_at': pdf_task.url_expires_at.isoformat() if pdf_task.url_expires_at else None,
                    'generation_time': f"{pdf_task.actual_generation_time:.2f}s",
                    'cached': pdf_task.was_cached,
                    'cache_hit_type': pdf_task.cache_hit_type
                })

        elif pdf_task.status in ['pending', 'processing', 'chunking']:
            progress_msg = 'Initializing...'
            if elapsed < 30:
                progress_msg = 'Starting...'
            elif elapsed < 60:
                progress_msg = 'Processing...'
            elif elapsed < 120:
                progress_msg = 'Generating PDF...'
            else:
                progress_msg = 'Taking longer than expected...'

            response_data['progress'] = progress_msg

            if is_multi_pdf:
                chunk_tasks = pdf_task.request_params.get('_chunk_tasks', [])
                completed_chunks = sum(1 for c in chunk_tasks if c.get('status') == 'completed')
                response_data['completed_chunks'] = completed_chunks

        elif pdf_task.status == 'failed':
            response_data['error'] = pdf_task.error_message

        return self.success_response(
            data=response_data,
            message="PDF status retrieved successfully."
        )

    except Exception as e:
        logger.error(f"[PDF Status] Error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve PDF status.",
            dev_message=str(e)
        )
