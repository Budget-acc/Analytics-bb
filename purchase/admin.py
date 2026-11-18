from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import SafeDeleteModel, HARD_DELETE

from .models import (
    Purchase,
    PurchaseItem,
    StockMovement,
    AdditionalCharge,
    PurchasePayment,
    Vendor,
)


class CommonSafeDeleteAdmin(SafeDeleteAdmin):
    actions = SafeDeleteAdmin.actions + ("hard_delete_selected",)

    @admin.action(description="Hard delete selected objects")
    def hard_delete_selected(self, request, queryset):
        if request.POST.get("post"):
            count = 0
            for obj in queryset:
                if isinstance(obj, SafeDeleteModel):
                    obj.delete(force_policy=HARD_DELETE)
                else:
                    obj.delete()
                count += 1

            self.message_user(
                request,
                ngettext(
                    "%d item was permanently deleted.",
                    "%d items were permanently deleted.",
                    count,
                ) % count,
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


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0
    fields = (
        "product_variant",
        "product_name",
        "variant_size",
        "variant_color",
        "variant_other",
        "quantity",
        "purchase_amount",
        "line_total",
        "stock_increased",
    )
    readonly_fields = ("line_total",)
    can_delete = False
    show_change_link = True


class AdditionalChargeInline(admin.TabularInline):
    model = AdditionalCharge
    extra = 0
    fields = ("charge_name", "amount", "account", "payment_method", "created_at")
    readonly_fields = ("created_at",)
    can_delete = False
    show_change_link = True


class PurchasePaymentInline(admin.TabularInline):
    model = PurchasePayment
    extra = 0
    fields = ("amount", "account", "payment_method", "payment_date", "transaction_reference", "created_at")
    readonly_fields = ("created_at",)
    can_delete = False
    show_change_link = True


@admin.register(Purchase)
class PurchaseAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "purchase_code",
        "shop",
        "vendor",
        "purchase_type",
        "purchase_date",
        "grand_total",
        "paid_amount",
        "is_completed",
        "deleted",
        "created_at",
    )
    search_fields = ("purchase_code", "invoice_number", "vendor__name")
    list_filter = ("purchase_type", "is_completed", "shop", "created_at")
    inlines = [PurchaseItemInline, AdditionalChargeInline, PurchasePaymentInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("shop", "vendor")


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "purchase",
        "product_name",
        "variant_other",
        "quantity",
        "purchase_amount",
        "line_total",
        "stock_increased",
        "created_at",
    )
    search_fields = ("product_name", "purchase__purchase_code")
    list_filter = ("stock_increased", "created_at")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "shop",
        "product_variant",
        "purchase",
        "movement_type",
        "previous_stock",
        "quantity_changed",
        "new_stock",
        "created_at",
    )
    search_fields = ("reference_number", "product_variant__product__product_name")
    list_filter = ("movement_type", "shop", "created_at")


@admin.register(AdditionalCharge)
class AdditionalChargeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "purchase",
        "charge_name",
        "amount",
        "account",
        "payment_method",
        "created_at",
    )
    search_fields = ("charge_name", "purchase__purchase_code")
    list_filter = ("payment_method", "created_at")


@admin.register(PurchasePayment)
class PurchasePaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "purchase",
        "amount",
        "account",
        "payment_method",
        "payment_date",
        "created_at",
    )
    search_fields = ("purchase__purchase_code", "transaction_reference")
    list_filter = ("payment_method", "created_at")


@admin.register(Vendor)
class VendorAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "shop",
        "name",
        "contact_number",
        "email",
        "location",
        "is_active",
        "deleted",
        "created_at",
    )
    search_fields = ("name", "contact_number", "email", "gst_number")
    list_filter = ("is_active", "shop", "created_at")
