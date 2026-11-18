from django.contrib import admin
from .models import ShopWhatsAppConfiguration, ShopWhatsAppTemplate


class ShopWhatsAppTemplateInline(admin.TabularInline):
    model = ShopWhatsAppTemplate
    extra = 0
    fields = ("template_name", "meta_template_id", "language", "category", "status")


@admin.register(ShopWhatsAppConfiguration)
class ShopWhatsAppConfigurationAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "provider", "connection_status", "verified", "created_at", "updated_at")
    list_filter = ("provider", "connection_status", "verified", "created_at")
    search_fields = ("shop__name", "business_account_id", "phone_number_id", "phone_number")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)
    inlines = [ShopWhatsAppTemplateInline]


@admin.register(ShopWhatsAppTemplate)
class ShopWhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "shop_whatsapp_configuration", "template_name", "language", "category", "status")
    list_filter = ("status", "language", "category")
    search_fields = ("template_name", "shop_whatsapp_configuration__shop__name")
    ordering = ("shop_whatsapp_configuration", "template_name")
