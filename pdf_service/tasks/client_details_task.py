import re
import time

from celery import shared_task

from .utils import logger


def _sanitize_filename_part(value: str, fallback: str) -> str:
    """Collapse whitespace to single underscores and strip anything unsafe
    for a filename, so a shop name like 'Stylin  Boutique' becomes
    'Stylin_Boutique'. Falls back (e.g. to the shop id) if the name is
    empty/blank or sanitizes down to nothing."""
    cleaned = re.sub(r"\s+", "_", (value or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", cleaned)
    return cleaned or fallback


@shared_task(bind=True, name='pdf_service.tasks.generate_client_details_pdf_task', max_retries=1, time_limit=300, soft_time_limit=280)
def generate_client_details_pdf_task(self, task_id, shop_id, request_params):
    """
    Generate a shop's client-details PDF (S.No. / Date / Name / Phone Number 1 /
    Phone Number 2, newest client first) asynchronously.

    Args:
        task_id: Unique task identifier
        shop_id: Shop ID
        request_params: Dict (currently empty - no filters for this report)
    """
    from pdf_service.models import PDFTask
    from pdf_service.api.v3.utils.client_details_pdf import ClientDetailsPDFService
    from services.s3_service import S3Service
    from booking_v2.api.v3.views.client_viewset.helpers.client_report import client_details_queryset
    from shop.models import Shop
    from django.utils import timezone as django_timezone

    pdf_task = None
    start_time = time.time()

    try:
        try:
            pdf_task = PDFTask.objects.get(task_id=task_id)
        except PDFTask.DoesNotExist:
            logger.error(f"[Client Details PDF] Task {task_id} not found in database")
            raise ValueError(f"PDF task {task_id} not found")

        pdf_task.mark_processing()

        logger.info(f"[Client Details PDF] Starting generation for task {task_id}")

        try:
            shop = Shop.objects.get(id=shop_id)
            shop_info = {
                'name': shop.name or '',
                'phone': shop.phone or '',
                'phone2': shop.phone2 or '',
                'email': shop.email or '',
                'address': shop.address or '',
            }
        except Shop.DoesNotExist:
            raise ValueError(f"Shop {shop_id} not found")

        # Memory-efficient, N+1-free: no related-object traversal, only the
        # 4 display fields (+ id) are loaded, and iterator() avoids holding
        # the full queryset in memory for shops with many clients.
        # Newest client first; -id keeps the order stable for equal timestamps.
        # Shared with the Client Details Excel export (same data + order).
        clients_qs = client_details_queryset(shop_id)

        # The view already computed this identical count when creating
        # pdf_task - reuse it instead of running the same COUNT query again.
        estimated_count = pdf_task.estimated_count

        logger.info(f"[Client Details PDF] Processing {estimated_count} clients")

        pdf_bytes = ClientDetailsPDFService.generate_client_details_pdf(
            shop_info=shop_info,
            clients=clients_qs.iterator(chunk_size=2000),
            shop=shop,
        )

        timestamp = django_timezone.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"pdf_reports/shop_{shop_id}/client_details/{task_id}_{timestamp}.pdf"
        shop_name_part = _sanitize_filename_part(shop.name, fallback=str(shop_id))
        download_filename = f"Client_Details_{shop_name_part}.pdf"

        download_url = S3Service.upload_pdf(
            s3_key,
            pdf_bytes,
            expires_in_hours=24,
            download_filename=download_filename,
        )

        generation_time = time.time() - start_time
        file_size = len(pdf_bytes)

        pdf_task.mark_completed(s3_key, download_url, generation_time, file_size)

        logger.info(
            f"[Client Details PDF] Completed task {task_id} in {generation_time:.2f}s "
            f"({estimated_count} clients, {file_size/1024:.1f}KB)"
        )

        return {
            'task_id': task_id,
            'status': 'completed',
            'download_url': download_url,
            'generation_time': generation_time,
            'client_count': estimated_count,
            'file_size': file_size,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Client Details PDF] Task {task_id} failed: {error_msg}", exc_info=True)

        if pdf_task:
            pdf_task.mark_failed(error_msg)

        raise
