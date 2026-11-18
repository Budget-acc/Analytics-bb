from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from django.db import IntegrityError, transaction
from subscriptions.models import Feature, SubscriptionPlan, FeatureUsagePolicy
from subscriptions.api.admin_v3.serializers import FeatureSerializer
import json
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Create feature",
    description="Creates a feature with optional dependencies and optional usage policies.",
    request=inline_serializer(
        name="SubscriptionsAdminV3CreateFeatureRequest",
        fields={
            "name": serializers.CharField(required=True),
            "code": serializers.CharField(required=True),
            "description": serializers.CharField(required=False, allow_blank=True),
            "feature_type": serializers.CharField(required=True),
            "usage_type": serializers.CharField(required=False),
            "base_price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "permissions": serializers.JSONField(required=False),
            "requires_features": serializers.ListField(required=False, child=serializers.IntegerField()),
            "usage_policies": serializers.ListField(required=False, child=serializers.JSONField()),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Feature created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- Feature name is required.\n"
                "- Feature name cannot exceed 100 characters.\n"
                "- Feature code is required.\n"
                "- Feature code cannot exceed 50 characters.\n"
                "- Feature code can only contain letters, numbers, and underscores.\n"
                "- Feature type is required.\n"
                "- Invalid feature type. Must be one of: {valid types}\n"
                "- Invalid usage_type. Must be one of: {valid usage types}\n"
                "- Base price cannot be negative.\n"
                "- Invalid base price format.\n"
                "- Invalid JSON format for permissions.\n"
                "- Permissions must be a JSON object.\n"
                "- Invalid required feature IDs: {ids}\n"
                "- Invalid format for requires_features.\n"
                "- usage_policies must be a list of policy objects.\n"
                "- Policy at index {idx} must be a dictionary.\n"
                "- Policy at index {idx}: 'plan' is required.\n"
                "- Policy at index {idx}: 'limit' is required.\n"
                "- Policy at index {idx}: Plan with ID {id} does not exist or is inactive.\n"
                "- Policy at index {idx}: Invalid plan ID format.\n"
                "- Policy at index {idx}: Limit must be a positive integer.\n"
                "- Policy at index {idx}: Invalid limit format.\n"
                "- Policy at index {idx}: Invalid reset_cycle. Must be one of: MONTHLY, DAILY, LIFETIME\n"
                "- Duplicate plan IDs found in usage_policies.\n"
                "- Usage policies can only be created for features with usage_type='COUNT'.\n"
                "- A feature with code '{code}' already exists.\n"
                "- A feature with name '{name}' already exists.\n"
                "- Duplicate usage policy detected for the same plan.\n"
                "- Database integrity error occurred during feature creation.\n"
                "- serializer errors under errors field"
            )
        ),
        500: OpenApiResponse(description="Exact message: An error occurred while creating the feature."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Advanced Reports", "code": "ADV_REPORTS", "feature_type": "ADDON", "usage_type": "COUNT", "base_price": 99, "requires_features": [1], "usage_policies": [{"plan": 2, "limit": 2000, "reset_cycle": "MONTHLY"}]}, request_only=True),
        OpenApiExample("Success response", value={"message": "Feature created successfully.", "data": {"id": 4, "name": "Advanced Reports", "code": "ADV_REPORTS"}}, response_only=True, status_codes=["201"]),
    ],
)
def create(self, request):
    """
    Create a new feature
    """
    try:
        data = request.data.copy()

        # Manual validation
        name = data.get("name", "").strip()
        code = data.get("code", "").strip().upper()
        feature_type = data.get("feature_type", "").strip().upper()
        usage_type = data.get("usage_type", "NONE").strip().upper()  # Default to NONE
        base_price = data.get("base_price")
        permissions = data.get("permissions", {})

        # Required field validation
        if not name:
            return Response(
                {"error": "Feature name is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(name) > 100:
            return Response(
                {"error": "Feature name cannot exceed 100 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not code:
            return Response(
                {"error": "Feature code is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(code) > 50:
            return Response(
                {"error": "Feature code cannot exceed 50 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Code format validation (alphanumeric and underscore only)
        if not code.replace('_', '').isalnum():
            return Response(
                {"error": "Feature code can only contain letters, numbers, and underscores."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Feature type validation
        valid_types = [choice[0] for choice in Feature.FEATURE_TYPE_CHOICES]
        if not feature_type:
            return Response(
                {"error": "Feature type is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if feature_type not in valid_types:
            return Response(
                {"error": f"Invalid feature type. Must be one of: {', '.join(valid_types)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Usage type validation
        valid_usage_types = [choice[0] for choice in Feature.USAGE_TYPE_CHOICES]
        if usage_type and usage_type not in valid_usage_types:
            return Response(
                {"error": f"Invalid usage_type. Must be one of: {', '.join(valid_usage_types)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Base price validation
        if base_price is not None:
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

        # Permissions validation
        if permissions:
            if isinstance(permissions, str):
                try:
                    permissions = json.loads(permissions)
                except json.JSONDecodeError:
                    return Response(
                        {"error": "Invalid JSON format for permissions."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            if not isinstance(permissions, dict):
                return Response(
                    {"error": "Permissions must be a JSON object."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validate requires_features if provided
        requires_features = data.get("requires_features", [])
        if requires_features:
            try:
                if isinstance(requires_features, str):
                    requires_features = [int(x.strip()) for x in requires_features.split(',')]
                elif not isinstance(requires_features, list):
                    requires_features = list(requires_features)

                # Check if all required features exist
                existing_features = Feature.objects.filter(id__in=requires_features, is_active=True)
                if len(existing_features) != len(requires_features):
                    invalid_ids = set(requires_features) - set(existing_features.values_list('id', flat=True))
                    return Response(
                        {"error": f"Invalid required feature IDs: {list(invalid_ids)}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid format for requires_features."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validate usage_policies if provided (optional)
        usage_policies_data = data.get("usage_policies", [])
        validated_policies = []
        if usage_policies_data:
            if not isinstance(usage_policies_data, list):
                return Response(
                    {"error": "usage_policies must be a list of policy objects."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate each policy
            for idx, policy in enumerate(usage_policies_data):
                if not isinstance(policy, dict):
                    return Response(
                        {"error": f"Policy at index {idx} must be a dictionary."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate required fields
                plan_id = policy.get('plan')
                limit = policy.get('limit')
                reset_cycle = policy.get('reset_cycle', 'MONTHLY').upper()

                if not plan_id:
                    return Response(
                        {"error": f"Policy at index {idx}: 'plan' is required."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if not limit:
                    return Response(
                        {"error": f"Policy at index {idx}: 'limit' is required."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate plan exists
                try:
                    plan = SubscriptionPlan.objects.get(pk=plan_id, is_active=True)
                except SubscriptionPlan.DoesNotExist:
                    return Response(
                        {"error": f"Policy at index {idx}: Plan with ID {plan_id} does not exist or is inactive."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                except (TypeError, ValueError):
                    return Response(
                        {"error": f"Policy at index {idx}: Invalid plan ID format."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate limit
                try:
                    limit = int(limit)
                    if limit <= 0:
                        return Response(
                            {"error": f"Policy at index {idx}: Limit must be a positive integer."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                except (TypeError, ValueError):
                    return Response(
                        {"error": f"Policy at index {idx}: Invalid limit format."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Validate reset_cycle
                valid_cycles = ['MONTHLY', 'DAILY', 'LIFETIME']
                if reset_cycle not in valid_cycles:
                    return Response(
                        {"error": f"Policy at index {idx}: Invalid reset_cycle. Must be one of: {', '.join(valid_cycles)}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                validated_policies.append({
                    'plan': plan,
                    'limit': limit,
                    'reset_cycle': reset_cycle
                })

            # Check for duplicate plan IDs
            plan_ids = [p['plan'].id for p in validated_policies]
            if len(plan_ids) != len(set(plan_ids)):
                return Response(
                    {"error": "Duplicate plan IDs found in usage_policies."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Update data with validated values
        data.update({
            "name": name,
            "code": code,
            "feature_type": feature_type,
            "usage_type": usage_type,
            "permissions": permissions
        })

        if base_price is not None:
            data["base_price"] = base_price

        # Validate usage policies type BEFORE transaction
        if validated_policies and usage_type != 'COUNT':
            return Response(
                {"error": "Usage policies can only be created for features with usage_type='COUNT'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create the feature with transaction to ensure atomicity
        # All validations done above, now execute atomic operation
        serializer = FeatureSerializer(data=data)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    # Check for duplicates inside transaction to prevent race conditions
                    if Feature.objects.select_for_update().filter(code__iexact=code).exists():
                        return Response(
                            {"error": f"A feature with code '{code}' already exists."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    if Feature.objects.select_for_update().filter(name__iexact=name).exists():
                        return Response(
                            {"error": f"A feature with name '{name}' already exists."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    # Create feature
                    feature = serializer.save()

                    # Add required features
                    if requires_features:
                        feature.requires_features.set(requires_features)

                    # Create usage policies if provided
                    if validated_policies:
                        # Bulk create usage policies
                        policies_to_create = [
                            FeatureUsagePolicy(
                                feature=feature,
                                plan=policy['plan'],
                                limit=policy['limit'],
                                reset_cycle=policy['reset_cycle']
                            )
                            for policy in validated_policies
                        ]
                        FeatureUsagePolicy.objects.bulk_create(policies_to_create)

                # Fetch feature with policies for response (outside transaction)
                feature = Feature.objects.prefetch_related(
                    'featureusagepolicy_set__plan'
                ).get(pk=feature.id)

                return Response(
                    {
                        "message": "Feature created successfully.",
                        "data": FeatureSerializer(feature).data
                    },
                    status=status.HTTP_201_CREATED
                )
            except IntegrityError as e:
                logger.error(f"IntegrityError in feature creation: {str(e)}")
                # Parse the error to give more specific message
                error_str = str(e).lower()
                if 'code' in error_str or 'unique' in error_str:
                    return Response(
                        {"error": f"A feature with code '{code}' already exists."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                elif 'plan' in error_str and 'feature' in error_str:
                    return Response(
                        {"error": "Duplicate usage policy detected for the same plan."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                return Response(
                    {"error": "Database integrity error occurred during feature creation."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            return Response(
                {"errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

    except Exception as e:
        logger.exception("Error in FeatureViewSet.create")
        return Response(
            {"error": "An error occurred while creating the feature."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
