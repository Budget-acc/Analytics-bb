from django.utils import timezone


def _build_log_item(self, log):
    """Build basic log item structure"""
    item = {
        "id": log.id,
        "action": log.action,
        "message": log.log_message,
        "user": log.user.full_name if log.user else "Unknown",
        "object_type": log.content_type.model if log.content_type else None,
        "object_id": log.object_id,
        "created_at": timezone.localtime(log.created_at).strftime("%d-%m-%Y %H:%M:%S"),
        # True = already restored (hide Restore button), False = restorable, None = not a delete log
        "is_restored": bool(log.restore_logs.all()) if log.action == "deleted" else None,
        # For "restored" logs: ID of the original delete log this was restored from
        "restored_from_activity_id": log.restored_from_activity_id if log.action == "restored" else None,
    }
    return item
