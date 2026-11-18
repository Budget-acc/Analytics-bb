from rest_framework import serializers
from shop.models import BugReport


class BugReportAdminListSerializer(serializers.ModelSerializer):
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    reporter_phone = serializers.CharField(source="reporter.phone", read_only=True)
    reporter_name = serializers.CharField(source="reporter.full_name", read_only=True)

    class Meta:
        model = BugReport
        fields = (
            "id",
            "shop_id",
            "shop_name",
            "reporter_id",
            "reporter_name",
            "reporter_phone",
            "title",
            "status",
            "created_at",
        )


class BugReportAdminDetailSerializer(serializers.ModelSerializer):
    shop = serializers.SerializerMethodField()
    reporter = serializers.SerializerMethodField()

    class Meta:
        model = BugReport
        fields = (
            "id",
            "shop",
            "reporter",
            "title",
            "description",
            "more_details",
            "images",
            "status",
            "resolved_at",
            "created_at",
        )

    def get_shop(self, obj):
        return {"id": obj.shop_id, "name": getattr(obj.shop, "name", None)}

    def get_reporter(self, obj):
        if not obj.reporter:
            return None
        return {
            "id": obj.reporter_id,
            "full_name": obj.reporter.full_name,
            "phone": str(obj.reporter.phone) if obj.reporter.phone else None,   # FIX
        }


class BugReportStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=BugReport.STATUS_CHOICES)

    def validate_status(self, value):
        allowed = {choice[0] for choice in BugReport.STATUS_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError("Invalid status value.")
        return value
