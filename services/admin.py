from django.contrib import admin, messages
from .models import (
    GeneralService,
    ShopOwnerService,
    Product,
    ProductVariant,
    MainServiceCategory,
    ProductTransferHistory,
    PDFGenerationTask,
    ServiceAttributeTemplate,
    ServiceAttributeValue
)
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import HARD_DELETE
from django.utils.translation import ngettext
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join


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


@admin.register(MainServiceCategory)
class MainServiceCategoryAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "name", "is_active", "deleted", "created_at")
    search_fields = ("name",)
    list_filter = ("is_active",)


@admin.register(GeneralService)
class GeneralServiceAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "name",
        "main_category",
        "icon",
        "description",
        "is_active",
        "deleted",
        "created_at",
    )
    search_fields = ("name",)
    list_filter = ("is_active", "deleted")


@admin.register(ShopOwnerService)
class ShopOwnerServiceAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "shop",
        "general_service",
        "is_active",
        "deleted",
        "created_at",
    )
    search_fields = ("shop__name", "general_service__name")
    list_filter = ("is_active", "deleted")


@admin.register(Product)
class ProductAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "name",
        "shop_service",
        "price",
        "color",
        "model",
        "category",
        "product_code",
        "is_active",
        "deleted",
        "created_at",
    )
    search_fields = ("name", "color", "model", "category")
    list_filter = ("is_active", "category", "deleted")
    readonly_fields = ("created_at", "view_bookings_button", "variant_details", "product_code")

    fieldsets = (
        (None, {
            'fields': (
                'shop_service', 'name', 'description', 'price',
                'color', 'model', 'category', "product_code",
                'image', 'thumbnail_image', 'is_active',
                'purchase_price'
            )
        }),
        ('Variants', {
            'fields': ('variant_details',)
        }),
        ('Readonly Info', {
            'fields': ('created_at',)
        }),
        ('Bookings', {
            'fields': ('view_bookings_button',)
        }),
    )
    def variant_details(self, obj):
        """Display all variants for this product in a table format, escaping user input."""
        variants = obj.variants.all()
        if not variants:
            return "No variants."
        header = format_html(
            '<table style="border-collapse:collapse; width:100%;">'
            '<tr style="background:#f5f5f5;"><th>ID</th><th>Attribute</th><th>Price</th><th>Stock</th><th>Fabric Length</th><th>Is Active</th></tr>'
        )
        rows = [
            format_html(
                '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>',
                v.id, v.attribute, v.price, v.stock, v.fabric_length, "Yes" if v.is_active else "No"
            ) for v in variants
        ]
        footer = format_html('</table>')
        return format_html('{}{}{}', header, format_html_join('', '{}', ((row,) for row in rows)), footer)

    variant_details.short_description = "Product Variants"
    variant_details.allow_tags = True

    def view_bookings_button(self, obj):
        """Display a button that links to filtered booking list for this product"""
        from booking_v2.models import BookingV2 
        if obj.pk:
            url = (
                reverse("admin:booking_v2_bookingv2_changelist")
                + f"?booked_items__product_variant__product__id__exact={obj.id}"
            )
            booking_count = BookingV2.objects.filter(
                booked_items__product_variant__product=obj
            ).count()

            return format_html(
                '<a class="button default" href="{}" style="padding: 8px 16px; '
                'background-color: #417690; color: white; text-decoration: none; '
                'border-radius: 4px; display: inline-block; margin: 5px 0;">'
                'View Bookings ({})</a>', 
                url, 
                booking_count
            )
        return "Save the product first to view bookings"

    view_bookings_button.short_description = "Bookings"
    view_bookings_button.allow_tags = True

    def lookup_allowed(self, lookup, value, request=None):
        # Allow filtering Product changelist by parent shop from custom Shop admin button.
        if lookup == "shop_service__shop__id__exact":
            return True
        return super().lookup_allowed(lookup, value, request=request)

@admin.register(ProductVariant)
class ProductVariantAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "product",
        # "get_shop",
        "shop",
        "attribute",
        "stock",
        "fabric_length",
        "is_active",
        "deleted",
        "created_at",
    )
    search_fields = ("product__name", "attribute")
    list_filter = ("is_active", "deleted")
    readonly_fields = ("variant_bookings", "created_at")

    fieldsets = (
        (None, {
            'fields': (
                'product', 'shop', 'attribute', 'price', 'stock', 'fabric_length', 'external_qr_code', 'is_active', 'variant_bookings'
            )
        }),
        ('Readonly Info', {
            'fields': ('created_at',)
        }),
    )

    def variant_bookings(self, obj):
        """Display only a button to view all bookings for this variant in Booking admin."""
        from booking_v2.models import BookingV2
        bookings_count = BookingV2.objects.filter(booked_items__product_variant=obj).distinct().count()
        if bookings_count == 0:
            return "No bookings."
        url = (
            reverse("admin:booking_v2_bookingv2_changelist")
            + f"?booked_items__product_variant__id__exact={obj.id}"
        )
        return format_html(
            '<a class="button default" href="{}" style="padding: 8px 16px; background-color: #417690; color: white; text-decoration: none; border-radius: 4px; display: inline-block; margin: 5px 0;">View All Bookings ({})</a>',
            url,
            bookings_count
        )

    variant_bookings.short_description = "Bookings for this Variant"
    variant_bookings.allow_tags = True

@admin.register(ProductTransferHistory)
class ProductTransferHistoryAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "transfer_id",
        "status",
        "source_shop",
        "source_product",
        "source_variant",
        "destination_shop",
        "destination_product",
        "destination_variant",
        "quantity_transferred",
        "transfer_type",
        "initiated_by",
        "transfer_date",
    )
    list_filter = ("status", "transfer_type", "source_shop", "destination_shop", "transfer_date")
    search_fields = ("transfer_id", "source_product__name", "destination_product__name", "initiated_by__full_name")
    readonly_fields = ("transfer_id", "transfer_date")

@admin.register(PDFGenerationTask)
class PDFGenerationTaskAdmin(CommonSafeDeleteAdmin):    
    list_display = (
        "task_id",
        "status",
        "created_at",
        "completed_at",
        "url_expires_at",
        "s3_key",
        "download_url",
    )
    list_filter = ("status", "created_at", "completed_at", "url_expires_at")
    search_fields = ("task_id", "s3_key")

@admin.register(ServiceAttributeTemplate)
class ServiceAttributeTemplateAdmin(admin.ModelAdmin):
    list_display = ("attribute_key", "general_service", "attribute_type", "applies_to")

@admin.register(ServiceAttributeValue)
class ServiceAttributeValueAdmin(admin.ModelAdmin):
    list_display = ("product", "variant", "attribute", "value")    


from .models import ProductVariantStockAdjustment


@admin.register(ProductVariantStockAdjustment)
class ProductVariantStockAdjustmentAdmin(admin.ModelAdmin):
    """Read-only: the history is written only by the stock-adjustment API."""
    list_display = (
        "id", "created_at", "shop", "product", "variant", "action", "reason",
        "quantity", "fabric_length", "updated_stock", "updated_unavailable_stock", "created_by",
    )
    list_filter = ("action", "reason", "created_at")
    search_fields = ("product__name", "variant__attribute", "note", "batch_id")
    list_select_related = ("shop", "product", "variant__product", "created_by")
    raw_id_fields = ("shop", "product", "variant", "created_by")
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
