# booking_v2/tasks/__init__.py
# Re-export all tasks so callers can keep using:
#   from booking_v2.tasks import cleanup_s3_documents_task, ...

from .document_tasks import cleanup_s3_documents_task, upload_booking_documents_task
from .thumbnail_tasks import generate_booking_thumbnails_task
from .cleanup_tasks import cleanup_old_temp_files, cleanup_old_booking_documents
from .utils import cleanup_orphaned_temp_files

__all__ = [
    "cleanup_s3_documents_task",
    "upload_booking_documents_task",
    "generate_booking_thumbnails_task",
    "cleanup_old_temp_files",
    "cleanup_old_booking_documents",
    "cleanup_orphaned_temp_files",
]
