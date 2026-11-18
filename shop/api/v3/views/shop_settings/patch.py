from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from shop.models import ShopSettings
from shop.api.v3.utils import get_active_shop_id
import logging

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Shop Management V3"],
    summary="Update shop operational settings",
    description="Creates or updates shop settings fields like cooling days, default action, search_client, cooling_period_type, and printer_preference.",
    request=inline_serializer(
        name="ShopV3SettingsPatchRequest",
        fields={
            "cooling_days": serializers.IntegerField(required=False),
            "default_action": serializers.CharField(required=False),
            "search_client": serializers.BooleanField(required=False),
            "cooling_period_type": serializers.CharField(required=False, help_text="after or before"),
            "printer_preference": serializers.CharField(required=False, help_text="ASK_EVERY_TIME, INVOICE_PDF, or RECEIPT."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Shop settings updated successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Invalid request format.\n"
                "- Required fields missing or invalid.\n"
                "- Invalid action value.\n"
                "- Invalid search client value.\n"
                "- Invalid cooling period type value.\n"
                "- Request body must be a dictionary of settings.\n"
                "- Invalid setting. Allowed fields: cooling_days, default_action, search_client, cooling_period_type, printer_preference\n"
                "- Cooling days must be a positive integer.\n"
                "- Invalid default action.\n"
                "- search_client must be a boolean value.\n"
                "- Invalid cooling_period_type. Must be 'after' or 'before'.\n"
                "- Invalid printer preference value."
            )
        ),
        403: OpenApiResponse(description="Exact message: You are not authorized to access this shop."),
        500: OpenApiResponse(description="Exact message: Something went wrong while saving shop settings. Please try again."),
    },
    examples=[
        OpenApiExample("Request example", value={"cooling_days": 7, "default_action": "ASK_EVERY_TIME", "search_client": True, "cooling_period_type": "after", "printer_preference": "INVOICE_PDF"}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Shop settings updated successfully.", "data": {"cooling_days": 7, "default_action": "ASK_EVERY_TIME", "search_client": True, "cooling_period_type": "after", "printer_preference": "INVOICE_PDF"}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["patch"], url_path="update-settings")
def patch(self, request):
    """
    Create or update shop settings (cooling_days etc.).
    """
    try:
        try:
            shop_id = get_active_shop_id(request)
        except Exception:
            return self.permission_denied_response(
                message="You are not authorized to access this shop."
            )

        data = request.data

        if not isinstance(data, dict):
            return self.validation_error_response(
                errors={"request": ["Request body must be a dictionary of settings."]},
                message="Invalid request format."
            )

        # Get or create settings with defaults
        settings_obj, created = ShopSettings.objects.get_or_create(
            shop_id=shop_id,
            defaults={
                "cooling_days": 0,
                "default_action": "ASK_EVERY_TIME",
                "search_client": False,
                "cooling_period_type": "after",
                "printer_preference": "ASK_EVERY_TIME",
            },
        )

        # Allowed updatable fields
        updatable_fields = ["cooling_days", "default_action", "search_client", "cooling_period_type", "printer_preference"]
        updated = {}

        allowed_actions = [choice[0] for choice in ShopSettings.ACTION_CHOICES]

        for field, value in data.items():
            if field not in updatable_fields:
                return self.validation_error_response(
                    errors={field: [f"Invalid setting. Allowed fields: {', '.join(updatable_fields)}"]},
                    message="Required fields missing or invalid."
                )

            # basic validation
            if field == "cooling_days":
                if not isinstance(value, int) or value < 0:
                    return self.validation_error_response(
                        errors={"cooling_days": ["Cooling days must be a positive integer."]},
                        message="Invalid action value."
                    )

            if field == "default_action":
                if value not in allowed_actions:
                    return self.validation_error_response(
                        errors={"default_action": ["Invalid default action."]},
                        message="Invalid action value."
                    )


            if field == "search_client":
                if not isinstance(value, bool):
                    return self.validation_error_response(
                        errors={"search_client": ["search_client must be a boolean value."]},
                        message="Invalid search client value."
                    )

            if field == "cooling_period_type":
                if value not in ["after", "before"]:
                    return self.validation_error_response(
                        errors={"cooling_period_type": ["Invalid cooling_period_type. Must be 'after' or 'before'."]},
                        message="Invalid cooling period type value."
                    )

            if field == "printer_preference":
                allowed_printer_preferences = [choice[0] for choice in ShopSettings.PRINTER_PREFERENCE_CHOICES]
                if value not in allowed_printer_preferences:
                    return self.validation_error_response(
                        errors={"printer_preference": ["Invalid printer preference."]},
                        message="Invalid printer preference value."
                    )

            setattr(settings_obj, field, value)
            updated[field] = value

        settings_obj.save()

        return self.success_response(
            data={
                "cooling_days": settings_obj.cooling_days,
                "default_action": settings_obj.default_action,
                "search_client": settings_obj.search_client,
                "cooling_period_type": settings_obj.cooling_period_type,
                "printer_preference": settings_obj.printer_preference,
            },
            message="Shop settings updated successfully.",
            dev_message=(
                f"{'Created' if created else 'Updated'} settings for shop {shop_id}: {updated}"
            )
        )

    except Exception as e:
        logger.error(f"Error updating shop settings: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while saving shop settings. Please try again.",
            dev_message=str(e)
        )
