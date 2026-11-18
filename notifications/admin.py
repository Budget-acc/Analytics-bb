from django.contrib import admin
from .models import DeviceToken, Notification, UserNotification

@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'token_short', 'platform', 'is_active', 'created_at', 'updated_at')
    list_filter = ('platform', 'is_active', 'created_at')
    search_fields = ('user__username', 'token')

    def token_short(self, obj):
        return obj.token[:25] + "..." if len(obj.token) > 25 else obj.token
    token_short.short_description = "Token"

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "notification_type", "priority", "target", "shop", "created_by", "created_at", "sent_at")
    search_fields = ("title", "body", "target")
    list_filter = ("notification_type", "priority", "target", "created_at", "sent_at")

@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "notification", "shop", "is_read", "push_status", "received_at", "read_at")
    list_filter = ("is_read", "is_deleted", "push_status", "received_at")
    search_fields = ("user__username", "notification__title")


