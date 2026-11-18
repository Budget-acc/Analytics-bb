from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from django.db import IntegrityError, transaction
from django.http import Http404
from subscriptions.models import Feature, ShopFeatureSubscription, ShopFeatureLimit
from subscriptions.api.admin_v3.utils.shop_subscription_service import ShopSubscriptionService
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware, is_naive, get_default_timezone
from decimal import Decimal
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Add features to subscription",
    description="Adds one or more add-on features to an existing subscription and can create usage limits in the same request.",
    parameters=[OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop subscription id.")],
    request=inline_serializer(
        name="SubscriptionsAdminV3AddFeaturesRequest",
        fields={
            "features": serializers.ListField(required=True, child=serializers.JSONField()),
        },
    ),
    responses={
        201: OpenApiResponse(description="Success response with added_features and optionally added_usage_limits."),
        400: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- Invalid request format. \"features\" must be an array.\n"
                "- At least one feature is required.\n"
                "- Cannot add more than 50 features at once.\n"
                "- Validation errors found. (with point-wise errors array)\n"
                "- Duplicate feature_ids found: {ids}.\n"
                "- Features already subscribed: {ids}.\n"
                "- Database integrity error. This may indicate duplicate entries or constraint violations."
            )
        ),
        404: OpenApiResponse(description="Exact messages: Subscription not found. / Features not found or inactive: {ids}."),
        500: OpenApiResponse(description="Exact message pattern: Failed to add features: {exception}"),
    },
    examples=[
        OpenApiExample("Request example", value={"features": [{"feature_id": 7, "price_paid": "99.00", "start_date": "2026-01-01T00:00:00", "end_date": "2026-02-01T00:00:00", "usage_limit": {"extra_limit": 1000, "valid_until": "2026-02-01T00:00:00", "is_active": True, "reason": "Promo"}}]}, request_only=True),
        OpenApiExample("Success response", value={"message": "1 feature(s) and 1 usage limit(s) added successfully.", "data": {"added_features": [{"feature_id": 7, "feature_name": "Advanced Reports", "price_paid": "99.00"}], "added_usage_limits": [{"feature_id": 7, "extra_limit": 1000}]}}, response_only=True, status_codes=["201"]),
    ],
)
@action(detail=False, methods=['post'], url_path='add-features/(?P<pk>[^/.]+)')
@transaction.atomic
def add_features(self, request, pk=None):
    """
    Add multiple features to an existing subscription with optional usage limits.

    Request body:
    {
      "features": [
        {
          "feature_id": 1,
          "price_paid": "99.00",
          "start_date": "2025-11-15T00:00:00",
          "end_date": "2026-11-15T00:00:00",
          "usage_limit": {  // OPTIONAL
            "extra_limit": 2000,
            "valid_until": "2026-11-15T00:00:00",
            "is_active": true,
            "reason": "Promotional bonus"
          }
        }
      ]
    }
    """
    try:
        shop_subscription = self.get_object()
    except Http404:
        return Response(
            {'message': 'Subscription not found.'},
            status=status.HTTP_404_NOT_FOUND
        )

    shop = shop_subscription.shop
    features_data = request.data.get('features', [])

    if not isinstance(features_data, list):
        return Response(
            {'message': 'Invalid request format. "features" must be an array.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not features_data:
        return Response(
            {'message': 'At least one feature is required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(features_data) > 50:
        return Response(
            {'message': 'Cannot add more than 50 features at once.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Phase 1: Extract and validate feature IDs
    feature_ids = []
    validation_errors = []

    for idx, feat_data in enumerate(features_data):
        if not isinstance(feat_data, dict):
            validation_errors.append(f"Feature at index {idx} must be an object.")
            continue

        feature_id = feat_data.get('feature_id')
        if not feature_id:
            validation_errors.append(f"Feature at index {idx}: feature_id is required.")
            continue

        try:
            feature_id = int(feature_id)
            feature_ids.append(feature_id)
        except (TypeError, ValueError):
            validation_errors.append(f"Feature at index {idx}: feature_id must be an integer.")

    if validation_errors:
        return Response(
            {'message': 'Validation errors found.', 'errors': validation_errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check for duplicate feature IDs
    if len(feature_ids) != len(set(feature_ids)):
        duplicates = [fid for fid in feature_ids if feature_ids.count(fid) > 1]
        return Response(
            {'message': f'Duplicate feature_ids found: {list(set(duplicates))}.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Phase 2: Validate features exist
    features_qs = Feature.objects.filter(id__in=feature_ids, is_active=True)
    found_ids = set(features_qs.values_list('id', flat=True))
    missing_ids = set(feature_ids) - found_ids

    if missing_ids:
        return Response(
            {'message': f'Features not found or inactive: {sorted(missing_ids)}.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Phase 3: Check for existing subscriptions (with lock to prevent race conditions)
    existing_feature_ids = set(
        ShopFeatureSubscription.objects.select_for_update().filter(
            shop_subscription=shop_subscription,
            feature_id__in=feature_ids
        ).values_list('feature_id', flat=True)
    )

    if existing_feature_ids:
        return Response(
            {'message': f'Features already subscribed: {sorted(existing_feature_ids)}.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Phase 4: Check for existing feature limits (with lock)
    existing_limit_feature_ids = set(
        ShopFeatureLimit.objects.select_for_update().filter(
            shop=shop,
            feature_id__in=feature_ids
        ).values_list('feature_id', flat=True)
    )

    features_map = {f.id: f for f in features_qs}

    # Phase 5: Validate all data before any database operations
    validated_features = []
    validated_limits = []

    for idx, feat_data in enumerate(features_data):
        feature_id = int(feat_data['feature_id'])
        feature_obj = features_map[feature_id]

        # Validate price_paid
        price_paid_raw = feat_data.get('price_paid')
        if price_paid_raw is None:
            validation_errors.append(f"Feature {feature_id} (index {idx}): price_paid is required.")
            continue
        try:
            price_paid = Decimal(price_paid_raw)
            if price_paid < 0:
                raise ValueError("Price cannot be negative")
        except Exception as e:
            validation_errors.append(f"Feature {feature_id} (index {idx}): invalid price_paid - {str(e)}.")
            continue

        # Validate dates
        start_date_str = feat_data.get('start_date')
        end_date_str = feat_data.get('end_date')

        start_date = None
        end_date = None

        if start_date_str:
            start_date = parse_datetime(start_date_str)
            if not start_date:
                validation_errors.append(f"Feature {feature_id} (index {idx}): invalid start_date format (ISO 8601 required).")
                continue
            # Ensure timezone awareness
            if is_naive(start_date):
                start_date = make_aware(start_date, get_default_timezone())

        if end_date_str:
            end_date = parse_datetime(end_date_str)
            if not end_date:
                validation_errors.append(f"Feature {feature_id} (index {idx}): invalid end_date format (ISO 8601 required).")
                continue
            # Ensure timezone awareness
            if is_naive(end_date):
                end_date = make_aware(end_date, get_default_timezone())

        if start_date and end_date and end_date < start_date:
            validation_errors.append(f"Feature {feature_id} (index {idx}): end_date must be >= start_date.")
            continue

        # Use subscription dates as defaults
        if not start_date:
            start_date = shop_subscription.start_date
        if not end_date:
            end_date = shop_subscription.end_date

        # Validate usage_limit if provided (OPTIONAL)
        usage_limit_data = feat_data.get('usage_limit')
        validated_limit = None

        if usage_limit_data is not None:
            if not isinstance(usage_limit_data, dict):
                validation_errors.append(f"Feature {feature_id} (index {idx}): usage_limit must be an object.")
                continue

            # Check if limit already exists for this shop+feature
            if feature_id in existing_limit_feature_ids:
                validation_errors.append(f"Feature {feature_id} (index {idx}): usage limit already exists for this shop and feature.")
                continue

            # Validate extra_limit (required if usage_limit provided)
            extra_limit_raw = usage_limit_data.get('extra_limit')
            if extra_limit_raw is None:
                validation_errors.append(f"Feature {feature_id} (index {idx}): usage_limit.extra_limit is required.")
                continue

            try:
                extra_limit = int(extra_limit_raw)
                # extra_limit can be positive or negative (no range restriction)
            except (TypeError, ValueError):
                validation_errors.append(f"Feature {feature_id} (index {idx}): usage_limit.extra_limit must be an integer.")
                continue

            # Validate valid_until (optional)
            valid_until = None
            valid_until_str = usage_limit_data.get('valid_until')
            if valid_until_str:
                valid_until = parse_datetime(valid_until_str)
                if not valid_until:
                    validation_errors.append(f"Feature {feature_id} (index {idx}): usage_limit.valid_until has invalid format (ISO 8601 required).")
                    continue
                # Ensure timezone awareness
                if is_naive(valid_until):
                    valid_until = make_aware(valid_until, get_default_timezone())

            # Validate is_active (optional, defaults to True)
            is_active = usage_limit_data.get('is_active', True)
            if not isinstance(is_active, bool):
                validation_errors.append(f"Feature {feature_id} (index {idx}): usage_limit.is_active must be a boolean.")
                continue

            # Validate reason (optional, max 200 chars)
            reason = usage_limit_data.get('reason', '').strip()
            if len(reason) > 200:
                validation_errors.append(f"Feature {feature_id} (index {idx}): usage_limit.reason cannot exceed 200 characters.")
                continue

            validated_limit = {
                'feature': feature_obj,
                'extra_limit': extra_limit,
                'valid_until': valid_until,
                'is_active': is_active,
                'reason': reason
            }

        validated_features.append({
            'feature': feature_obj,
            'price_paid': price_paid,
            'start_date': start_date,
            'end_date': end_date
        })

        if validated_limit:
            validated_limits.append(validated_limit)

    if validation_errors:
        return Response(
            {'message': 'Validation errors found.', 'errors': validation_errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Phase 6: Atomic database operations
    added_features = []
    added_limits = []

    try:
        # Create feature subscriptions
        for item in validated_features:
            feat_sub = ShopSubscriptionService.add_feature_to_subscription(
                shop_subscription=shop_subscription,
                feature=item['feature'],
                price_paid=item['price_paid'],
                start_date=item['start_date'],
                end_date=item['end_date']
            )
            added_features.append({
                'feature_id': item['feature'].id,
                'feature_name': item['feature'].name,
                'price_paid': str(item['price_paid'])
            })

        # Create feature limits (if any)
        if validated_limits:
            limit_objects = [
                ShopFeatureLimit(
                    shop=shop,
                    feature=limit_data['feature'],
                    extra_limit=limit_data['extra_limit'],
                    valid_until=limit_data['valid_until'],
                    is_active=limit_data['is_active'],
                    reason=limit_data['reason']
                )
                for limit_data in validated_limits
            ]
            ShopFeatureLimit.objects.bulk_create(limit_objects)

            added_limits = [
                {
                    'feature_id': limit_data['feature'].id,
                    'feature_name': limit_data['feature'].name,
                    'extra_limit': limit_data['extra_limit'],
                    'valid_until': limit_data['valid_until'].isoformat() if limit_data['valid_until'] else None,
                    'is_active': limit_data['is_active'],
                    'reason': limit_data['reason']
                }
                for limit_data in validated_limits
            ]

    except IntegrityError as e:
        logger.error(f"IntegrityError while adding features to subscription {shop_subscription.id}: {str(e)}")
        return Response(
            {'message': 'Database integrity error. This may indicate duplicate entries or constraint violations.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception(f"Failed to add features to subscription {shop_subscription.id}")
        return Response(
            {'message': f"Failed to add features: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    response_data = {
        'message': f'{len(added_features)} feature(s) added successfully.',
        'data': {
            'added_features': added_features
        }
    }

    if added_limits:
        response_data['data']['added_usage_limits'] = added_limits
        response_data['message'] = f'{len(added_features)} feature(s) and {len(added_limits)} usage limit(s) added successfully.'

    return Response(response_data, status=status.HTTP_201_CREATED)
