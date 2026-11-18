from rest_framework import serializers
from subscriptions.models import SubscriptionPlan
from .feature import FeatureBasicSerializer, FeatureShopSerializer


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    included_features = FeatureBasicSerializer(many=True, read_only=True)
    included_feature_count = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'plan_type', 'description', 'base_price',
            'max_users', 'max_products', 'max_bookings_per_month',
            'duration_days', 'included_features', 'included_feature_count',
            'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def get_included_feature_count(self, obj):
        return obj.included_features.count()


class SubscriptionPlanShopSerializer(serializers.ModelSerializer):
    included_features = FeatureShopSerializer(many=True, read_only=True)

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'plan_type', 'description', 'base_price',
            'max_users', 'max_products', 'max_bookings_per_month',
            'duration_days', 'included_features', 'is_active'
        ]
