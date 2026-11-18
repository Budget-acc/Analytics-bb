from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.models import ShopFeaturePolicy, FeatureKey, PasswordRequirement
from shop.api.v3.utils import get_active_shop_id
from subscriptions.api.admin_v3.utils.role_permissions import OwnerOnlyPermission
from ERP.utils.mixins import StandardResponseMixin
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger(__name__)


class ShopPrivacySettingsView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated, OwnerOnlyPermission, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Shop Management V3"],
        summary="Update shop privacy feature policies",
        description="Creates or updates password policy settings per feature for the active shop.",
        request=inline_serializer(
            name="ShopV3PrivacySettingsPatchRequest",
            fields={
                "BOOKING_UPDATE": serializers.CharField(required=False, help_text="Value from PasswordRequirement choices."),
                "SALE_CREATE": serializers.CharField(required=False, help_text="Value from PasswordRequirement choices."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Feature policies updated successfully."),
            400: OpenApiResponse(
                description=(
                    "Error messages:\n"
                    "- Request data must be a dictionary of feature: password_requirement pairs\n"
                    "- Invalid feature. Choose from: {FeatureKey choices}\n"
                    "- Invalid value. Choose from: {PasswordRequirement choices}"
                )
            ),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while updating feature policies."),
        },
        examples=[
            OpenApiExample("Request example", value={"BOOKING_UPDATE": "ALWAYS", "SALE_CREATE": "NEVER"}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Feature policies updated successfully.", "data": {"BOOKING_UPDATE": "ALWAYS", "SALE_CREATE": "NEVER"}}, response_only=True, status_codes=["200"]),
        ],
    )
    def patch(self, request):
        """Update or create multiple shop feature policies"""
        try:
            # Get active shop ID
            try:
                shop_id = get_active_shop_id(request)
            except Exception as e:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated.",
                    dev_message=str(e)
                )

            data = request.data

            if not isinstance(data, dict):
                return self.validation_error_response(
                    errors={"request": ["Request data must be a dictionary of feature: password_requirement pairs"]}
                )

            response_data = {}

            for feature, require_password_for in data.items():
                # Validate feature
                if feature not in [choice[0] for choice in FeatureKey.choices]:
                    return self.validation_error_response(
                        errors={feature: [f"Invalid feature. Choose from: {', '.join([c[0] for c in FeatureKey.choices])}"]}
                    )

                # Validate require_password_for
                if require_password_for not in [choice[0] for choice in PasswordRequirement.choices]:
                    return self.validation_error_response(
                        errors={feature: [f"Invalid value. Choose from: {', '.join([c[0] for c in PasswordRequirement.choices])}"]}
                    )

                # Update or create policy
                ShopFeaturePolicy.objects.update_or_create(
                    shop_id=shop_id,
                    feature=feature,
                    defaults={
                        "require_password_for": require_password_for,
                        "is_active": True
                    }
                )

                # Add to response dictionary
                response_data[feature] = require_password_for

            return self.success_response(
                data=response_data,
                message="Feature policies updated successfully.",
                dev_message=f"Updated feature policies for shop {shop_id}"
            )

        except Exception as e:
            logger.error(f"Feature policy bulk update error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while updating feature policies.",
                dev_message=str(e)
            )
