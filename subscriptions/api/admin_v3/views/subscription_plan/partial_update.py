from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from subscriptions.models import SubscriptionPlan, Feature
from subscriptions.api.admin_v3.serializers import SubscriptionPlanSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Update subscription plan",
    description="Partially updates a subscription plan and optionally replaces included features.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Subscription plan id.")],
    request=inline_serializer(
        name="SubscriptionsAdminV3PatchPlanRequest",
        fields={
            "name": serializers.CharField(required=False),
            "description": serializers.CharField(required=False, allow_blank=True),
            "plan_type": serializers.CharField(required=False),
            "base_price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "max_users": serializers.IntegerField(required=False),
            "max_products": serializers.IntegerField(required=False),
            "max_bookings_per_month": serializers.IntegerField(required=False),
            "duration_days": serializers.IntegerField(required=False),
            "included_features": serializers.ListField(required=False, child=serializers.IntegerField()),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Subscription plan updated successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Plan ID is required.\n"
                "- Plan name cannot be empty.\n"
                "- Plan name cannot exceed 100 characters.\n"
                "- A subscription plan with name '{name}' already exists.\n"
                "- Invalid plan type. Must be one of: {valid types}\n"
                "- Base price cannot be negative.\n"
                "- Invalid base price format.\n"
                "- {Field} cannot be negative.\n"
                "- Invalid {field} format.\n"
                "- Invalid feature IDs: {ids}\n"
                "- Invalid format for included features.\n"
                "- serializer errors under errors field\n"
                "- Invalid plan ID format."
            )
        ),
        404: OpenApiResponse(description="Exact message: Subscription plan not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while updating the subscription plan."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Pro Plus", "base_price": 1299}, request_only=True),
        OpenApiExample("Success response", value={"message": "Subscription plan updated successfully.", "data": {"id": 2, "name": "Pro Plus"}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None):
    """
    Update an existing subscription plan
    """
    try:
        if not pk:
            return Response(
                {"error": "Plan ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            plan = SubscriptionPlan.objects.get(pk=pk)
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {"error": "Subscription plan not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        data = request.data.copy()

        # Validate name if provided
        if "name" in data:
            name = data.get("name", "").strip()
            if not name:
                return Response(
                    {"error": "Plan name cannot be empty."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if len(name) > 100:
                return Response(
                    {"error": "Plan name cannot exceed 100 characters."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check for duplicate names (excluding current plan)
            if SubscriptionPlan.objects.filter(name__iexact=name).exclude(pk=pk).exists():
                return Response(
                    {"error": f"A subscription plan with name '{name}' already exists."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validate plan type if provided
        if "plan_type" in data:
            plan_type = data.get("plan_type", "").strip().upper()
            valid_types = [choice[0] for choice in SubscriptionPlan.PLAN_TYPE_CHOICES]
            if plan_type not in valid_types:
                return Response(
                    {"error": f"Invalid plan type. Must be one of: {', '.join(valid_types)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            data["plan_type"] = plan_type

        # Validate base price if provided
        if "base_price" in data:
            try:
                base_price = float(data.get("base_price"))
                if base_price < 0:
                    return Response(
                        {"error": "Base price cannot be negative."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                data["base_price"] = base_price
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid base price format."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validate integer fields if provided
        integer_fields = ['max_users', 'max_products', 'max_bookings_per_month', 'duration_days']
        for field_name in integer_fields:
            if field_name in data:
                try:
                    field_value = int(data.get(field_name))
                    if field_value < 0:
                        return Response(
                            {"error": f"{field_name.replace('_', ' ').title()} cannot be negative."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    data[field_name] = field_value
                except (TypeError, ValueError):
                    return Response(
                        {"error": f"Invalid {field_name.replace('_', ' ')} format."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        # Validate included features if provided
        included_features = None
        if "included_features" in data:
            included_features = data.get("included_features", [])
            if included_features:
                try:
                    if isinstance(included_features, str):
                        included_features = [int(x.strip()) for x in included_features.split(',')]
                    elif not isinstance(included_features, list):
                        included_features = list(included_features)

                    # Check if all features exist
                    existing_features = Feature.objects.filter(id__in=included_features, is_active=True)
                    if len(existing_features) != len(included_features):
                        invalid_ids = set(included_features) - set(existing_features.values_list('id', flat=True))
                        return Response(
                            {"error": f"Invalid feature IDs: {list(invalid_ids)}"},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                except (TypeError, ValueError):
                    return Response(
                        {"error": "Invalid format for included features."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        # Remove included_features from data for serializer
        data.pop("included_features", None)

        # Update the plan
        serializer = SubscriptionPlanSerializer(plan, data=data, partial=True)
        if serializer.is_valid():
            plan = serializer.save()

            # Update included features if provided
            if included_features is not None:
                plan.included_features.set(included_features)

            return Response(
                {
                    "message": "Subscription plan updated successfully.",
                    "data": SubscriptionPlanSerializer(plan).data
                },
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {"errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

    except ValueError:
        return Response(
            {"error": "Invalid plan ID format."},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error in SubscriptionPlanViewSet.partial_update")
        return Response(
            {"error": "An error occurred while updating the subscription plan."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
