import logging
import os
import tempfile
from datetime import date, datetime, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("api_requests")

EXCEL_URL_EXPIRY_HOURS = 24


def _parse_range(request_params):
    """(start, end) tz-aware datetimes from the stored ISO dates; end exclusive."""
    tz = timezone.get_current_timezone()
    start_day = date.fromisoformat(request_params["start_date"])
    end_day = date.fromisoformat(request_params["end_date"])
    start = timezone.make_aware(datetime.combine(start_day, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(end_day + timedelta(days=1), datetime.min.time()), tz)
    return start, end


# The explicit name keeps the registered task name identical to when this
# task lived in trending_insights/tasks.py, so messages already queued under
# that name are still consumed after the move into the tasks/ package.
@shared_task(
    bind=True,
    max_retries=0,
    time_limit=300,
    soft_time_limit=280,
    name="trending_insights.tasks.generate_unused_products_excel_task",
)
def generate_unused_products_excel_task(self, task_id, shop_id, request_params):
    """
    Builds the Unused & Low-Usage Products Excel report for an
    ExcelGenerationTask, uploads it to S3 and stores a pre-signed URL.
    Uses the same get_product_usage() calculation as the Unused Products API.
    The workbook is streamed to a temporary file (constant_memory), which is
    always removed afterwards.
    """
    from services.models import ExcelGenerationTask
    from services.s3_service import S3Service
    from shop.models import Shop
    from trending_insights.api.v3.helpers.unused_products_excel import (
        REPORT_KEY, build_excel_filename, write_unused_products_excel,
    )

    excel_task = ExcelGenerationTask.objects.filter(task_id=task_id, shop_id=shop_id).first()
    if excel_task is None:
        logger.error(f"[UnusedProductsExcel] Task {task_id} not found for shop {shop_id}")
        return {"task_id": task_id, "status": "failed", "error": "task not found"}

    tmp_path = None
    started = timezone.now()
    try:
        excel_task.status = "processing"
        excel_task.save(update_fields=["status"])

        start, end = _parse_range(request_params)
        shop_name = Shop.objects.only("id", "name").get(id=shop_id).name

        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        rows_written = write_unused_products_excel(tmp_path, shop_id, shop_name, start, end)

        with open(tmp_path, "rb") as fh:
            excel_bytes = fh.read()

        s3_key = f"excel_reports/{REPORT_KEY}/{shop_id}/{task_id}.xlsx"
        download_url = S3Service.upload_excel(
            s3_key,
            excel_bytes,
            expires_in_hours=EXCEL_URL_EXPIRY_HOURS,
            download_filename=build_excel_filename(shop_name, start, end),
        )

        now = timezone.now()
        excel_task.status = "completed"
        excel_task.s3_key = s3_key
        excel_task.download_url = download_url
        excel_task.estimated_count = rows_written
        excel_task.completed_at = now
        excel_task.url_expires_at = now + timedelta(hours=EXCEL_URL_EXPIRY_HOURS)
        excel_task.actual_generation_time = (now - started).total_seconds()
        excel_task.error_message = None
        excel_task.save()

        logger.info(f"[UnusedProductsExcel] Task {task_id}: {rows_written} rows in {excel_task.actual_generation_time:.2f}s")
        return {"task_id": task_id, "status": "completed", "rows": rows_written}

    except Exception as e:
        logger.error(f"[UnusedProductsExcel] Task {task_id} failed: {e}", exc_info=True)
        ExcelGenerationTask.objects.filter(pk=excel_task.pk).update(
            status="failed", error_message=f"{type(e).__name__}: {e}"[:2000],
        )
        return {"task_id": task_id, "status": "failed", "error": str(e)}

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning(f"[UnusedProductsExcel] Could not remove temp file {tmp_path}")
