
from rest_framework import serializers
from subscriptions.models import Feature, FeatureUsagePolicy


class FeatureBasicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = ['id', 'name', 'code', 'feature_type']

class FeatureUsagePolicySerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source='plan.name', read_only=True)

    class Meta:
        model = FeatureUsagePolicy
        fields = ['id', 'plan', 'plan_name', 'limit', 'reset_cycle']
        read_only_fields = ['id']


class FeatureSerializer(serializers.ModelSerializer):
    requires_features = serializers.SerializerMethodField()
    requires_features_count = serializers.SerializerMethodField()
    dependent_features_count = serializers.SerializerMethodField()
    usage_policies = FeatureUsagePolicySerializer(source='featureusagepolicy_set', many=True, read_only=True)

    class Meta:
        model = Feature
        fields = [
            'id', 'name', 'code', 'description', 'feature_type', 'base_price',
            'permissions', 'usage_type', 'requires_features', 'requires_features_count',
            'dependent_features_count', 'usage_policies', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def get_requires_features(self, obj):
        """Get required features with basic info"""
        return [
            {
                'id': f.id,
                'name': f.name,
                'code': f.code,
                'feature_type': f.feature_type
            }
            for f in obj.requires_features.filter(is_active=True)
        ]

    def get_requires_features_count(self, obj):
        return obj.requires_features.filter(is_active=True).count()

    def get_dependent_features_count(self, obj):
        """Count how many features depend on this feature"""
        return Feature.objects.filter(requires_features=obj, is_active=True).count()



class FeatureShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = [
            'id', 'name', 'code', 'description', 'feature_type',
            'base_price', 'permissions', 'usage_type', 'is_active'
        ]
