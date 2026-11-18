from .base_user_notification_viewset import BaseUserNotificationViewSet
from .user_notification_clicked import UserNotificationClickedMixin
from .user_notification_read_all import UserNotificationReadAllMixin
from .user_notification_destroy import UserNotificationDestroyMixin
from .user_notification_delete_all import UserNotificationDeleteAllMixin


class UserNotificationViewSet(
    UserNotificationClickedMixin,
    UserNotificationReadAllMixin,
    UserNotificationDestroyMixin,
    UserNotificationDeleteAllMixin,
    BaseUserNotificationViewSet,
):
    """
    Composed ViewSet — inherits base + all action mixins.
    API endpoints remain unchanged.
    """
    pass
