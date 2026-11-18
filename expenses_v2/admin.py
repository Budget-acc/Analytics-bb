from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import SafeDeleteModel
from safedelete.models import HARD_DELETE
from .models import Expenses, ProductVariantExpense, ShopCategory


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


# Register your models here.
@admin.register(Expenses)
class ExpensesAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "shop", "user", "expense", "type", "date", "expense_date_time", "description", "payment_method", "account", "created_at", "deleted", "category")
    list_filter = ("type", "date", "shop")
    search_fields = ("description",)

@admin.register(ProductVariantExpense)
class ProductVariantExpenseAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "product_variant", "user", "expense", "date", "expense_date_time", "type", "payment_method", "account", "created_at", "deleted", "category")
    list_filter = ("type", "date", "shop")
    search_fields = ("description",)


@admin.register(ShopCategory)
class ShopCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "name", "type", "is_active", "created_at")
    list_filter = ("is_active", "shop")
    search_fields = ("name",)