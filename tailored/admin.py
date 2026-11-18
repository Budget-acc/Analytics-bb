from django.contrib import admin, messages
from .models import (
    Tailor,
    TailorOrder,
    TailorOrderItem,
    TailorMaterialUsage,
    TailorPayment,
    TailorOrderAdditionalCharge,
    CustomerPayment,
    TailorOrderSecurityPayment,
    TailorOrderRefund,
    TailorOrderSecurityDepositAdjustment,
    TailorOrderItemAssignment,
)
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import HARD_DELETE


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


@admin.register(Tailor)
class TailorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "shop","is_deleted", "created_at")
    list_filter = ("shop", "created_at")
    search_fields = ("name", "phone", "shop__name")
    ordering = ("-created_at",)




# ------------------- INLINE CLASSES -------------------

class TailorOrderItemInline(admin.TabularInline):
    model = TailorOrderItem
    extra = 0
    readonly_fields = ('created_at',)
    show_change_link = True


class TailorOrderItemAssignmentInline(admin.TabularInline):
    model = TailorOrderItemAssignment
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    fields = ('tailor', 'tailor_charge', 'tailoring_charge_from_customer', 'expected_completion_date', 'actual_completion_date', 'created_at', 'updated_at')


class TailorOrderAdditionalChargeInline(admin.TabularInline):
    model = TailorOrderAdditionalCharge
    extra = 0
    readonly_fields = ('created_at',)
    show_change_link = True


class CustomerPaymentInline(admin.TabularInline):
    model = CustomerPayment
    extra = 0
    readonly_fields = ('created_at',)
    show_change_link = True


@admin.register(TailorOrder)
class TailorOrderAdmin(CommonSafeDeleteAdmin):
    list_display = (
        'id', 'shop_tailor_id', 'shop', 'client', 'customer_order_type',
        'status', 'customer_total_charge', 'discount_amount', 'paid_amount', 'created_at', 'completed_at', 'recorded_at'
    )
    list_filter = (
        'shop', 'status', 'customer_order_type', 'created_at',
    )
    search_fields = (
        'shop_tailor_id', 'shop__name', 'client__client_name', 'client__client_phone_1',
        'created_by__full_name', 'staff__name'
    )
    date_hierarchy = 'created_at'
    inlines = [TailorOrderItemInline, TailorOrderAdditionalChargeInline, CustomerPaymentInline]
    ordering = ('-created_at',)
    list_per_page = 25


@admin.register(TailorOrderItem)
class TailorOrderItemAdmin(CommonSafeDeleteAdmin):
    list_display = (
        'id', 'product_name', 'product_type', 'order',
        'assigned_tailor', 'tailor_charge', 'customer_tailoring_charge',
        'status', 'expected_completion_date'
    )
    list_filter = ('status', 'assigned_tailor', 'created_at')
    search_fields = ('product_name', 'order__shop_tailor_id', 'assigned_tailor__name')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 25
    inlines = [TailorOrderItemAssignmentInline]


@admin.register(TailorOrderItemAssignment)
class TailorOrderItemAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'order_item', 'tailor', 'tailor_charge', 'tailoring_charge_from_customer',
        'expected_completion_date', 'actual_completion_date', 'created_at',
    )
    list_filter = ('tailor', 'created_at')
    search_fields = (
        'tailor__name',
        'order_item__product_name',
        'order_item__order__shop_tailor_id',
    )
    ordering = ('-created_at',)
    list_per_page = 25


@admin.register(TailorMaterialUsage)
class TailorMaterialUsageAdmin(admin.ModelAdmin):
    list_display = ('id', 'order_item', 'material', 'quantity_meters', 'total_amount', 'created_at')
    list_filter = ('material', 'created_at')
    search_fields = (
        'order_item__product_name', 
        'material__product__name', 
        'order_item__order__shop_tailor_id'
    )
    ordering = ('-created_at',)
    list_per_page = 25


@admin.register(TailorPayment)
class TailorPaymentAdmin(CommonSafeDeleteAdmin):
    list_display = (
        'id', 'shop', 'tailor', 'amount', 'account', 'payment_method',
        'transaction_date_time', 'created_at', 'deleted'
    )
    list_filter = ('payment_method', 'tailor', 'shop', 'created_at')
    search_fields = (
        'tailor__name', 'shop__name', 'account__name',
    )
    ordering = ('-transaction_date_time',)
    list_per_page = 25


@admin.register(TailorOrderAdditionalCharge)
class TailorOrderAdditionalChargeAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'name', 'amount', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'order__shop_tailor_id', 'order__client__name')
    ordering = ('-created_at',)
    list_per_page = 25


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'order', 'payment_amount', 'payment_method', 'created_at', 'recorded_at'
    )
    list_filter = ('payment_method', 'created_at', 'order__shop')
    search_fields = (
        'order__shop_tailor_id', 'order__client__name', 'order__shop__name'
    )
    ordering = ('-created_at',)
    list_per_page = 25

@admin.register(TailorOrderSecurityPayment)
class TailorOrderSecurityPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "amount", "account", "payment_method", "is_paid", "paid_at", "created_at", "recorded_at")
    list_filter = ("created_at", "account", "order__shop")
    search_fields = (
        "order__shop_tailor_id",
        "order__client__name",
        "order__shop__name",
        "account__name",
    )
    ordering = ("-created_at",)
    list_per_page = 25


@admin.register(TailorOrderSecurityDepositAdjustment)
class TailorOrderSecurityDepositAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "adjustment_type",
        "amount",
        "payment_method",
        "account",
        "is_migration_generated",
        "created_at",
        "recorded_at"
    )
    list_filter = (
        "adjustment_type",
        "payment_method",
        "is_migration_generated",
        "created_at",
        "order__shop",
    )
    search_fields = (
        "order__shop_tailor_id",
        "order__client__name",
        "order__shop__name",
        "account__name",
        "reason",
    )
    ordering = ("-created_at",)
    list_per_page = 25


@admin.register(TailorOrderRefund)
class TailorOrderRefundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "refunded_amount",
        "refund_method",
        "account",
        "refunded_by",
        "created_at",
        "recorded_at"
    )
    list_filter = ("refund_method", "created_at", "order__shop")
    search_fields = (
        "order__shop_tailor_id",
        "order__client__name",
        "order__shop__name",
        "account__name",
        "refunded_by__username",
    )
    ordering = ("-created_at",)
    list_per_page = 25