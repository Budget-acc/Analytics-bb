import time

from celery import shared_task

from .utils import logger


@shared_task(bind=True, name='pdf_service.tasks.generate_account_history_pdf_task', max_retries=1, time_limit=300, soft_time_limit=280)
def generate_account_history_pdf_task(self, task_id, shop_id, request_params):
    """
    Generate a single account's bank-statement-style history PDF asynchronously.

    Args:
        task_id: Unique task identifier
        shop_id: Shop ID
        request_params: Dict with 'account_id', 'start_date', 'end_date' (isoformat strings)
    """
    from pdf_service.models import PDFTask
    from pdf_service.api.v3.utils.account_history_pdf import AccountHistoryPDFService
    from pdf_service.api.v3.utils.helpers import parse_date
    from services.s3_service import S3Service
    from finance.models import Account
    from finance.api.v3.utils.account_history import get_account_history_data
    from finance.api.v3.utils.reference_id_resolver import resolve_ledger_reference_ids
    from finance.api.v3.utils.transaction_description_builder import build_transaction_descriptions
    from shop.models import Shop
    from django.utils import timezone as django_timezone

    pdf_task = None
    start_time = time.time()

    try:
        try:
            pdf_task = PDFTask.objects.get(task_id=task_id)
        except PDFTask.DoesNotExist:
            logger.error(f"[Account History PDF] Task {task_id} not found in database")
            raise ValueError(f"PDF task {task_id} not found")

        pdf_task.mark_processing()

        logger.info(f"[Account History PDF] Starting generation for task {task_id}")

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

        account_id = request_params.get('account_id')
        account = Account.objects.filter(
            id=account_id, shop_id=shop_id, deleted__isnull=True
        ).first()
        if not account:
            raise ValueError(f"Account {account_id} not found for shop {shop_id}")

        from_date_raw = request_params.get('start_date')
        to_date_raw = request_params.get('to_date') or request_params.get('end_date')
        start_date = parse_date(from_date_raw) if isinstance(from_date_raw, str) else from_date_raw
        end_date = parse_date(to_date_raw) if isinstance(to_date_raw, str) else to_date_raw

        base_qs, ledger_qs, opening_balance, account_balance = get_account_history_data(
            account, start_date, end_date
        )

        entries = list(ledger_qs)

        running_balance = opening_balance
        for entry in entries:
            running_balance += entry.signed_amount
            entry.balance_after = running_balance

        resolve_ledger_reference_ids(entries)
        build_transaction_descriptions(entries)

        pdf_task.estimated_count = len(entries)
        pdf_task.save(update_fields=['estimated_count'])

        logger.info(f"[Account History PDF] Processing {len(entries)} entries")

        pdf_bytes = AccountHistoryPDFService.generate_account_history_pdf(
            shop_info=shop_info,
            account=account,
            entries=entries,
            opening_balance=opening_balance,
            available_balance=account_balance,
            start_date=start_date.strftime('%d/%m/%Y') if start_date else '',
            end_date=end_date.strftime('%d/%m/%Y') if end_date else '',
            shop=shop,
        )

        timestamp = django_timezone.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"pdf_reports/shop_{shop_id}/account/{task_id}_{timestamp}.pdf"

        date_part = ""
        if start_date and end_date:
            date_part = f"_{start_date.strftime('%d-%m-%Y')}_{end_date.strftime('%d-%m-%Y')}"
        download_filename = f"account_statement_{account_id}{date_part}.pdf"

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
            f"[Account History PDF] Completed task {task_id} in {generation_time:.2f}s "
            f"({len(entries)} entries, {file_size/1024:.1f}KB)"
        )

        return {
            'task_id': task_id,
            'status': 'completed',
            'download_url': download_url,
            'generation_time': generation_time,
            'entry_count': len(entries),
            'file_size': file_size,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Account History PDF] Task {task_id} failed: {error_msg}", exc_info=True)

        if pdf_task:
            pdf_task.mark_failed(error_msg)

        raise
