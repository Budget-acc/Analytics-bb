import logging
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.utils import timezone
from services.models import ExcelGenerationTask

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Get Excel generation status",
    description="Returns status and output metadata for an async Excel generation task.",
    parameters=[OpenApiParameter("task_id", OpenApiTypes.STR, OpenApiParameter.PATH, required=True, description="Excel task id.")],
    responses={
        200: OpenApiResponse(description="Standard success response with pending/completed/failed task data."),
        400: OpenApiResponse(description="Exact message: task_id is required."),
        404: OpenApiResponse(description="Exact message: Task not found."),
        500: OpenApiResponse(description="Exact message: Unable to retrieve Excel status."),
    },
    examples=[
        OpenApiExample("Completed response", value={"status": "success", "message": "Excel status retrieved successfully.", "data": {"task_id": "abc", "status": "completed", "download_url": "https://..."}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Failed response", value={"status": "success", "message": "Excel status retrieved successfully.", "data": {"task_id": "abc", "status": "failed", "error": "Worker timeout"}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="excel-status/(?P<task_id>[^/.]+)")
def excel_status(self, request, task_id=None):
    """
    Check Excel generation status with multi-chunk support.
    """
    try:

        if not task_id:
            return self.validation_error_response(
                errors={"task_id": ["This field is required."]},
                message="task_id is required."
            )

        try:
            excel_task = ExcelGenerationTask.objects.get(task_id=task_id, shop_id=self.shop_id)
        except ExcelGenerationTask.DoesNotExist:
            return self.not_found_response(
                resource="ExcelGenerationTask",
                message="Task not found."
            )

        elapsed = (timezone.now() - excel_task.created_at).total_seconds()
        is_multi_excel = excel_task.total_chunks and excel_task.total_chunks > 1

        response_data = {
            'task_id': task_id,
            'status': excel_task.status,
            'elapsed_seconds': int(elapsed),
            'is_multi_excel': is_multi_excel,
            'total_chunks': excel_task.total_chunks or 1
        }

        if excel_task.status == 'completed':
            if is_multi_excel:
                chunk_tasks = excel_task.request_params.get('_chunk_tasks', [])
                completed_excels = excel_task.request_params.get('_completed_excels', [])

                response_data.update({
                    'completed_excels': completed_excels,
                    'total_products': excel_task.estimated_count,
                    'generation_time': f"{excel_task.actual_generation_time:.2f}s",
                    'message': f'{len(completed_excels)} Excel files generated successfully'
                })
            else:
                response_data.update({
                    'download_url': excel_task.download_url,
                    'expires_at': excel_task.url_expires_at.isoformat() if excel_task.url_expires_at else None,
                    'generation_time': f"{excel_task.actual_generation_time:.2f}s",
                    'cached': excel_task.was_cached,
                    'cache_hit_type': excel_task.cache_hit_type
                })

        elif excel_task.status in ['pending', 'processing', 'chunking']:
            progress_msg = 'Initializing...'
            if elapsed < 15:
                progress_msg = 'Starting...'
            elif elapsed < 30:
                progress_msg = 'Processing...'
            elif elapsed < 60:
                progress_msg = 'Generating Excel...'
            else:
                progress_msg = 'Taking longer than expected...'

            response_data['progress'] = progress_msg

            if is_multi_excel:
                chunk_tasks = excel_task.request_params.get('_chunk_tasks', [])
                completed_chunks = sum(1 for c in chunk_tasks if c.get('status') == 'completed')
                response_data['completed_chunks'] = completed_chunks

        elif excel_task.status == 'failed':
            response_data['error'] = excel_task.error_message

        return self.success_response(
            data=response_data,
            message="Excel status retrieved successfully."
        )

    except Exception as e:
        logger.error(f"[Excel Status] Error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve Excel status.",
            dev_message=str(e)
        )
