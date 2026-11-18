import time
from datetime import date

from celery import shared_task

from .utils import logger

GENERATION_FAILED_MESSAGE = "Failed to generate booking PDF."


@shared_task(bind=True, name='pdf_service.tasks.generate_booking_list_pdf_task', max_retries=1, time_limit=300, soft_time_limit=280)
def generate_booking_list_pdf_task(self, task_id, shop_id, request_params):
    """
    Generate the Booking List PDF (mobile or desktop list filters) and upload it to S3.

    Args:
        task_id: PDFTask.task_id created by ExportBookingListPDFView
        shop_id: shop the bookings belong to
        request_params: {platform, status, search, start_date, end_date, purchase_mode, today}
            `today` (ISO date) is the date the export was requested, so date-relative
            statuses match what the user saw in the list.
    """
    from django.utils import timezone as django_timezone

    from pdf_service.api.v3.utils.booking_list_pdf import (
        BookingListPDFService, build_queryset, collect_rows, download_filename, filter_lines,
        shop_info, with_pdf_columns,
    )
    from pdf_service.models import PDFTask
    from services.s3_service import S3Service
    from shop.models import Shop

    start_time = time.time()
    pdf_task = PDFTask.objects.filter(task_id=task_id, pdf_type="booking_list").first()
    if pdf_task is None:
        logger.error(f"[Booking List PDF] Task {task_id} not found")
        return {"task_id": task_id, "status": "missing"}
    if pdf_task.status != "pending":
        # Duplicate delivery / retry of a task that already ran: never generate twice.
        logger.warning(f"[Booking List PDF] Task {task_id} skipped (status={pdf_task.status})")
        return {"task_id": task_id, "status": pdf_task.status}

    try:
        pdf_task.mark_processing()
        shop = Shop.objects.get(id=shop_id)
        platform = request_params["platform"]
        today = date.fromisoformat(request_params["today"])

        queryset = with_pdf_columns(build_queryset(platform, shop_id, request_params, today))
        rows, totals = collect_rows(queryset)

        pdf_bytes = BookingListPDFService.generate(
            shop_info=shop_info(shop),
            rows=rows,
            totals=totals,
            filter_lines=filter_lines(platform, request_params, django_timezone.now()),
            shop=shop,
        )

        timestamp = django_timezone.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"pdf_reports/shop_{shop_id}/booking_list/{task_id}_{timestamp}.pdf"
        download_url = S3Service.upload_pdf(
            s3_key,
            pdf_bytes,
            expires_in_hours=24,
            download_filename=download_filename(platform, request_params, today),
        )

        generation_time = time.time() - start_time
        pdf_task.mark_completed(s3_key, download_url, generation_time, len(pdf_bytes))
        logger.info(
            f"[Booking List PDF] Completed task {task_id} for shop {shop_id} ({platform}): "
            f"{totals['count']} bookings, {len(pdf_bytes) / 1024:.1f}KB in {generation_time:.2f}s"
        )
        return {"task_id": task_id, "status": "completed", "booking_count": totals["count"]}

    except Exception:
        logger.exception(f"[Booking List PDF] Task {task_id} failed for shop {shop_id}")
        # User-facing reason only; the details are in the log above.
        pdf_task.mark_failed(GENERATION_FAILED_MESSAGE)
        raise
