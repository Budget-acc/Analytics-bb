import time
from decimal import Decimal

from celery import shared_task

from .utils import logger


@shared_task(bind=True, name='pdf_service.tasks.generate_tax_report_pdf_task', max_retries=1, time_limit=300, soft_time_limit=280)
def generate_tax_report_pdf_task(self, task_id, shop_id, request_params):
    """
    Generate the consolidated Tax Report PDF asynchronously.

    Args:
        task_id: Unique task identifier
        shop_id: Shop ID
        request_params: Dict with optional 'start_date', 'end_date' (isoformat strings, or None)
    """
    from pdf_service.models import PDFTask
    from pdf_service.api.v3.utils.tax_report_pdf import TaxReportPDFService
    from pdf_service.api.v3.utils.helpers import parse_date
    from services.s3_service import S3Service
    from finance.api.v3.views.tax_report_viewset.helpers import build_tax_report_queryset, get_tax_report_summary
    from shop.models import Shop
    from django.utils import timezone as django_timezone

    pdf_task = None
    start_time = time.time()

    try:
        try:
            pdf_task = PDFTask.objects.get(task_id=task_id)
        except PDFTask.DoesNotExist:
            logger.error(f"[Tax Report PDF] Task {task_id} not found in database")
            raise ValueError(f"PDF task {task_id} not found")

        pdf_task.mark_processing()

        logger.info(f"[Tax Report PDF] Starting generation for task {task_id}")

        try:
            shop = Shop.objects.get(id=shop_id)
            shop_info = {
                'name': shop.name or '',
                'phone': shop.phone or '',
                'phone2': shop.phone2 or '',
                'email': shop.email or '',
                'address': shop.address or '',
                'gst_number': shop.gst_number or '',
            }
        except Shop.DoesNotExist:
            raise ValueError(f"Shop {shop_id} not found")

        start_date_raw = request_params.get('start_date')
        end_date_raw = request_params.get('end_date')
        start_date = parse_date(start_date_raw) if isinstance(start_date_raw, str) else start_date_raw
        end_date = parse_date(end_date_raw) if isinstance(end_date_raw, str) else end_date_raw

        # build_tax_report_queryset/get_tax_report_summary already union/aggregate
        # scalar snapshot fields only (no related-object access), so pulling every
        # matching row here stays a small, fixed number of DB queries regardless
        # of row count - no N+1 risk.
        qs = build_tax_report_queryset(shop_id, start_date, end_date)
        rows = []
        for row in qs:
            tax_amount = row["tax_amount"]
            # Round CGST first, then derive SGST as the remainder so the two
            # always sum exactly to tax_amount - halving both independently
            # and rounding each to 2dp can drift by a paisa on odd-cent
            # amounts (e.g. 99.99 -> 49.995 + 49.995, each rounds to 50.00).
            cgst = (tax_amount / 2).quantize(Decimal("0.01"))
            sgst = tax_amount - cgst
            rows.append({
                **row,
                "cgst": cgst,
                "sgst": sgst,
            })
        summary = get_tax_report_summary(shop_id, start_date, end_date)

        pdf_task.estimated_count = len(rows)
        pdf_task.save(update_fields=['estimated_count'])

        logger.info(f"[Tax Report PDF] Processing {len(rows)} rows")

        pdf_bytes = TaxReportPDFService.generate_tax_report_pdf(
            shop_info=shop_info,
            rows=rows,
            summary=summary,
            start_date=start_date.strftime('%d/%m/%Y') if start_date else '',
            end_date=end_date.strftime('%d/%m/%Y') if end_date else '',
            shop=shop,
        )

        timestamp = django_timezone.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"pdf_reports/shop_{shop_id}/tax_report/{task_id}_{timestamp}.pdf"

        date_part = ""
        if start_date and end_date:
            date_part = f"_{start_date.strftime('%d-%m-%Y')}_{end_date.strftime('%d-%m-%Y')}"
        download_filename = f"tax_report{date_part}.pdf"

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
            f"[Tax Report PDF] Completed task {task_id} in {generation_time:.2f}s "
            f"({len(rows)} rows, {file_size/1024:.1f}KB)"
        )

        return {
            'task_id': task_id,
            'status': 'completed',
            'download_url': download_url,
            'generation_time': generation_time,
            'entry_count': len(rows),
            'file_size': file_size,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Tax Report PDF] Task {task_id} failed: {error_msg}", exc_info=True)

        if pdf_task:
            pdf_task.mark_failed(error_msg)

        raise
