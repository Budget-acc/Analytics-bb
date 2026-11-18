from rest_framework import serializers
from subscriptions.models import ShopSubscription, ShopFeatureSubscription, ShopUserFeature
from .feature import FeatureShopSerializer
from .subscription_plan import SubscriptionPlanShopSerializer


class ShopFeatureSubscriptionSerializer(serializers.ModelSerializer):
    feature = FeatureShopSerializer(read_only=True)

    class Meta:
        model = ShopFeatureSubscription
        fields = [
            'id', 'feature', 'start_date', 'end_date', 'price_paid',
            'discount_amount', 'discount_reason', 'is_active'
        ]


class ShopSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanShopSerializer(read_only=True)
    addon_features = ShopFeatureSubscriptionSerializer(
        source='shopfeaturesubscription_set',
        many=True,
        read_only=True
    )

    class Meta:
        model = ShopSubscription
        fields = [
            'id', 'shop', 'plan', 'status', 'start_date', 'end_date',
            'addon_features', 'total_amount', 'payment_status',
            'auto_renew', 'created_at', 'updated_at'
        ]


class ShopUserFeatureSerializer(serializers.ModelSerializer):
    feature_code = serializers.CharField(source="feature.code", read_only=True)
    shop_user_name = serializers.CharField(source="shop_user.user.username", read_only=True)

    class Meta:
        model = ShopUserFeature
        fields = [
            "id",
            "shop_user",
            "feature",
            "granted",
            "feature_code",
            "shop_user_name",
        ]
