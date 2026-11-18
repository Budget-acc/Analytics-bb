import time

from celery import shared_task

from .utils import build_pdf_download_filename, logger


@shared_task(bind=True, name='pdf_service.tasks.generate_ledger_pdf_task', max_retries=1, time_limit=300, soft_time_limit=280)
def generate_ledger_pdf_task(self, task_id, shop_id, request_params):
    """
    Generate ledger book PDF asynchronously.

    Args:
        task_id: Unique task identifier
        shop_id: Shop ID
        request_params: Dict with 'from_date', 'to_date', 'client_id' (optional)
    """
    from pdf_service.models import PDFTask
    from pdf_service.api.v3.utils.ledger_report_pdf import LedgerBookPDFService
    from services.s3_service import S3Service
    from pdf_service.api.v3.views.mixins import ShopLedgerTransactionExportMixin
    from shop.models import Shop
    from django.utils import timezone as django_timezone

    pdf_task = None
    start_time = time.time()

    try:
        # Get task
        try:
            pdf_task = PDFTask.objects.get(task_id=task_id)
        except PDFTask.DoesNotExist:
            logger.error(f"[Ledger PDF] Task {task_id} not found in database")
            raise ValueError(f"PDF task {task_id} not found")

        pdf_task.mark_processing()

        logger.info(f"[Ledger PDF] Starting generation for task {task_id}")

        # Get shop info
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

        # Get transaction data
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

        # Get transactions
        transactions = mixin.get_transactions(shop_id, from_date, to_date, txn_types, client_id)

        # Transactions are already in the correct format with all fields
        # No need to convert - pass them directly
        transactions_list = transactions

        # Update estimated count
        pdf_task.estimated_count = len(transactions_list)
        pdf_task.save(update_fields=['estimated_count'])

        logger.info(f"[Ledger PDF] Processing {len(transactions_list)} transactions")

        # Generate PDF
        pdf_bytes = LedgerBookPDFService.generate_ledger_pdf(
            shop_info=shop_info,
            transactions=transactions_list,
            start_date=from_date.strftime('%d-%m-%Y') if from_date else '',
            end_date=to_date.strftime('%d-%m-%Y') if to_date else '',
            shop=shop  # Pass shop object for logo
        )

        # Upload to S3
        timestamp = django_timezone.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"pdf_reports/shop_{shop_id}/ledger/{task_id}_{timestamp}.pdf"

        download_filename = build_pdf_download_filename("ledger_book", request_params)
        download_url = S3Service.upload_pdf(
            s3_key,
            pdf_bytes,
            expires_in_hours=24,
            download_filename=download_filename,
        )

        generation_time = time.time() - start_time
        file_size = len(pdf_bytes)

        # Mark as completed
        pdf_task.mark_completed(s3_key, download_url, generation_time, file_size)

        logger.info(
            f"[Ledger PDF] Completed task {task_id} in {generation_time:.2f}s "
            f"({len(transactions_list)} transactions, {file_size/1024:.1f}KB)"
        )

        return {
            'task_id': task_id,
            'status': 'completed',
            'download_url': download_url,
            'generation_time': generation_time,
            'transaction_count': len(transactions_list),
            'file_size': file_size
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Ledger PDF] Task {task_id} failed: {error_msg}", exc_info=True)

        if pdf_task:
            pdf_task.mark_failed(error_msg)

        raise