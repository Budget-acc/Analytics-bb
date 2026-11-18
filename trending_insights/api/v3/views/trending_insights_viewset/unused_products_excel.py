import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status

from services.models import ExcelGenerationTask

from ...helpers import MAX_RANGE_DAYS, get_date_range, get_product_usage
from ...helpers.unused_products_excel import REPORT_KEY
from .schema_params import DATE_RANGE_ERRORS, END_DATE_PARAM, START_DATE_PARAM, TASK_ID_PARAM

logger = logging.getLogger("api_requests")

ACTIVE_STATUSES = ("pending", "processing", "chunking")
DEDUP_WINDOW = timedelta(minutes=15)


def _request_hash(shop_id, request_params):
    payload = json.dumps({"shop_id": shop_id, **request_params}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


class UnusedProductsExcelReportMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Start unused/low-usage products Excel report",
        description=(
            "Starts background generation of an Excel report of the shop's unused (0 orders) and "
            "low-usage (1-5 orders) products for the requested date range, and returns a task_id "
            "to poll with unused-products/excel-status/{task_id}/.\n\n"
            "Purpose: Lets the shop owner/manager download the full unused/low-usage product list "
            "(not just one page) for offline review.\n\n"
            "Context: Uses exactly the same calculation as the unused-products/ endpoint, so the "
            "report contains the same products, order counts, last ordered dates and order "
            "(order count ascending). The file is generated in the background, streamed row by "
            "row, so it stays fast and memory-safe for any catalog size.\n\n"
            "Report columns: S.No, Product Name, Specification (Color, Model, Category), Number of "
            "Bookings/Orders, Last Ordered Date (DD-MM-YYYY; blank when there are no orders).\n\n"
            "A repeated request for the same shop and date range while a report is still being "
            "generated (within 15 minutes) returns the existing task (reused: true) instead of "
            "starting a new one. When no product has 0-5 orders, no file is generated and "
            "status is 'completed' with download_url null.\n\n"
            "Accepted inputs:\n"
            "Query parameters - Required:\n"
            "- start_date (YYYY-MM-DD or DD-MM-YYYY): start of the date range.\n"
            "- end_date (YYYY-MM-DD or DD-MM-YYYY): end of the date range (inclusive). Range must "
            "not exceed 180 days.\n\n"
            "No path parameters or request body."
        ),
        parameters=[START_DATE_PARAM, END_DATE_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Exact messages: Excel generation initiated successfully.; Using "
                    "existing Excel generation task.; No unused or low-usage products found for "
                    "this date range.\n\n"
                    "Response shape (under `data`): task_id (string | null), status (pending | "
                    "processing | completed), estimated_products (integer - products in the "
                    "report), reused (boolean), download_url (string | null, only when no file "
                    "is needed)."
                )
            ),
            400: OpenApiResponse(
                description=f"Validation error. Exact messages: {DATE_RANGE_ERRORS}"
            ),
            403: OpenApiResponse(
                description="Permission error. Exact message: Access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Unable to start Excel generation."
            ),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={"start_date": "2026-04-01", "end_date": "2026-09-27"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Excel generation initiated successfully.",
                    "data": {
                        "task_id": "5b0c1f2e-8a1d-4c3e-9b7a-2f6d1e0c9a11",
                        "status": "pending",
                        "estimated_products": 128,
                        "reused": False,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "No products response",
                value={
                    "status": "success",
                    "message": "No unused or low-usage products found for this date range.",
                    "data": {
                        "task_id": None, "status": "completed", "estimated_products": 0,
                        "reused": False, "download_url": None,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error response",
                value={"status": "error", "message": "Date range cannot exceed 180 days.", "data": None},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            start, end = get_date_range(
                request.query_params.get("start_date"),
                request.query_params.get("end_date"),
                max_days=MAX_RANGE_DAYS,
            )
            request_params = {
                "report": REPORT_KEY,
                "start_date": start.date().isoformat(),
                "end_date": (end - timedelta(days=1)).date().isoformat(),
            }
            request_hash = _request_hash(self.shop_id, request_params)

            existing = (
                ExcelGenerationTask.objects.filter(
                    shop_id=self.shop_id,
                    request_hash=request_hash,
                    status__in=ACTIVE_STATUSES,
                    created_at__gte=timezone.now() - DEDUP_WINDOW,
                )
                .order_by("-created_at")
                .first()
            )
            if existing:
                return self.success_response(
                    data={
                        "task_id": existing.task_id,
                        "status": existing.status,
                        "estimated_products": existing.estimated_count,
                        "reused": True,
                    },
                    message="Using existing Excel generation task.",
                )

            estimated_count = get_product_usage(self.shop_id, start, end).count()
            if estimated_count == 0:
                return self.success_response(
                    data={
                        "task_id": None,
                        "status": "completed",
                        "estimated_products": 0,
                        "reused": False,
                        "download_url": None,
                    },
                    message="No unused or low-usage products found for this date range.",
                )

            task_id = str(uuid.uuid4())
            excel_task = ExcelGenerationTask.objects.create(
                task_id=task_id,
                shop_id=self.shop_id,
                status="pending",
                request_params=request_params,
                request_hash=request_hash,
                estimated_count=estimated_count,
                total_chunks=1,
            )

            from trending_insights.tasks import generate_unused_products_excel_task
            try:
                generate_unused_products_excel_task.delay(task_id, self.shop_id, request_params)
            except Exception as e:
                # Broker unavailable - don't leave a task stuck in "pending".
                logger.error(f"UnusedProductsExcel: could not queue task {task_id}: {e}", exc_info=True)
                ExcelGenerationTask.objects.filter(pk=excel_task.pk).update(
                    status="failed", error_message="Background worker unavailable."
                )
                return self.error_response(
                    message="Unable to start Excel generation.",
                    dev_message="Background worker unavailable.",
                    http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            return self.success_response(
                data={
                    "task_id": task_id,
                    "status": "pending",
                    "estimated_products": estimated_count,
                    "reused": False,
                },
                message="Excel generation initiated successfully.",
            )

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"UnusedProductsExcel start error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to start Excel generation.",
                dev_message=f"{type(e).__name__}: {e}",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UnusedProductsExcelStatusMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Get unused/low-usage products Excel report status",
        description=(
            "Returns the status of an Excel report started with unused-products/excel-report/, "
            "and the download link once it is ready.\n\n"
            "Purpose: Lets the app poll until the report file is generated, then download it.\n\n"
            "Context: Poll every few seconds after starting a report. Only tasks of this report "
            "type belonging to the active shop can be read. The download link expires 24 hours "
            "after generation.\n\n"
            "Accepted inputs:\n"
            "Path parameters - Required:\n"
            "- task_id (string): the task_id returned by unused-products/excel-report/.\n\n"
            "No query parameters or request body."
        ),
        parameters=[TASK_ID_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Exact message: Excel status retrieved successfully.\n\n"
                    "Response shape (under `data`): task_id, status (pending | processing | "
                    "completed | failed), elapsed_seconds (integer). When completed: "
                    "download_url (string), expires_at (ISO datetime), generation_time (string, "
                    "e.g. '2.41s'), total_products (integer). While pending/processing: progress "
                    "(string). When failed: error (string)."
                )
            ),
            403: OpenApiResponse(
                description="Permission error. Exact message: Access to this shop has been deactivated."
            ),
            404: OpenApiResponse(description="Exact message: Task not found."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve Excel status."),
        },
        examples=[
            OpenApiExample(
                "Completed response",
                value={
                    "status": "success",
                    "message": "Excel status retrieved successfully.",
                    "data": {
                        "task_id": "5b0c1f2e-8a1d-4c3e-9b7a-2f6d1e0c9a11",
                        "status": "completed",
                        "elapsed_seconds": 6,
                        "download_url": "https://bucket.s3.amazonaws.com/excel_reports/unused_products/49/5b0c....xlsx?...",
                        "expires_at": "2026-09-28T12:00:00+05:30",
                        "generation_time": "2.41s",
                        "total_products": 128,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Failed response",
                value={
                    "status": "success",
                    "message": "Excel status retrieved successfully.",
                    "data": {
                        "task_id": "5b0c1f2e-8a1d-4c3e-9b7a-2f6d1e0c9a11",
                        "status": "failed",
                        "elapsed_seconds": 40,
                        "error": "Excel generation failed. Please try again.",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Not found response",
                value={"status": "not_found", "message": "Task not found.", "data": None},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    def get(self, request, task_id=None, *args, **kwargs):
        try:
            excel_task = (
                ExcelGenerationTask.objects.filter(
                    task_id=task_id,
                    shop_id=self.shop_id,
                    request_params__report=REPORT_KEY,
                )
                .only(
                    "task_id", "status", "created_at", "download_url", "url_expires_at",
                    "actual_generation_time", "estimated_count", "error_message",
                )
                .first()
            )
            if excel_task is None:
                return self.not_found_response(
                    resource="ExcelGenerationTask",
                    message="Task not found.",
                )

            elapsed = (timezone.now() - excel_task.created_at).total_seconds()
            data = {
                "task_id": excel_task.task_id,
                "status": excel_task.status,
                "elapsed_seconds": int(elapsed),
            }
            dev_message = None

            if excel_task.status == "completed":
                data.update({
                    "download_url": excel_task.download_url,
                    "expires_at": excel_task.url_expires_at.isoformat() if excel_task.url_expires_at else None,
                    "generation_time": (
                        f"{excel_task.actual_generation_time:.2f}s"
                        if excel_task.actual_generation_time is not None else None
                    ),
                    "total_products": excel_task.estimated_count,
                })
            elif excel_task.status in ACTIVE_STATUSES:
                if elapsed < 15:
                    data["progress"] = "Starting..."
                elif elapsed < 60:
                    data["progress"] = "Generating Excel..."
                else:
                    data["progress"] = "Taking longer than expected..."
            elif excel_task.status == "failed":
                data["error"] = "Excel generation failed. Please try again."
                dev_message = excel_task.error_message

            return self.success_response(
                data=data,
                message="Excel status retrieved successfully.",
                **({"dev_message": dev_message} if dev_message else {}),
            )

        except Exception as e:
            logger.error(f"UnusedProductsExcel status error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve Excel status.",
                dev_message=f"{type(e).__name__}: {e}",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
