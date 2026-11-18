from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import BugReport
from .tasks import send_bug_report_email
from django.db import transaction
import logging

logger = logging.getLogger("api_requests")

@receiver(post_save, sender=BugReport)
def bug_created_signal(sender, instance, created, **kwargs):
    if not created:
        return
    def send_async():
        try:
            send_bug_report_email.delay(
                bug_id=instance.id,
                title=instance.title,
                description=instance.description,
                shop_name=instance.shop.name,
                reporter=str(instance.reporter),
                more_details=instance.more_details,
                images=instance.images
            )
        except Exception as e:
            logger.error(f"BUG EMAIL QUEUE FAILED: {e}")

    transaction.on_commit(send_async)        
