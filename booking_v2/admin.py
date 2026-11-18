from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils.translation import ngettext
from safedelete.admin import SafeDeleteAdmin
from safedelete.models import SafeDeleteModel
from safedelete.models import HARD_DELETE
from .models import (
    BookingV2,
    BookedProductVariant,
    PaymentDetailsV2,
    ClientRequirementV2,
    ClientV2,
    ProductVariantAvailability,
    Client,
    BookingAdditionalCharge,
    RefundDetails,
    SecurityDepositPayment,
    SecurityDepositAdjustment,
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



class BookedProductVariantInline(admin.TabularInline):
    model = BookedProductVariant
    extra = 0
    fields = (
        "product_name",
        "variant_attribute",
        "amount",
        "created_at",
    )
    readonly_fields = ("created_at",)
    can_delete = False
    show_change_link = True

class PaymentDetailsV2Inline(admin.TabularInline):
    model = PaymentDetailsV2
    extra = 0
    fields = (
        "id",
        "payment_amount",
        "payment_method",
        "account",
        "created_at",
        "recorded_at",
    )
    readonly_fields = ("id", "created_at", "recorded_at")
    can_delete = False
    show_change_link = True

@admin.register(BookingV2)
class BookingV2Admin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "shop_booking_id",
        "is_deleted",
        "user",
        "shop",
        "list_booked_products", 
        "total_amount",
        "discount_amount",
        "advance_amount",
        "security_amount",
        "get_booking_status",
        "pickup_date",
        "return_date",
        "cooling_period_end",
        "completed_at",
        "delivery_status",
        "payment_status",
        "deleted",
        "created_at",
        "recorded_at",
        "client_address",
        "client_identity",
        "staff",
    )
    search_fields = ("client__client_name", "shop_booking_id")
    list_filter = ("delivery_status", "booking_status", "purchase_mode", "shop", "created_at")
    inlines = [BookedProductVariantInline, PaymentDetailsV2Inline]

    @admin.display(description="Booking Status")
    def get_booking_status(self, obj):
        return obj.get_booking_status_display()

    @admin.display(description="Booked Products")
    def list_booked_products(self, obj):
        products = obj.booked_items.all() 
        return ", ".join(p.product_name for p in products)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("booked_items").select_related("shop", "client")

    @admin.display(boolean=True, description="Deleted")
    def is_deleted(self, obj):
        return obj.deleted is not None    

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "shop":
            kwargs["queryset"] = db_field.related_model.objects.all().order_by('name')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)    
    def lookup_allowed(self, lookup, value):
        allowed = [
            "booked_items__product_variant__product__id__exact",
            "booked_items__product_variant__id__exact",
        ]
        if lookup in allowed:
            return True
        return super().lookup_allowed(lookup, value)

@admin.register(BookedProductVariant)
class BookedProductVariantAdmin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "booking",
        "product_name",
        "variant_attribute",
        "amount",
        "quantity",
        "deleted",
        "returned_at",
        "created_at",
    )
    search_fields = ("product_name",)
    list_filter = ("shop", "created_at")


@admin.register(PaymentDetailsV2)
class PaymentDetailsV2Admin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "booking",
        "payment_amount",
        "payment_method",
        "account",
        "created_at",
        "recorded_at",
    )
    list_filter = ("payment_method", "created_at", "booking__shop")
    search_fields = ("booking__shop_booking_id", "booking__client__client_name", "booking__client__client_phone_1")


@admin.register(ClientRequirementV2)
class ClientRequirementV2Admin(CommonSafeDeleteAdmin):
    list_display = ("id", "booking", "description", "created_at")


@admin.register(ClientV2)
class ClientV2Admin(CommonSafeDeleteAdmin):
    list_display = (
        "id",
        "client_name",
        "client_phone_1",
        "client_address",
        "shop",
        "created_at",
    )
    search_fields = ("client_name", "client_phone_1")
    list_filter = ("shop",)


@admin.register(ProductVariantAvailability)
class ProductVariantAvailabilityAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "variant", "date", "booked_quantity", "created_at", "updated_at")
    list_filter = ("date",)
    search_fields = ("variant__product__name", "variant__attribute")
    ordering = ("-date",)

@admin.register(Client)
class ClientIdentityAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "shop", "client_name", "client_phone_1", "phone_1_e164", "client_phone_2", "phone_2_e164", "deleted", "created_at")
    search_fields = ("client_name", "client_phone_1")
    list_filter = ("shop",)

@admin.register(BookingAdditionalCharge)
class BookingAdditionalChargeAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "booking", "name", "amount", "created_at")
    search_fields = ("booking__shop_booking_id", "name")
    list_filter = ("created_at",)    

@admin.register(RefundDetails)
class RefundDetailsAdmin(CommonSafeDeleteAdmin):
    list_display = ("id", "booking", "refunded_amount", "refund_method", "created_at", "recorded_at")
    search_fields = ("booking__shop_booking_id",)
    list_filter = ("refund_method", "created_at", "booking__shop")    

@admin.register(SecurityDepositPayment)
class SecurityDepositPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "booking",
        "amount",
        "payment_method",
        "account",
        "is_paid",
        "paid_at",
        "created_at",
        "recorded_at"
    )
    list_filter = ("payment_method", "created_at", "booking__shop")
    search_fields = ("booking__id",)
    ordering = ("-created_at",)


@admin.register(SecurityDepositAdjustment)
class SecurityDepositAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "booking",
        "adjustment_type",
        "amount",
        "payment_method",
        "account",
        "reason",
        "created_at",
        "recorded_at"
    )
    list_filter = ("payment_method", "created_at", "booking__shop")
    search_fields = ("booking__id", "adjustment_type", "reason")
    ordering = ("-created_at",)
