from django.contrib import admin, messages
from .models import Shop, ShopUser, ShopFeaturePolicy, ShopSettings, Staff, ShopActivityLog, BugReport, Organization, ShopTaxConfiguration, ShopNumberingSequence
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import HARD_DELETE
from booking_v2.models import BookingV2
from services.models import Product
from django.utils.html import format_html
from django.urls import reverse


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


@admin.register(Shop)
class ShopAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "name", "place", "phone", "address", "deleted", "is_active", "created_at")
    search_fields = ("name", "phone", "gst_number")
    list_filter = ("created_at",)

    readonly_fields = ("view_bookings_button", "view_products_button", "created_at", "s3_folder_name")

    fieldsets = (
        (None, {
            'fields': ('name', 'place', 'phone', 'phone2', 'address', "email", "city", "state", "pincode", "organization", "gst_number", "password", "password_changed_at", "extra_stock_limit", "booking_start_id","last_booking_id", "sale_start_id", "last_sale_id", 'tailor_start_id', 'last_tailor_id', 'last_product_number', 'is_active', "subscription_renewal_price", "extra_user_limit", "terms_and_conditions", "slug", "public_token", "s3_folder_name", "created_at")
        }),
        ('Bookings', {
            'fields': ('view_bookings_button',)
        }),
        ('Products', {
            'fields': ('view_products_button',)
        }),
    )
    
    def view_bookings_button(self, obj):
        """Display a button that links to filtered booking list for this shop"""
        if obj.pk:  # Only show if shop is saved (has an ID)
            url = (
                reverse("admin:booking_v2_bookingv2_changelist")  # app_label:modelname_changelist
                + f"?shop__id__exact={obj.id}"  # filter only this shop's bookings
            )
            # Count bookings for this shop
            booking_count = BookingV2.objects.filter(shop=obj).count()
            return format_html(
                '<a class="button default" href="{}" style="padding: 8px 16px; '
                'background-color: #417690; color: white; text-decoration: none; '
                'border-radius: 4px; display: inline-block; margin: 5px 0;">'
                'View Bookings ({})</a>', 
                url, 
                booking_count
            )
        return "Save the shop first to view bookings"
    
    view_bookings_button.short_description = "Bookings"
    view_bookings_button.allow_tags = True

    def view_products_button(self, obj):
        """Display a button that links to filtered product list for this shop."""
        if obj.pk:
            url = (
                reverse("admin:services_product_changelist")
                + f"?shop_service__shop__id__exact={obj.id}"
            )
            # Active count only; list page still contains all products for the shop.
            active_product_count = Product.objects.filter(
                shop_service__shop=obj,
                is_active=True,
            ).count()

            return format_html(
                '<a class="button default" href="{}" style="padding: 8px 16px; '
                'background-color: #417690; color: white; text-decoration: none; '
                'border-radius: 4px; display: inline-block; margin: 5px 0;">'
                'View Products ({})</a>',
                url,
                active_product_count,
            )
        return "Save the shop first to view products"

    view_products_button.short_description = "Products"
    view_products_button.allow_tags = True


@admin.register(ShopUser)
class ShopUserAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "user", "shop", "role", "deleted", "is_active", "created_at")
    search_fields = ("user__full_name", "user__phone", "shop__name")
    list_filter = ("created_at",)


@admin.register(ShopFeaturePolicy)
class ShopFeaturePolicyAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "shop", "feature", "require_password_for", "is_active", "created_at", "updated_at")
    list_filter = ("feature", "require_password_for", "is_active", "created_at")
    search_fields = ("shop__name", "feature")
    ordering = ("-created_at",)

@admin.register(ShopSettings)
class ShopSettingsAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "shop", "cooling_days", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("shop__name", "key")
    ordering = ("-created_at",)

@admin.register(ShopTaxConfiguration)
class ShopTaxConfigurationAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "shop", "tax_name", "tax_rate", "is_enabled", "tax_calculation_type", "created_at", "updated_at")
    list_filter = ("is_enabled", "tax_name", "created_at")
    search_fields = ("shop__name", "tax_name")
    ordering = ("-created_at",)

@admin.register(ShopNumberingSequence)
class ShopNumberingSequenceAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id", "shop", "booking_start_id", "last_booking_id", "sale_start_id",
        "last_sale_id", "tailor_start_id", "last_tailor_id", "purchase_start_id",
        "last_purchase_id", "last_product_number", "created_at", "updated_at",
    )
    list_filter = ("created_at",)
    search_fields = ("shop__name",)
    ordering = ("-created_at",)


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "shop","is_deleted", "created_at")
    list_filter = ("shop", "created_at")
    search_fields = ("name", "phone", "shop__name")
    ordering = ("-created_at",)


@admin.register(ShopActivityLog)
class ShopActivityLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "shop",
        "user",
        "action",
        "content_type",
        "object_id",
        "created_at",
    )
    list_filter = ("action", "shop", "content_type", "created_at")
    search_fields = (
        "shop__name",
        "user__username",
        "action",
        "log_message",
        "content_type__model",
    )
    readonly_fields = (
        "shop",
        "user",
        "action",
        "content_type",
        "object_id",
        "log_message",
        "created_at",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "shop",
        "reporter",
        "title",
        "status",
        "created_at",
        "resolved_at",
    )
    list_filter = ("status", "shop", "created_at")
    search_fields = ("title", "description", "shop__name", "reporter__phone")
    ordering = ("-created_at",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "public_token", "is_active", "public_listing_enabled", "created_at")
    search_fields = ("name", "slug", "public_token")
    list_filter = ("is_active", "public_listing_enabled", "created_at")
    readonly_fields = ("slug", "public_token", "created_at")
    ordering = ("-created_at",)