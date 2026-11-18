from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from subscriptions.models import SubscriptionPlan, Feature
from subscriptions.api.admin_v3.serializers import SubscriptionPlanSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Create subscription plan",
    description="Creates a subscription plan with optional included features.",
    request=inline_serializer(
        name="SubscriptionsAdminV3CreatePlanRequest",
        fields={
            "name": serializers.CharField(required=True),
            "description": serializers.CharField(required=False, allow_blank=True),
            "plan_type": serializers.CharField(required=True),
            "base_price": serializers.DecimalField(required=True, max_digits=12, decimal_places=2),
            "max_users": serializers.IntegerField(required=False),
            "max_products": serializers.IntegerField(required=False),
            "max_bookings_per_month": serializers.IntegerField(required=False),
            "duration_days": serializers.IntegerField(required=False),
            "included_features": serializers.ListField(required=False, child=serializers.IntegerField()),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Subscription plan created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Plan name is required.\n"
                "- Plan name cannot exceed 100 characters.\n"
                "- Plan type is required.\n"
                "- Invalid plan type. Must be one of: {valid types}\n"
                "- Base price is required.\n"
                "- Base price cannot be negative.\n"
                "- Invalid base price format.\n"
                "- {Field} cannot be negative.\n"
                "- Invalid {field} format.\n"
                "- A subscription plan with name '{name}' already exists.\n"
                "- Invalid feature IDs: {ids}\n"
                "- Invalid format for included features.\n"
                "- serializer errors under errors field"
            )
        ),
        500: OpenApiResponse(description="Exact message: An error occurred while creating the subscription plan."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Pro", "plan_type": "MONTHLY", "base_price": 999, "duration_days": 30, "included_features": [1, 2]}, request_only=True),
        OpenApiExample("Success response", value={"message": "Subscription plan created successfully.", "data": {"id": 2, "name": "Pro", "plan_type": "MONTHLY"}}, response_only=True, status_codes=["201"]),
    ],
)
def create(self, request):
    """
    Create a new subscription plan
    """
    try:
        data = request.data.copy()

        # Manual validation
        name = data.get("name", "").strip()
        plan_type = data.get("plan_type", "").strip().upper()
        base_price = data.get("base_price")
        max_users = data.get("max_users")
        max_products = data.get("max_products")
        max_bookings_per_month = data.get("max_bookings_per_month")
        duration_days = data.get("duration_days")

        # Required field validation
        if not name:
            return Response(
                {"error": "Plan name is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(name) > 100:
            return Response(
                {"error": "Plan name cannot exceed 100 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Plan type validation
        valid_types = [choice[0] for choice in SubscriptionPlan.PLAN_TYPE_CHOICES]
        if not plan_type:
            return Response(
                {"error": "Plan type is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if plan_type not in valid_types:
            return Response(
                {"error": f"Invalid plan type. Must be one of: {', '.join(valid_types)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Price validation
        if base_price is None:
            return Response(
                {"error": "Base price is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            base_price = float(base_price)
            if base_price < 0:
                return Response(
                    {"error": "Base price cannot be negative."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid base price format."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Integer field validations
        integer_fields = {
            'max_users': max_users,
            'max_products': max_products,
            'max_bookings_per_month': max_bookings_per_month,
            'duration_days': duration_days
        }

        for field_name, field_value in integer_fields.items():
            if field_value is not None:
                try:
                    field_value = int(field_value)
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

        # Check for duplicate plan names
        if SubscriptionPlan.objects.filter(name__iexact=name).exists():
            return Response(
                {"error": f"A subscription plan with name '{name}' already exists."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate included features if provided
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

        # Update data with validated values
        data.update({
            "name": name,
            "plan_type": plan_type,
            "base_price": base_price
        })

        # Create the plan
        serializer = SubscriptionPlanSerializer(data=data)
        if serializer.is_valid():
            plan = serializer.save()

            # Add included features
            if included_features:
                plan.included_features.set(included_features)

            return Response(
                {
                    "message": "Subscription plan created successfully.",
                    "data": SubscriptionPlanSerializer(plan).data
                },
                status=status.HTTP_201_CREATED
            )
        else:
            return Response(
                {"errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

    except Exception as e:
        logger.exception(f"Error in SubscriptionPlanViewSet.create: {str(e)}")
        return Response(
            {"error": "An error occurred while creating the subscription plan."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
