from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from purchase.models.purchase import Purchase


@extend_schema(
    tags=["Purchase Management V3"],
    summary="List a vendor's purchase history",
    description=(
        "Returns every (non-deleted) purchase recorded against this vendor, newest first.\n\n"
        "Purpose: Lets shop staff see a vendor's full buying history in one place.\n\n"
        "Context: Used from a vendor's detail screen to show past purchases without navigating "
        "to the general purchase list and filtering by vendor manually."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purpose: Identifies the vendor whose purchases to list. Location: URL path. Format/Type: integer. Required."),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Page number for pagination. Location: query parameter. Format/Type: integer. Optional."),
        OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Purpose: Number of purchases per page. Location: query parameter. Format/Type: integer. Optional."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Vendor purchase history retrieved successfully. Paginated (or plain, if pagination doesn't apply) list of PurchaseListSerializer entries."),
        404: OpenApiResponse(description="Exact message: Not found. (vendor does not exist, belongs to another shop, or is soft-deleted)"),
    },
)
@action(
    detail=False,
    methods=["get"],
    url_path="purchases/(?P<pk>[^/.]+)",
)
def purchases(self, request, *args, **kwargs):

    pk = kwargs.get("pk")
    vendor, error = self._get_vendor_or_404(pk)
    if error:
        return error

    purchases_qs = (
        Purchase.objects.filter(
            vendor=vendor,
            deleted__isnull=True,
        )
        .select_related("vendor")
        .order_by("-purchase_date")
    )

    from purchase.api.v3.serializers import PurchaseListSerializer

    page = self.paginate_queryset(purchases_qs)

    if page is not None:

        serializer = PurchaseListSerializer(
            page,
            many=True,
        )

        return self.get_paginated_response(
            serializer.data
        )

    serializer = PurchaseListSerializer(
        purchases_qs,
        many=True,
    )

    return self.success_response(
        data=serializer.data,
        message="Vendor purchase history retrieved successfully.",
    )


__all__ = ["purchases"]
