from django.db.models import Q

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin

from marketplace.models.marketplace_product import MarketplaceProduct
from marketplace.models.marketplace_category import MarketplaceCategory
from marketplace.models.marketplace_subcategory import MarketplaceSubCategory
from marketplace.models.marketplace_product_fabric import MarketplaceProductFabric


class ProductFilterOptionsAPIView(StandardResponseMixin, APIView):

    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    ALLOWED_FILTERS = {
        "colors",
        "models",
        "subcategories",
        "fabrics",
    }

    def post(self, request, *args, **kwargs):

        # Support both the existing category_names parameter
        # and the V3 API's categories parameter.
        categories = request.data.get(
            "categories",
            request.data.get("category_names"),
        )

        requested_filters = request.data.get("filters")

        # Validate categories
        if (
            not isinstance(categories, list)
            or not categories
            or not all(
                isinstance(name, str) and name.strip()
                for name in categories
            )
        ):
            return self.validation_error_response(
                errors={
                    "categories": [
                        "Provide a non-empty list of category names."
                    ]
                },
                message="Invalid filter parameters.",
            )

        categories = list(
            dict.fromkeys(name.strip() for name in categories)
        )

        # Validate requested filter options
        if (
            not isinstance(requested_filters, list)
            or not requested_filters
            or not all(
                isinstance(item, str) and item in self.ALLOWED_FILTERS
                for item in requested_filters
            )
        ):
            return self.validation_error_response(
                errors={
                    "filters": [
                        "Provide a non-empty list containing only: "
                        "colors, models, subcategories, fabrics."
                    ]
                },
                message="Invalid filter parameters.",
            )

        requested_filters = list(dict.fromkeys(requested_filters))

        # Build category matching condition
        category_query = Q()

        for category_name in categories:
            category_query |= Q(
                subcategory__category__name__iexact=category_name
            )

        # Base queryset for eligible products
        products = MarketplaceProduct.objects.filter(
            category_query,
            is_public=True,
            is_active=True,
            subcategory__is_active=True,
            subcategory__category__is_active=True,
        ).distinct()

        data = {}

        # 1. Colors
        if "colors" in requested_filters:

            values = products.exclude(
                color__isnull=True
            ).exclude(
                color__exact=""
            ).values_list(
                "color", flat=True
            ).distinct()

            data["colors"] = sorted(
                {
                    value.strip()
                    for value in values
                    if value and value.strip()
                },
                key=str.casefold,
            )

        # 2. Models
        if "models" in requested_filters:

            values = products.exclude(
                model__isnull=True
            ).exclude(
                model__exact=""
            ).values_list(
                "model", flat=True
            ).distinct()

            data["models"] = sorted(
                {
                    value.strip()
                    for value in values
                    if value and value.strip()
                },
                key=str.casefold,
            )

        # 3. Subcategories
        # These come directly from the selected categories,
        # even if a subcategory currently has no eligible products.
        if "subcategories" in requested_filters:

            subcategory_query = Q()

            for category_name in categories:
                subcategory_query |= Q(
                    category__name__iexact=category_name
                )

            subcategories = MarketplaceSubCategory.objects.filter(
                subcategory_query,
                is_active=True,
                category__is_active=True,
            ).select_related(
                "category"
            ).order_by(
                "name", "id"
            )

            data["subcategories"] = [
                {
                    "id": item.id,
                    "name": item.name,
                    "category_id": item.category_id,
                    "category_name": item.category.name,
                }
                for item in subcategories
            ]

        # 4. Fabrics
        # Return fabrics linked to eligible products in the
        # selected categories through MarketplaceProductFabric.
        if "fabrics" in requested_filters:

            fabric_records = MarketplaceProductFabric.objects.filter(
                product__in=products,
                is_active=True,
                fabric__is_active=True,
            ).values(
                "fabric_id",
                "fabric__name",
            ).distinct().order_by(
                "fabric__name", "fabric_id"
            )

            data["fabrics"] = [
                {
                    "id": item["fabric_id"],
                    "name": item["fabric__name"],
                }
                for item in fabric_records
            ]

        return self.success_response(
            data=data,
            message="Filter options retrieved successfully.",
        )