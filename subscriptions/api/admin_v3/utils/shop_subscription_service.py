import datetime
from django.utils import timezone
from django.db import transaction
from django.utils.timezone import make_aware, is_naive

from subscriptions.models import (
    ShopSubscription,
    ShopFeatureSubscription,
    SubscriptionPlan,
    Feature,
)


class ShopSubscriptionService:
    @staticmethod
    def _ensure_aware(dt):
        """Ensure datetime is timezone-aware to prevent comparison errors"""
        if dt is None:
            return None
        if is_naive(dt):
            return make_aware(dt)
        return dt

    @staticmethod
    @transaction.atomic
    def subscribe_shop_to_plan(shop, plan, payment_status=False, addon_features=None, bundles=None, start_date=None, end_date=None, plan_price_paid=None, auto_renew=False):
        """
        Subscribe a shop to a plan with optional add-on features and bundles

        Args:
            shop: Shop model instance
            plan: SubscriptionPlan model instance
            payment_status: Boolean indicating if payment has been made
            addon_features: List of (feature, price_paid, end_date) tuples for add-on features
            bundles: List of (bundle, price_paid, selected_features, end_date) tuples for bundles
            start_date: Optional start date (defaults to now)
            end_date: Optional end date (defaults to start_date + plan.duration_days)
            plan_price_paid: Optional custom price paid for plan (defaults to plan.base_price)
            auto_renew: Boolean for auto-renewal (defaults to False)

        Returns:
            ShopSubscription instance
        """

        # Use provided start date or default to now
        if start_date is None:
            start_date = timezone.now()
        else:
            start_date = ShopSubscriptionService._ensure_aware(start_date)

        if end_date is None:
            end_date = start_date + datetime.timedelta(days=plan.duration_days)
        else:
            end_date = ShopSubscriptionService._ensure_aware(end_date)

        if end_date <= start_date:
            raise ValueError("end_date must be after start_date")

        plan_price_paid = plan_price_paid if plan_price_paid is not None else plan.base_price

        # Start total with this price
        total_amount = plan_price_paid

        # Create or update shop subscription
        if hasattr(shop, 'subscription'):
            # Update existing subscription
            shop_subscription = shop.subscription
            shop_subscription.plan = plan
            shop_subscription.status = "ACTIVE"
            shop_subscription.start_date = start_date
            shop_subscription.end_date = end_date
            shop_subscription.total_amount = plan_price_paid
            shop_subscription.payment_status = payment_status
            shop_subscription.auto_renew = auto_renew
        else:
            # Create new subscription
            shop_subscription = ShopSubscription(
                shop=shop,
                plan=plan,
                status="ACTIVE",
                start_date=start_date,
                end_date=end_date,
                total_amount=plan_price_paid,
                payment_status=payment_status,
                auto_renew=auto_renew
            )

        shop_subscription.save()

        # Add add-on features
        if addon_features:
            for feature, price_paid, feature_end_date in addon_features:
                # Use plan end date if no specific date provided
                if feature_end_date:
                    feature_end_date = ShopSubscriptionService._ensure_aware(feature_end_date)
                else:
                    feature_end_date = end_date

                if feature_end_date < start_date:
                    raise ValueError(f"Feature {feature.name} end_date cannot be before subscription start_date")

                # Create feature subscription
                ShopFeatureSubscription.objects.update_or_create(
                    shop_subscription=shop_subscription,
                    feature=feature,
                    defaults={
                        'start_date': start_date,
                        'end_date': feature_end_date,
                        'price_paid': price_paid,
                        'base_price_at_purchase': feature.base_price,
                        'discount_amount': max(0, feature.base_price - price_paid),
                        'is_active': True
                    }
                )

                total_amount += price_paid

        # Update total amount
        shop_subscription.total_amount = total_amount
        shop_subscription.save()

        # Update shop subscription status fields
        shop.subscription_status = "ACTIVE"
        shop.subscribed_date = start_date.date()
        shop.renewal_due_date = end_date.date()
        shop.save(update_fields=['subscription_status', 'subscribed_date', 'renewal_due_date'])

        return shop_subscription

    @staticmethod
    def cancel_shop_subscription(shop_subscription):
        """Cancel a shop's subscription"""
        shop_subscription.status = "CANCELLED"
        shop_subscription.save()

        # Update shop model
        shop = shop_subscription.shop
        shop.subscription_status = "NONE"
        shop.save(update_fields=['subscription_status'])

        return shop_subscription

    @staticmethod
    def add_feature_to_subscription(shop_subscription, feature, price_paid=None, start_date=None, end_date=None):
        """Add a feature to an existing subscription"""
        if price_paid is None:
            price_paid = feature.base_price

        if start_date is None:
            start_date = timezone.now()
        else:
            start_date = ShopSubscriptionService._ensure_aware(start_date)

        if end_date is None:
            end_date = shop_subscription.end_date
        else:
            end_date = ShopSubscriptionService._ensure_aware(end_date)

        feature_sub, created = ShopFeatureSubscription.objects.update_or_create(
            shop_subscription=shop_subscription,
            feature=feature,
            defaults={
                'start_date': start_date,
                'end_date': end_date,
                'price_paid': price_paid,
                'base_price_at_purchase': feature.base_price,
                'discount_amount': max(0, feature.base_price - price_paid),
                'is_active': True
            }
        )

        # Update total amount if created new
        if created:
            shop_subscription.total_amount += price_paid
            shop_subscription.save(update_fields=['total_amount'])

        return feature_sub
