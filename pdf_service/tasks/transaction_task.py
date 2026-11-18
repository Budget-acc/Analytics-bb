import io
import time

from celery import shared_task

from ERP.utils.number_format import format_inr_or_raw

from .utils import build_pdf_download_filename, logger


@shared_task(bind=True, name='pdf_service.tasks.generate_transaction_pdf_task', max_retries=1, time_limit=300, soft_time_limit=280)
def generate_transaction_pdf_task(self, task_id, shop_id, request_params):
    """
    Generate transaction report PDF asynchronously.
    Similar to ledger but with different formatting.
    """
    from pdf_service.models import PDFTask
    from services.s3_service import S3Service
    from pdf_service.api.v3.views.mixins import ShopLedgerTransactionExportMixin
    from shop.models import Shop
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
    from django.utils import timezone as django_timezone

    pdf_task = None
    start_time = time.time()

    try:
        try:
            pdf_task = PDFTask.objects.get(task_id=task_id)
        except PDFTask.DoesNotExist:
            logger.error(f"[Transaction PDF] Task {task_id} not found in database")
            raise ValueError(f"PDF task {task_id} not found")

        pdf_task.mark_processing()

        logger.info(f"[Transaction PDF] Starting generation for task {task_id}")

        # Get transaction data (reuse existing logic)
        mixin = ShopLedgerTransactionExportMixin()

        from_date = request_params.get('from_date')
        to_date = request_params.get('to_date')
        client_id = request_params.get('client_id')
        txn_types = request_params.get('txn_types', ['all'])

        # Parse dates
        if isinstance(from_date, str):
            from_date = mixin.parse_date(from_date)
        if isinstance(to_date, str):
            to_date = mixin.parse_date(to_date)

        transactions = list(mixin.get_transactions(shop_id, from_date, to_date, txn_types, client_id))

        pdf_task.estimated_count = len(transactions)
        pdf_task.save(update_fields=['estimated_count'])

        # Generate PDF (optimized version)
        buffer = io.BytesIO()
        p = pdf_canvas.Canvas(buffer, pagesize=A4)
        y = 800
        p.setFont("Helvetica-Bold", 14)
        p.drawString(100, y, "Shop Transaction Report")
        y -= 20

        p.setFont("Helvetica", 10)
        period = f"Period: {from_date.strftime('%d-%m-%Y') if from_date else 'N/A'} to {to_date.strftime('%d-%m-%Y') if to_date else 'N/A'}"
        p.drawString(100, y, period)
        y -= 30

        p.setFont("Helvetica", 9)

        for txn in transactions:
            # Safely truncate long fields
            name_text = (txn.get('name') or 'N/A')[:20]
            desc_text = (txn.get('description') or '')[:50]
            items_text = (txn.get('items', ''))[:30] if txn.get('items') else ''

            if txn['type'] == 'sale' and items_text:
                line = f"[{txn['date']}] {txn['type'].title()} | {name_text} | {format_inr_or_raw(txn['amount'])} | {desc_text} | Items: {items_text}"
            else:
                line = f"[{txn['date']}] {txn['type'].title()} | {name_text} | {format_inr_or_raw(txn['amount'])} | {desc_text}"

            p.drawString(50, y, line)
            y -= 15

            if y < 50:
                p.showPage()
                p.setFont("Helvetica", 9)
                y = 800

        p.save()
        buffer.seek(0)
        pdf_bytes = buffer.getvalue()

        # Upload to S3
        timestamp = django_timezone.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"pdf_reports/shop_{shop_id}/transactions/{task_id}_{timestamp}.pdf"

        download_filename = build_pdf_download_filename("transaction_report", request_params)
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
            f"[Transaction PDF] Completed task {task_id} in {generation_time:.2f}s "
            f"({len(transactions)} transactions, {file_size/1024:.1f}KB)"
        )

        return {
            'task_id': task_id,
            'status': 'completed',
            'download_url': download_url,
            'generation_time': generation_time,
            'transaction_count': len(transactions),
            'file_size': file_size
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Transaction PDF] Task {task_id} failed: {error_msg}", exc_info=True)

        if pdf_task:
            pdf_task.mark_failed(error_msg)

        raise
