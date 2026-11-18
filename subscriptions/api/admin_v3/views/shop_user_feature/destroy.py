from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import ShopUserFeature
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Delete shop-user feature access",
    description="Deletes a shop-user feature access record.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopUserFeature id.")],
    responses={
        200: OpenApiResponse(description="Exact message: Access removed."),
        404: OpenApiResponse(description="Not found response for ShopUserFeature."),
        500: OpenApiResponse(description="Exact message: Unable to delete feature access."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Access removed.", "data": None}, response_only=True, status_codes=["200"]),
    ],
)
@transaction.atomic
def destroy(self, request, pk=None):
    try:
        obj = ShopUserFeature.objects.get(pk=pk)
        obj.delete()
        return self.success_response(
            message="Access removed.",
            dev_message=f"Soft-delete #{pk}",
        )
    except ShopUserFeature.DoesNotExist:
        return self.not_found_response("ShopUserFeature")
    except Exception as e:
        logger.error(f"ShopUserFeatureViewSet.delete:\n,{e}")
        return self.error_response(
            message="Unable to delete feature access.",
            dev_message=str(e),
        )
