# celerry.py
import os
from celery import Celery
from celery.schedules import crontab
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ERP.settings')
app = Celery('ERP')
app.config_from_object('django.conf:settings', namespace='CELERY')

app.conf.update(
    # Task routing
    task_routes={
        'pdf_service.tasks.generate_ledger_pdf_task': {'queue': 'pdf_generation'},
        'pdf_service.tasks.generate_transaction_pdf_task': {'queue': 'pdf_generation'},        
        'services.tasks.generate_pdf_chunk_task': {'queue': 'pdf_chunks'},
        'services.tasks.merge_pdf_chunks_task': {'queue': 'pdf_merge'},
        'services.tasks.generate_pdf_parallel_task': {'queue': 'pdf_main'},
        'services.tasks.list_and_queue_bulk_import_task': {'queue': 'bulk_import'},
        'services.tasks.process_import_chunk_task': {'queue': 'bulk_import'},
        'services.tasks.cleanup_old_bulk_imports_task': {'queue': 'bulk_import'},
    },
    
    # Performance tuning
    worker_prefetch_multiplier=getattr(settings, 'CELERY_WORKER_PREFETCH_MULTIPLIER', 1),
    task_acks_late=True,
    worker_max_tasks_per_child=getattr(settings, 'CELERY_WORKER_MAX_TASKS_PER_CHILD', 50),
    
    # Result backend
    result_backend=settings.CELERY_RESULT_BACKEND,
    result_expires=getattr(settings, 'CELERY_RESULT_EXPIRES', 3600),
    
    # Timeouts
    broker_connection_retry_on_startup=True,

    # Task settings
    task_track_started=settings.CELERY_TASK_TRACK_STARTED,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=getattr(settings, 'CELERY_TASK_SOFT_TIME_LIMIT', int(settings.CELERY_TASK_TIME_LIMIT * 0.9)),
)

app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')