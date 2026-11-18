import logging

from celery import shared_task

from .expiry_task_common import execute_expiry_notification_task

logger = logging.getLogger("api_requests")


@shared_task
def check_insurance_expiry_notifications():
    """Daily reminders for insurance expiry within the next 7 days."""
    return execute_expiry_notification_task(
        expiry_attr_key="insurance_expiry",
        expiry_type="insurance_expiry",
        title_prefix="Insurance Expiry",
    )
