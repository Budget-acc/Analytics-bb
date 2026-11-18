from subscriptions.models import ShopUserFeature
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Retrieve shop-user feature access",
    description="Returns one shop-user feature access record by id.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopUserFeature id.")],
    responses={
        200: OpenApiResponse(description="Exact message: Feature access retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Record not found."),
        500: OpenApiResponse(description="Exact message: Could not retrieve record."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Feature access retrieved successfully.", "data": {"id": 14, "user": 25, "feature": 4, "granted": True}}, response_only=True, status_codes=["200"]),
    ],
)
def retrieve(self, request, pk=None):
    try:
        obj = (
            ShopUserFeature.objects
            .select_related("shop_user", "feature")
            .get(pk=pk)
        )

        return self.success_response(
            data={
                "id": obj.id,
                "user": obj.shop_user.user_id,
                "shop_user": obj.shop_user_id,
                "feature": obj.feature_id,
                "feature_code": obj.feature.code,
                "feature_name": obj.feature.name,
                "granted": obj.granted
            },
            message="Feature access retrieved successfully.",
            dev_message=f"user-feature #{pk}"
        )
    except ShopUserFeature.DoesNotExist:
        return self.not_found_response("ShopUserFeature", message="Record not found.")
    except Exception as e:
        logger.error(f"ShopUserFeatureViewSet.retrieve:\n,{e}")
        return self.error_response(
            message="Could not retrieve record.",
            dev_message=str(e),
        )
