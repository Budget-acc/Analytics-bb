from django import forms
from django.contrib import admin

from .models import (
    InvoiceTemplate,
    InvoiceTemplateShare,
    ShopCustomInvoice,
    ShopInvoicePreference,
)
from .v3.registry import get_definition
from .v3.services.configuration import ConfigurationError, split_errors, validate_configuration
from .v3.services.custom_invoices import InvalidName, creation_limit, name_exists, normalize_name


@admin.register(InvoiceTemplate)
class InvoiceTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "version", "display_order", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    # Identity and version mirror the code registry (sync_invoice_templates);
    # only availability / presentation are edited here.
    readonly_fields = ("code", "version", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead - customized invoices reference it


@admin.register(ShopInvoicePreference)
class ShopInvoicePreferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "is_customization_enabled", "default_custom_invoice", "updated_at")
    list_filter = ("is_customization_enabled",)
    list_editable = ("is_customization_enabled",)
    search_fields = ("shop__name", "shop__id")
    autocomplete_fields = ("shop",)
    raw_id_fields = ("default_custom_invoice",)
    list_select_related = ("shop", "default_custom_invoice")
    readonly_fields = ("created_at", "updated_at", "updated_by")


class ShopCustomInvoiceAdminForm(forms.ModelForm):
    """Applies the same rules as the API: name format/uniqueness, settings schema, creation limit."""

    class Meta:
        model = ShopCustomInvoice
        fields = "__all__"

    def clean_name(self):
        try:
            return normalize_name(self.cleaned_data.get("name"))
        except InvalidName:
            raise forms.ValidationError("Invoice name must be 1-60 characters without < > { } or line breaks.")

    def clean(self):
        cleaned = super().clean()
        shop, template, name = cleaned.get("shop"), cleaned.get("template"), cleaned.get("name")
        if not (shop and template):
            return cleaned

        if self.instance.pk and self.instance.template_id != template.id:
            self.add_error("template", "The template of a customized invoice cannot be changed.")
        if name and name_exists(shop.id, name, exclude_id=self.instance.pk):
            self.add_error("name", "A customized invoice with this name already exists in this shop.")
        if not self.instance.pk:
            limit = creation_limit()
            if ShopCustomInvoice.objects.filter(shop=shop).count() >= limit:
                raise forms.ValidationError(f"This shop already has the maximum of {limit} customized invoices.")

        definition = get_definition(template.code)
        if definition is None:
            self.add_error("template", "This template is not available in the code registry.")
        else:
            try:
                cleaned["configuration"] = validate_configuration(definition, cleaned.get("configuration") or {})
                cleaned["template_version"] = definition.version
            except ConfigurationError as e:
                self.add_error("configuration", "; ".join(split_errors(e.errors)))
        return cleaned


@admin.register(ShopCustomInvoice)
class ShopCustomInvoiceAdmin(admin.ModelAdmin):
    form = ShopCustomInvoiceAdminForm
    list_display = ("id", "name", "shop", "template", "template_version", "updated_at")
    list_filter = ("template",)
    search_fields = ("name", "shop__name", "shop__id")
    autocomplete_fields = ("shop",)
    list_select_related = ("shop", "template")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")


@admin.register(InvoiceTemplateShare)
class InvoiceTemplateShareAdmin(admin.ModelAdmin):
    list_display = (
        "id", "code", "invoice_name", "source_shop", "template", "import_count", "created_at", "revoked_at",
    )
    list_filter = ("template", "revoked_at")
    search_fields = ("code", "invoice_name", "source_shop__name")
    autocomplete_fields = ("source_shop",)
    raw_id_fields = ("source_custom_invoice",)
    list_select_related = ("source_shop", "template")
    readonly_fields = (
        "code", "invoice_name", "configuration_snapshot", "template_version", "created_by", "created_at",
        "import_count", "last_imported_at",
    )
