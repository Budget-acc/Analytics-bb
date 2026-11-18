from django.contrib import admin, messages
from safedelete.admin import SafeDeleteAdmin
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.models import HARD_DELETE
from .models import Account, LedgerEntry, DailyBalance, ManualIncome, Investment, Withdrawal


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


@admin.register(Account)
class AccountAdmin(CommonSafeDeleteAdmin):
    list_display = ['id', 'name', 'account_type', 'shop', 'is_default', 'is_active', 'created_at', 'deleted']
    search_fields = ['name', 'shop__name']
    list_filter = ['account_type', 'is_default', 'is_active', 'shop', 'deleted']
    ordering = ['-created_at']


@admin.register(LedgerEntry)
class LedgerEntryAdmin(CommonSafeDeleteAdmin):
    list_display = ['id', 'transaction_type', 'amount', 'direction', 'account', 'shop', 'created_at', 'reference_id', 'reference_type', 'is_system_generated', 'deleted']
    search_fields = ['transaction_type', 'account__name', 'shop__name', 'reference_type', 'notes']
    list_filter = ['direction', 'transaction_type', 'payment_method', 'created_by', 'is_system_generated', 'shop', 'account', 'deleted']
    ordering = ['-created_at']



@admin.register(DailyBalance)
class DailyBalanceAdmin(CommonSafeDeleteAdmin):
    list_display = ['id', 'shop', 'account', 'date', 'opening_balance', 'closing_balance', 'total_in', 'total_out', 'deleted']
    search_fields = ['shop__name', 'account__name']
    list_filter = ['date', 'shop', 'account', 'deleted']
    ordering = ['-date']
    date_hierarchy = 'date'


@admin.register(ManualIncome)
class ManualIncomeAdmin(CommonSafeDeleteAdmin):
    list_display = ['id', 'shop', 'amount', 'payment_method', 'category', 'transaction_date_time', 'created_by', 'created_at', 'deleted']
    search_fields = ['shop__name', 'description', 'created_by__username']
    list_filter = ['payment_method', 'shop', 'category', 'transaction_date_time', 'deleted']
    ordering = ['-transaction_date_time']
    date_hierarchy = 'transaction_date_time'


@admin.register(Investment)
class InvestmentAdmin(CommonSafeDeleteAdmin):
    list_display = ['id', 'shop', 'investor_display_name', 'amount', 'investment_type', 'status', 'account', 'transaction_date', 'created_by', 'created_at', 'deleted']
    search_fields = ['shop__name', 'investor_display_name', 'investor_phone', 'investor_email', 'reference_number', 'notes']
    list_filter = ['investment_type', 'payment_method', 'status', 'is_opening_entry', 'shop', 'account', 'category', 'deleted']
    ordering = ['-transaction_date']
    date_hierarchy = 'transaction_date'


@admin.register(Withdrawal)
class WithdrawalAdmin(CommonSafeDeleteAdmin):
    list_display = ['id', 'shop', 'withdrawn_by_display_name', 'amount', 'withdrawal_reason', 'status', 'account', 'transaction_date', 'created_by', 'created_at', 'deleted']
    search_fields = ['shop__name', 'withdrawn_by_display_name', 'withdrawn_by_phone', 'withdrawn_by_email', 'reference_number', 'notes']
    list_filter = ['withdrawal_reason', 'payment_method', 'status', 'is_opening_entry', 'shop', 'account', 'category', 'deleted']
    ordering = ['-transaction_date']
    date_hierarchy = 'transaction_date'
