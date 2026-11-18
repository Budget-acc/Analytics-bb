from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import HARD_DELETE
from shop.models import Shop
from .models import (
    SubscriptionPlan, Feature, ShopSubscription,
    UserSubscriptionAccess, ShopFeatureSubscription,
    ShopUserFeature, UserFeature,
    FeatureUsagePolicy, ShopFeatureLimit, ShopFeatureUsage
)


class CommonSafeDeleteAdmin(SafeDeleteAdmin):
    actions = SafeDeleteAdmin.actions + ("hard_delete_selected",)

    @admin.action(description="Hard delete selected objects")
    def hard_delete_selected(self, request, queryset):
        if request.POST.get("post"):
            count = 0
            for obj in queryset:
                obj.delete(force_policy=HARD_DELETE)
                count += 1

            self.message_user(
                request,
                ngettext(
                    "%d item was permanently deleted.",
                    "%d items were permanently deleted.",
                    count,
                )
                % count,
                messages.SUCCESS,
            )
            return None

        return TemplateResponse(
            request,
            "safedelete/hard_delete_selected_confirmation.html",
            {
                "title": "Are you sure?",
                "objects_name": "items",
                "deletable_objects": queryset,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "action": "hard_delete_selected",
            },
        )


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "name", "plan_type", "base_price", "duration_days", 
        "max_users", "max_products", "max_bookings_per_month", 
        "is_active", "deleted", "created_at"
    )
    list_filter = ("plan_type", "is_active", "created_at")
    search_fields = ("name", "description")
    filter_horizontal = ("included_features",)
    fieldsets = (
        ("Basic Information", {
            "fields": ("name", "plan_type", "description", "base_price")
        }),
        ("Plan Limits", {
            "fields": ("max_users", "max_products", "max_bookings_per_month", "duration_days")
        }),
        ("Features", {
            "fields": ("included_features",)
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
    )


@admin.register(Feature)
class FeatureAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "name", "code", "feature_type", "base_price", 
        "is_active", "deleted", "created_at"
    )
    list_filter = ("feature_type", "is_active", "created_at")
    search_fields = ("name", "code", "description")
    filter_horizontal = ("requires_features",)
    fieldsets = (
        ("Basic Information", {
            "fields": ("name", "code", "description", "feature_type")
        }),
        ("Pricing", {
            "fields": ("base_price",)
        }),
        ("Configuration", {
            "fields": ("permissions", "requires_features")
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
    )

@admin.register(ShopSubscription)
class ShopSubscriptionAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "shop", "plan", "status", "start_date", "end_date",
        "total_amount", "payment_status", "auto_renew", "deleted", "created_at"
    )
    list_filter = ("status", "payment_status", "auto_renew", "created_at")
    search_fields = ("shop__name", "plan__name")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Subscription Details", {
            "fields": ("shop", "plan", "status")
        }),
        ("Duration", {
            "fields": ("start_date", "end_date")
        }),
        ("Payment", {
            "fields": ("total_amount", "payment_status", "auto_renew")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )


class ShopFilter(admin.SimpleListFilter):
    title = "shop"
    parameter_name = "shop"

    def lookups(self, _request, _model_admin):
        shops = (
            Shop.objects.filter(subscription__isnull=False)
            .distinct()
            .order_by("name")
            .values_list("id", "name")
        )
        return list(shops)

    def queryset(self, _request, queryset):
        if self.value():
            return queryset.filter(shop_subscription__shop_id=self.value())
        return queryset


@admin.register(ShopFeatureSubscription)
class ShopFeatureSubscriptionAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "get_shop_name", "feature", "price_paid", "discount_amount",
        "start_date", "end_date", "is_active", "deleted", "created_at"
    )
    list_filter = ("is_active", ShopFilter, "feature", "created_at")
    search_fields = (
        "shop_subscription__shop__name", "feature__name",
        "discount_reason"
    )
    list_select_related = ("shop_subscription__shop", "feature")
    readonly_fields = ("base_price_at_purchase", "created_at")
    fieldsets = (
        ("Subscription Details", {
            "fields": ("shop_subscription", "feature")
        }),
        ("Duration", {
            "fields": ("start_date", "end_date", "is_active")
        }),
        ("Pricing", {
            "fields": ("price_paid", "discount_amount", "discount_reason", "base_price_at_purchase")
        }),
        ("Timestamps", {
            "fields": ("created_at",),
            "classes": ("collapse",)
        }),
    )

    @admin.display(description="Shop", ordering="shop_subscription__shop__name")
    def get_shop_name(self, obj):
        return obj.shop_subscription.shop.name

@admin.register(UserSubscriptionAccess)
class UserSubscriptionAccessAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "shop_user", "max_bookings_per_day", "can_view_analytics",
        "can_manage_users", "deleted", "created_at"
    )
    list_filter = ("can_view_analytics", "can_manage_users", "created_at")
    search_fields = (
        "shop_user__user__full_name", "shop_user__user__phone",
        "shop_user__shop__name"
    )
    filter_horizontal = ("allowed_features", "restricted_features")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("User", {
            "fields": ("shop_user",)
        }),
        ("Feature Access", {
            "fields": ("allowed_features", "restricted_features")
        }),
        ("Access Limits", {
            "fields": ("max_bookings_per_day", "can_view_analytics", "can_manage_users")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )
   
@admin.register(UserFeature)
class UserFeatureAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "role", "feature")
    list_filter = ("role",)
    search_fields = ("feature__code", "feature__name")
    autocomplete_fields = ("feature",)


@admin.register(ShopUserFeature)
class ShopUserFeatureAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "shop_user", "feature", "granted")
    list_filter = ("granted", "feature")
    search_fields = ("shop_user__user__username", "shop_user__user__email", "feature__code")
    autocomplete_fields = ("shop_user", "feature")


@admin.register(FeatureUsagePolicy)
class FeatureUsagePolicyAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "plan", "feature", "limit", "reset_cycle", 
        "deleted"
    )
    list_filter = ("reset_cycle",)
    search_fields = ("plan__name", "feature__name", "feature__code")
    autocomplete_fields = ("plan", "feature")
    fieldsets = (
        ("Policy Details", {
            "fields": ("plan", "feature")
        }),
        ("Limits", {
            "fields": ("limit", "reset_cycle")
        }),
    )


@admin.register(ShopFeatureLimit)
class ShopFeatureLimitAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "shop", "feature", "extra_limit", "valid_until", 
        "is_active", "reason", "deleted"
    )
    list_filter = ("is_active",)
    search_fields = ("shop__name", "feature__name", "feature__code", "reason")
    autocomplete_fields = ("shop", "feature")
    fieldsets = (
        ("Shop & Feature", {
            "fields": ("shop", "feature")
        }),
        ("Limit Adjustment", {
            "fields": ("extra_limit", "reason")
        }),
        ("Validity", {
            "fields": ("valid_until", "is_active")
        }),
    )


@admin.register(ShopFeatureUsage)
class ShopFeatureUsageAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "shop", "feature", "used", "period_start", 
        "period_end", "deleted"
    )
    list_filter = ("feature", "period_start", "period_end")
    search_fields = ("shop__name", "feature__name", "feature__code")
    autocomplete_fields = ("shop", "feature")
    date_hierarchy = "period_start"
    fieldsets = (
        ("Shop & Feature", {
            "fields": ("shop", "feature")
        }),
        ("Usage Data", {
            "fields": ("used", "period_start", "period_end")
        }),
    )
    
    def get_readonly_fields(self, request, obj=None):
        # Make all fields except 'used' readonly when editing
        if obj:
            return ("shop", "feature", "period_start", "period_end")
        return ()    