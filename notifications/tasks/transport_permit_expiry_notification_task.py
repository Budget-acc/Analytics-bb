import logging

from celery import shared_task

from .expiry_task_common import execute_expiry_notification_task

logger = logging.getLogger("api_requests")


@shared_task
def check_transport_permit_expiry_notifications():
    """Daily reminders for transport permit expiry within the next 7 days."""
    return execute_expiry_notification_task(
        expiry_attr_key="transport_permit_expiry",
        expiry_type="transport_permit_expiry",
        title_prefix="Vehicle Permit Expiry",
    )
