# Register your models here.
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User
from safedelete.admin import SafeDeleteAdmin
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import HARD_DELETE
from django import forms

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
class UserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = "__all__"
        widgets = {
            'phone': forms.TextInput(attrs={'placeholder': 'Phone number'})
        }

@admin.register(User)
class UserAdmin(CommonSafeDeleteAdmin, BaseUserAdmin):
    list_display = (
        "id",
        "phone",
        "full_name",
        "email",
        "role",
        "max_allowed_sessions",
        "is_active",
        "is_staff",
        "is_superuser",
        "deleted",
        "password",
    )
    list_filter = (
        "role",
        "is_active",
        "is_staff",
        "is_superuser",
        "deleted",
        "max_allowed_sessions",
    )
    search_fields = ("phone", "full_name", "email")
    ordering = ("id",)

    fieldsets = (
        (None, {"fields": ("phone", "password", "secondary_password")}),
        ("Personal Info", {"fields": ("full_name", "email")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Role Info", {"fields": ("role", "parent")}),
        ("Important dates", {"fields": ("last_login",)}),
        ("Session", {"fields": ("max_allowed_sessions",)}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "password", "role"),
            },
        ),
    )
