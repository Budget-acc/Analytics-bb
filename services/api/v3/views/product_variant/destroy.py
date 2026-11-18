import logging
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import ProductVariant
from ERP.utils.shop_activity_logs import create_activity_log

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Delete product variant",
    description="Deletes a product variant after ownership check.",
    parameters=[
        OpenApiParameter("product_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Parent product id."),
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Variant id."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Variant deleted successfully."),
        400: OpenApiResponse(description="Exact message: A product must have at least one variant. The last remaining variant cannot be deleted."),
        403: OpenApiResponse(description="Exact message: You do not have permission to delete this variant."),
        404: OpenApiResponse(description="Exact message: The variant was not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Variant deleted successfully.", "data": None}, response_only=True, status_codes=["200"]),
    ],
)
def destroy(self, request, pk=None, product_id=None):
    try:
        # variant = ProductVariant.objects.get(pk=pk, product_id=product_id)
        # variant.delete()
        # return self.success_response(
        #     message="Variant deleted successfully.",
        #     data=None,
        # )
        variant = ProductVariant.objects.select_related(
            "product__shop_service__shop"
        ).get(pk=pk, product_id=product_id)

        shop_obj = getattr(variant.product.shop_service, "shop", None)
        if getattr(self, "shop_id", None) and shop_obj and shop_obj.id != self.shop_id:
            return self.permission_denied_response(
                message="You do not have permission to delete this variant."
            )

        # A product must always retain at least one variant. Check for the
        # existence of another variant rather than counting all rows - this
        # stops at the first match instead of scanning the full variant set.
        has_other_variant = ProductVariant.objects.filter(
            product_id=product_id
        ).exclude(pk=variant.pk).exists()
        if not has_other_variant:
            return self.error_response(
                message="A product must have at least one variant."
            )

        # Keep details for logging before deletion
        variant_attr = variant.attribute
        variant_id = variant.id
        # product_ref = variant.product

        # Delete variant
        variant.delete()

        # Activity log (best-effort)
        try:
            create_activity_log(
                shop=shop_obj,
                user=request.user,
                instance=variant,
                action="variant_deleted",
                message=f"Variant '{variant_attr or 'default'}' deleted."
            )
        except Exception:
            logger.exception(f"Failed to create activity log for variant delete (variant_id={variant_id})")

        return self.success_response(
            message="Variant deleted successfully.",
            data=None,
        )

    except ProductVariant.DoesNotExist:
        return self.not_found_response(
            resource="ProductVariant",
            message="The variant was not found.",
            dev_message=f"Variant with id {pk} for product {product_id} not found."
        )
    except Exception as e:
        logger.error(f"Variant delete error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting. Please try again.",
            dev_message=str(e)
        )
