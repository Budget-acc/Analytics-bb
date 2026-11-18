from django.contrib import admin
from .models import Sale, SaleItem, SalesPaymentDetails


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1  
    readonly_fields = ("subtotal",)
    autocomplete_fields = ["product_variant"]


class PaymentDetailsInline(admin.TabularInline):
    model = SalesPaymentDetails
    extra = 1


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "shop_sale_id",
        "shop",
        "client_phone",
        "client_phone_e164",
        "sale_date",
        "total_amount",
        "discount",
        "paid_amount",
        "decrease_stock",
        "staff",
        "created_at",
    )
    list_filter = ("shop", "sale_date")
    search_fields = ("client__client_name", "shop__name", "client_phone", "client_phone_e164")
    inlines = [SaleItemInline, PaymentDetailsInline]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ("id", "sale", "product_variant", "quantity", "price", "subtotal", "created_at")
    list_filter = ("sale__shop", "product_variant")
    search_fields = ("product_variant__name", "sale__client__client_name")


@admin.register(SalesPaymentDetails)
class PaymentDetailsAdmin(admin.ModelAdmin):
    list_display = ("id", "sale", "payment_amount", "payment_method", "created_at")
    list_filter = ("payment_method", "sale__shop")
    search_fields = ("sale__client__client_name",)

