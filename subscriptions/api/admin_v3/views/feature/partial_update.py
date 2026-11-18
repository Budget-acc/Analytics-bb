from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from django.db import IntegrityError, transaction
from subscriptions.models import Feature, SubscriptionPlan, FeatureUsagePolicy
from subscriptions.api.admin_v3.serializers import FeatureSerializer
from safedelete.models import HARD_DELETE
import json
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Update feature",
    description="Partially updates a feature, optional dependencies, and optional full usage-policies replacement.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Feature id.")],
    request=inline_serializer(
        name="SubscriptionsAdminV3PatchFeatureRequest",
        fields={
            "name": serializers.CharField(required=False),
            "code": serializers.CharField(required=False),
            "description": serializers.CharField(required=False, allow_blank=True),
            "feature_type": serializers.CharField(required=False),
            "usage_type": serializers.CharField(required=False),
            "base_price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "permissions": serializers.JSONField(required=False),
            "requires_features": serializers.ListField(required=False, child=serializers.IntegerField()),
            "usage_policies": serializers.ListField(required=False, child=serializers.JSONField()),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Feature updated successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- Feature ID is required.\n"
                "- Feature name cannot be empty.\n"
                "- Feature name cannot exceed 100 characters.\n"
                "- A feature with name '{name}' already exists.\n"
                "- Feature code cannot be empty.\n"
                "- Feature code cannot exceed 50 characters.\n"
                "- Feature code can only contain letters, numbers, and underscores.\n"
                "- A feature with code '{code}' already exists.\n"
                "- Invalid feature type. Must be one of: {valid types}\n"
                "- Invalid usage_type. Must be one of: {valid usage types}\n"
                "- Base price cannot be negative.\n"
                "- Invalid base price format.\n"
                "- Invalid JSON format for permissions.\n"
                "- Permissions must be a JSON object.\n"
                "- A feature cannot depend on itself.\n"
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
                "- A feature with this code already exists or duplicate usage policy detected.\n"
                "- serializer errors under errors field\n"
                "- Invalid feature ID format."
            )
        ),
        404: OpenApiResponse(description="Exact message: Feature not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while updating the feature."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Advanced Reports Plus", "base_price": 129, "usage_policies": [{"plan": 2, "limit": 2500, "reset_cycle": "MONTHLY"}]}, request_only=True),
        OpenApiExample("Success response", value={"message": "Feature updated successfully.", "data": {"id": 4, "name": "Advanced Reports Plus"}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None):
    """
    Update an existing feature
    """
    try:
        if not pk:
            return Response(
                {"error": "Feature ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            feature = Feature.objects.get(pk=pk)
        except Feature.DoesNotExist:
            return Response(
                {"error": "Feature not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        data = request.data.copy()

        # Validate name if provided
        if "name" in data:
            name = data.get("name", "").strip()
            if not name:
                return Response(
                    {"error": "Feature name cannot be empty."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if len(name) > 100:
                return Response(
                    {"error": "Feature name cannot exceed 100 characters."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check for duplicate names (excluding current feature)
            if Feature.objects.filter(name__iexact=name).exclude(pk=pk).exists():
                return Response(
                    {"error": f"A feature with name '{name}' already exists."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            data["name"] = name

        # Validate code if provided
        if "code" in data:
            code = data.get("code", "").strip().upper()
            if not code:
                return Response(
                    {"error": "Feature code cannot be empty."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if len(code) > 50:
                return Response(
                    {"error": "Feature code cannot exceed 50 characters."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Code format validation
            if not code.replace('_', '').isalnum():
                return Response(
                    {"error": "Feature code can only contain letters, numbers, and underscores."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check for duplicate codes (excluding current feature)
            if Feature.objects.filter(code__iexact=code).exclude(pk=pk).exists():
                return Response(
                    {"error": f"A feature with code '{code}' already exists."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            data["code"] = code

        # Validate feature type if provided
        if "feature_type" in data:
            feature_type = data.get("feature_type", "").strip().upper()
            valid_types = [choice[0] for choice in Feature.FEATURE_TYPE_CHOICES]
            if feature_type not in valid_types:
                return Response(
                    {"error": f"Invalid feature type. Must be one of: {', '.join(valid_types)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            data["feature_type"] = feature_type

        # Validate usage type if provided
        if "usage_type" in data:
            usage_type = data.get("usage_type", "").strip().upper()
            valid_usage_types = [choice[0] for choice in Feature.USAGE_TYPE_CHOICES]
            if usage_type not in valid_usage_types:
                return Response(
                    {"error": f"Invalid usage_type. Must be one of: {', '.join(valid_usage_types)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            data["usage_type"] = usage_type

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

        # Validate permissions if provided
        if "permissions" in data:
            permissions = data.get("permissions", {})
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
            data["permissions"] = permissions

        # Validate requires_features if provided
        requires_features = None
        if "requires_features" in data:
            requires_features = data.get("requires_features", [])
            if requires_features:
                try:
                    if isinstance(requires_features, str):
                        requires_features = [int(x.strip()) for x in requires_features.split(',')]
                    elif not isinstance(requires_features, list):
                        requires_features = list(requires_features)

                    # Prevent self-dependency
                    if int(pk) in requires_features:
                        return Response(
                            {"error": "A feature cannot depend on itself."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

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

        # Validate usage_policies if provided (optional update)
        usage_policies_data = data.get("usage_policies", None)
        validated_policies = None
        if usage_policies_data is not None:
            if not isinstance(usage_policies_data, list):
                return Response(
                    {"error": "usage_policies must be a list of policy objects."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            validated_policies = []
            for idx, policy in enumerate(usage_policies_data):
                if not isinstance(policy, dict):
                    return Response(
                        {"error": f"Policy at index {idx} must be a dictionary."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

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

        # Remove requires_features and usage_policies from data for serializer
        data.pop("requires_features", None)
        data.pop("usage_policies", None)

        # Update the feature with transaction
        serializer = FeatureSerializer(feature, data=data, partial=True)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    updated_feature = serializer.save()

                    # Update required features if provided
                    if requires_features is not None:
                        updated_feature.requires_features.set(requires_features)

                    # Update usage policies if provided
                    if validated_policies is not None:
                        # Only allow usage policies for COUNT-based features
                        if updated_feature.usage_type != 'COUNT':
                            return Response(
                                {"error": "Usage policies can only be created for features with usage_type='COUNT'."},
                                status=status.HTTP_400_BAD_REQUEST
                            )

                        # Delete existing policies and create new ones (replace strategy)
                        FeatureUsagePolicy.objects.filter(feature=updated_feature).delete(force_policy=HARD_DELETE)

                        if validated_policies:  # Only create if not empty list
                            policies_to_create = [
                                FeatureUsagePolicy(
                                    feature=updated_feature,
                                    plan=policy['plan'],
                                    limit=policy['limit'],
                                    reset_cycle=policy['reset_cycle']
                                )
                                for policy in validated_policies
                            ]
                            FeatureUsagePolicy.objects.bulk_create(policies_to_create)

                # Fetch updated feature with policies
                updated_feature = Feature.objects.prefetch_related(
                    'featureusagepolicy_set__plan'
                ).get(pk=updated_feature.id)

                return Response(
                    {
                        "message": "Feature updated successfully.",
                        "data": FeatureSerializer(updated_feature).data
                    },
                    status=status.HTTP_200_OK
                )
            except IntegrityError as e:
                logger.error(f"IntegrityError in feature update: {str(e)}")
                return Response(
                    {"error": "A feature with this code already exists or duplicate usage policy detected."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            return Response(
                {"errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

    except ValueError:
        return Response(
            {"error": "Invalid feature ID format."},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error in FeatureViewSet.partial_update")
        return Response(
            {"error": "An error occurred while updating the feature."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
