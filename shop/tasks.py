from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
import json
import logging
import traceback

logger = logging.getLogger("api_requests")

@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_bug_report_email(self, bug_id, title, description, shop_name, reporter, more_details, images):
    """
    Send bug report email to admins.
    Handles serialization and defensive type safety around payload.
    """
    try:
        subject = f"[BUG] {title or 'Untitled Bug'}"

        more_details_str = ""
        if more_details:
            if isinstance(more_details, dict):
                more_details_str = json.dumps(more_details, indent=2)
            else:
                more_details_str = str(more_details)

        images_str = ""
        if images:
            str_images = []
            for item in images:
                str_images.append(str(item))
            images_str = "\n".join(str_images)

        message = f"""
🚨 New Bug Report Added

Shop: {shop_name}
Reporter: {reporter}
Bug ID: {bug_id}

Title:
{title}

Description:
{description}

More Details:
{more_details_str}

Images:
{images_str}
""".strip()

        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=settings.ADMIN_EMAILS,
        )

        email.send(fail_silently=False)
        return True

    except Exception as e:
        logger.error(
            f"[CELERY] BUG EMAIL FAILED: {e}\n{traceback.format_exc()}"
        )
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            return False
