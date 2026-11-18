from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from subscriptions.models import SubscriptionPlan, ShopSubscription, Feature
from subscriptions.api.admin_v3.serializers import ShopSubscriptionSerializer
from subscriptions.api.admin_v3.utils.shop_subscription_service import ShopSubscriptionService
from shop.models import Shop
from django.utils.dateparse import parse_datetime
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Create shop subscription",
    description="Subscribes a shop to a plan with optional schedule, payment details, and optional add-on features.",
    request=inline_serializer(
        name="SubscriptionsAdminV3SubscribeRequest",
        fields={
            "shop_id": serializers.IntegerField(required=True),
            "plan_id": serializers.IntegerField(required=True),
            "payment_status": serializers.BooleanField(required=False),
            "auto_renew": serializers.BooleanField(required=False),
            "start_date": serializers.DateTimeField(required=False),
            "end_date": serializers.DateTimeField(required=False),
            "plan_price_paid": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "addon_features": serializers.ListField(required=False, child=serializers.JSONField()),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Subscription created successfully"),
        400: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- Invalid date format for start_date. Use ISO format (YYYY-MM-DDTHH:MM:SS)\n"
                "- Invalid date format for start_date\n"
                "- Invalid date format for end_date. Use ISO format (YYYY-MM-DDTHH:MM:SS)\n"
                "- Invalid date format for end_date\n"
                "- end_date must be after start_date\n"
                "- Shop ID and Plan ID are required\n"
                "- Shop already has an active subscription (ID: {id})\n"
                "- plan_price_paid cannot be negative\n"
                "- Invalid value for plan_price_paid\n"
                "- Addon feature at index {idx}: price_paid cannot be negative\n"
                "- Addon feature at index {idx}: invalid price_paid value\n"
                "- Addon feature at index {idx}: invalid end_date format\n"
                "- ValueError message from service via error field\n"
                "- Something went wrong while creating the subscription."
            )
        ),
        404: OpenApiResponse(
            description=(
                "Exact messages:\n"
                "- Invalid Shop\n"
                "- Invalid Subscription Plan\n"
                "- Addon feature at index {idx}: Feature with ID {id} not found or inactive"
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 10, "plan_id": 2, "payment_status": True, "auto_renew": True, "start_date": "2026-01-01T00:00:00", "end_date": "2026-02-01T00:00:00", "plan_price_paid": "999.00", "addon_features": [{"feature_id": 7, "price_paid": "99.00", "end_date": "2026-02-01T00:00:00"}]}, request_only=True),
        OpenApiExample("Success response", value={"message": "Subscription created successfully", "data": {"id": 40, "shop": 10, "plan": 2, "status": "ACTIVE"}}, response_only=True, status_codes=["201"]),
    ],
)
@action(detail=False, methods=['post'])
def subscribe(self, request):
    """Create a new subscription for a shop"""
    shop_id = request.data.get('shop_id')
    plan_id = request.data.get('plan_id')
    payment_status = request.data.get('payment_status', False)
    auto_renew = request.data.get('auto_renew', False)

    start_date_str = request.data.get('start_date')
    end_date_str = request.data.get('end_date')
    start_date = None
    end_date = None

    if start_date_str:
        try:
            # Parse the date string to datetime object
            start_date = parse_datetime(start_date_str)
            if not start_date:
                # If parse_datetime returns None, the format is invalid
                return Response(
                    {'error': 'Invalid date format for start_date. Use ISO format (YYYY-MM-DDTHH:MM:SS)'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except ValueError:
            return Response(
                {'error': 'Invalid date format for start_date'},
                status=status.HTTP_400_BAD_REQUEST
            )

    if end_date_str:
        try:
            end_date = parse_datetime(end_date_str)
            if not end_date:
                return Response(
                    {'error': 'Invalid date format for end_date. Use ISO format (YYYY-MM-DDTHH:MM:SS)'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except ValueError:
            return Response(
                {'error': 'Invalid date format for end_date'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # Validate date ordering (if both provided)
    if start_date and end_date:
        if end_date <= start_date:
            return Response(
                {'error': 'end_date must be after start_date'},
                status=status.HTTP_400_BAD_REQUEST
            )


    # Validate required data
    if not shop_id or not plan_id:
        return Response(
            {'error': 'Shop ID and Plan ID are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        shop = Shop.objects.get(id=shop_id)
        plan = SubscriptionPlan.objects.get(id=plan_id)
    except Shop.DoesNotExist:
        return Response(
            {'error': 'Invalid Shop'},
            status=status.HTTP_404_NOT_FOUND
        )
    except SubscriptionPlan.DoesNotExist:
        return Response(
            {'error': 'Invalid Subscription Plan'},
            status=status.HTTP_404_NOT_FOUND
        )

    existing_subscription = ShopSubscription.objects.filter(
        shop=shop,
        status='ACTIVE'
    ).first()

    if existing_subscription:
        return Response(
            {'error': f'Shop already has an active subscription (ID: {existing_subscription.id})'},
            status=status.HTTP_400_BAD_REQUEST
        )

    plan_price_paid = request.data.get('plan_price_paid')
    if plan_price_paid is not None:
        try:
            plan_price_paid = Decimal(plan_price_paid)
            if plan_price_paid < 0:
                return Response(
                    {'error': 'plan_price_paid cannot be negative'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except InvalidOperation:
            return Response(
                {'error': 'Invalid value for plan_price_paid'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # Process add-on features
    addon_features = []
    addon_features_data = request.data.get('addon_features', [])
    for idx, feature_data in enumerate(addon_features_data):
        try:
            feature = Feature.objects.get(id=feature_data.get('feature_id'), is_active=True)
            price_paid_raw = feature_data.get('price_paid', feature.base_price)

            try:
                price_paid = Decimal(price_paid_raw)
                if price_paid < 0:
                    return Response(
                        {'error': f'Addon feature at index {idx}: price_paid cannot be negative'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {'error': f'Addon feature at index {idx}: invalid price_paid value'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            feature_end_date_str = feature_data.get('end_date')
            feature_end_date = None

            if feature_end_date_str:
                try:
                    feature_end_date = parse_datetime(feature_end_date_str)
                    if not feature_end_date:
                        return Response(
                            {'error': f'Addon feature at index {idx}: invalid end_date format'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                except ValueError:
                    return Response(
                        {'error': f'Addon feature at index {idx}: invalid end_date format'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            addon_features.append((feature, price_paid, feature_end_date))
        except Feature.DoesNotExist:
            return Response(
                {'error': f"Addon feature at index {idx}: Feature with ID {feature_data.get('feature_id')} not found or inactive"},
                status=status.HTTP_404_NOT_FOUND
            )


    # Create subscription
    try:
        shop_subscription = ShopSubscriptionService.subscribe_shop_to_plan(
            shop=shop,
            plan=plan,
            payment_status=payment_status,
            addon_features=addon_features,
            start_date=start_date,
            end_date=end_date,
            plan_price_paid=plan_price_paid,
            auto_renew=auto_renew
        )
        return Response(
            {
                'message': 'Subscription created successfully',
                'data': ShopSubscriptionSerializer(shop_subscription).data
            },
            status=status.HTTP_201_CREATED
        )
    except ValueError as e:
        logger.exception(f"Validation error creating subscription for shop {shop_id}: {str(e)}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )

    except Exception as e:
        logger.error("\nError creating subscription:\n %s", str(e))
        return Response(
            {'error': 'Something went wrong while creating the subscription.'},
            status=status.HTTP_400_BAD_REQUEST
        )
