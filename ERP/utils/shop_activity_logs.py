from django.contrib.contenttypes.models import ContentType
from shop.models import ShopActivityLog

def create_activity_log(
    *,
    shop,
    user=None,
    instance=None,
    action="",
    message="",
):
    """
    Args:
        shop (Shop): The shop related to the action.
        user (User, optional): The user performing the action.
        instance (Model instance, optional): The affected model instance (e.g. Product, Booking, Sale).
        action (str): Action performed (e.g., "created", "updated", "deleted").
        message (str): Human-readable description of the action.

    Returns:
        ShopActivityLog: Created log instance.
    """

    if not shop or not action:
        raise ValueError("Both 'shop' and 'action' are required to create an activity log.")

    log_data = {
        "shop": shop,
        "user": user,
        "action": action,
        "log_message": message,
    }

    if instance:
        content_type = ContentType.objects.get_for_model(instance.__class__)
        log_data.update({
            "content_type": content_type,
            "object_id": instance.pk,
        })

    return ShopActivityLog.objects.create(**log_data)
