import logging
from rest_framework.decorators import action
from django.db.models import Q, Prefetch, Case, When, Value, IntegerField, F
from django.db.models.functions import Lower, Length
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product, ProductVariant
from shop.models import Shop
from services.models import ShopOwnerService
from services.api.v3.serializers import ProductSerializer
from ERP.utils.custom_pagination import StandardPageNumberPagination
from services.utils.product_report_pdf import ProductReportPDFService

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="List products with variants or generate PDF",
    description="Returns products with active variants. Can return JSON (paginated/non-paginated) or downloadable PDF when generate_pdf=true.",
    parameters=[
        OpenApiParameter("shop_service_id", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Single id or comma-separated shop service ids."),
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search text."),
        OpenApiParameter("search_by", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="name, color, category, model. Invalid values fallback to name."),
        OpenApiParameter("min_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Minimum price."),
        OpenApiParameter("max_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Maximum price."),
        OpenApiParameter("in_stock_only", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Only products with stock variants."),
        OpenApiParameter("paginate", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Default true. Set false to return non-paginated list."),
        OpenApiParameter("generate_pdf", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="When true returns PDF file response."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page when paginate=true."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated JSON response, non-paginated success envelope, or PDF file stream."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Please enter valid numbers for price.\n"
                "- Service id(s) must be integers.\n"
                "- min_price and max_price must be integers.\n"
                "- Invalid id(s)."
            )
        ),
        404: OpenApiResponse(
            description=(
                "Not-found messages:\n"
                "- No matching active service(s) found for this shop.\n"
                "- No active services found for this shop.\n"
                "- Shop not found."
            )
        ),
        500: OpenApiResponse(description="Exact messages: Failed to generate PDF report. / Unable to fetch products right now. Please try again."),
    },
    examples=[
        OpenApiExample("JSON request example", value={"shop_service_id": "1,2", "search": "shirt", "paginate": True, "generate_pdf": False}, request_only=True),
        OpenApiExample("JSON success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 10, "name": "Blue Shirt", "variants": [{"id": 3, "attribute": "M"}]}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("PDF response note", value={"content_type": "application/pdf", "content_disposition": "attachment; filename=Product_Report_Main_Shop_20260504_100000.pdf"}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="list-with-variants")
def list_with_variants(self, request):
    """
    List products with their active variants for a ShopOwnerService or across all services of the active shop.

    Query params:
      - shop_service_id (optional) : integer or comma separated integers
      - search (optional)
      - search_by: name|color|category|model (optional, default=name)
      - min_price, max_price (optional)
      - in_stock_only: true|false (optional, default=false)
      - paginate: true|false (optional, default=true)
      - generate_pdf: true|false (optional, default=false)

    Returns:
      - If generate_pdf=false: paginated JSON response (default behavior)
      - If generate_pdf=true: PDF file download
    """
    try:
        shop_id = self.shop_id

        generate_pdf = request.query_params.get("generate_pdf", "false").lower() == "true"

        # --- parse and validate params ---
        raw_service_id = request.query_params.get("shop_service_id")
        search = (request.query_params.get("search") or "").strip()
        search_by = request.query_params.get("search_by", "name")
        valid_fields = ["name", "color", "category", "model"]
        if search_by not in valid_fields:
            search_by = "name"

        in_stock_only = request.query_params.get("in_stock_only", "false").lower() == "true"
        paginate_flag = request.query_params.get("paginate", "true").lower() != "false"

        # price parsing
        min_price = request.query_params.get("min_price")
        max_price = request.query_params.get("max_price")
        try:
            min_price = int(min_price) if (min_price is not None and min_price != "") else None
            max_price = int(max_price) if (max_price is not None and max_price != "") else None
        except ValueError:
            return self.validation_error_response(
                errors={"price": ["min_price and max_price must be integers."]},
                message="Please enter valid numbers for price."
            )

        # --- determine shop services to include ---
        service_ids = None
        if raw_service_id:
            # allow comma separated list or single id
            raw_vals = [v.strip() for v in str(raw_service_id).split(",") if v.strip()]
            try:
                service_ids = [int(v) for v in raw_vals]
            except ValueError:
                return self.validation_error_response(
                    errors={"shop_service_id": ["Invalid id(s)."]},
                    message="Service id(s) must be integers."
                )

            # ensure services belong to this shop and active
            services_qs = ShopOwnerService.objects.filter(id__in=service_ids, is_active=True, shop_id=shop_id)
            found_ids = list(services_qs.values_list("id", flat=True))
            if not found_ids:
                return self.not_found_response(
                    resource="ShopOwnerService",
                    message="No matching active service(s) found for this shop."
                )
            # restrict to found_ids (ignore invalid ones)
            service_ids = found_ids
        else:
            # fetch all active services for this shop
            service_ids = list(
                ShopOwnerService.objects.filter(shop_id=shop_id, is_active=True).values_list("id", flat=True)
            )
            if not service_ids:
                return self.not_found_response(
                    resource="ShopOwnerService",
                    message="No active services found for this shop."
                )

        # --- build product queryset efficiently ---
        base_filter = Q(shop_service_id__in=service_ids, is_active=True)
        order_by_fields = ["-id"]

        if search:
            base_filter &= Q(**{f"{search_by}__icontains": search})
            order_by_fields = [
                "search_rank",
                "field_length",
                "field_lower",
                "-id",
            ]

        price_q = Q()
        if min_price is not None and max_price is not None:
            price_q = Q(price__isnull=True) | Q(price__gte=min_price, price__lte=max_price)
        elif min_price is not None:
            price_q = Q(price__isnull=True) | Q(price__gte=min_price)
        elif max_price is not None:
            price_q = Q(price__isnull=True) | Q(price__lte=max_price)

        # Prefetch only needed variants (active; optionally stock>0)
        variant_qs = ProductVariant.objects.filter(is_active=True)
        if in_stock_only:
            variant_qs = variant_qs.filter(stock__gt=0)

        # products_qs = (
        #     Product.objects
        #     .filter(base_filter & price_q)
        #     .select_related("shop_service__general_service", "shop_service__shop")
        #     .prefetch_related(Prefetch("variants", queryset=variant_qs.order_by("id")))
        #     .order_by("-id")
        # )

        # if in_stock_only:
        #     # ensure product has at least one matching variant (keeps DB-side filtering)
        #     products_qs = products_qs.filter(variants__is_active=True, variants__stock__gt=0).distinct()

        if generate_pdf:
            # Use values() for better performance with large datasets
            products_qs = (
                Product.objects
                .filter(base_filter & price_q)
                .select_related("shop_service__general_service__main_category", "shop_service__shop")
                .prefetch_related(
                    Prefetch("variants", queryset=variant_qs.only(
                        'id', 'attribute', 'stock', 'price', 'product_id', 'is_active'
                    ))
                )
                .only(
                    'id', 'name', 'color', 'category', 'model', 'thumbnail_image',
                    'purchase_price', 'price', 'shop_service_id',
                    'shop_service__general_service__name',
                    'shop_service__general_service__main_category__name',
                    'shop_service__shop__name', 'shop_service__shop__phone',
                    'shop_service__shop__address', 'shop_service__shop__city',
                    'shop_service__shop__state', 'shop_service__shop__pincode'
                )
            )
        else:
            # Regular JSON response query
            # select_related includes shop_service__shop__numbering_sequence so
            # ProductSerializer.full_product_code (which reads
            # shop_service.shop.numbering_sequence) doesn't trigger a separate
            # query per product row (N+1) when serializing the list below.
            products_qs = (
                Product.objects
                .filter(base_filter & price_q)
                .select_related("shop_service__general_service", "shop_service__shop", "shop_service__shop__numbering_sequence")
                .prefetch_related(Prefetch("variants", queryset=variant_qs.order_by("id")))
            )

        if search:
            products_qs = products_qs.annotate(
                search_rank=Case(
                    When(**{f"{search_by}__iexact": search}, then=Value(0)),
                    When(**{f"{search_by}__istartswith": search}, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                ),
                field_length=Length(F(search_by)),
                field_lower=Lower(F(search_by)),
            )

        products_qs = products_qs.order_by(*order_by_fields)

        if in_stock_only:
            products_qs = products_qs.filter(variants__is_active=True, variants__stock__gt=0).distinct()

        # --- Handle PDF generation ---
        if generate_pdf:
            try:
                # Get shop details
                shop = Shop.objects.get(id=shop_id)

                # Get service name for report (if single service selected)
                service_name = None
                if raw_service_id and len(service_ids) == 1:
                    service = ShopOwnerService.objects.select_related('general_service').get(id=service_ids[0])
                    service_name = service.general_service.name

                # Generate PDF
                pdf_bytes = ProductReportPDFService.generate_product_report_pdf(
                    shop=shop,
                    products_queryset=products_qs,
                    service_name=service_name,
                    search_term=search if search else None
                )

                # Return PDF file
                response = HttpResponse(pdf_bytes, content_type="application/pdf")
                filename = f"Product_Report_{shop.name.replace(' ', '_')}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                response["Content-Disposition"] = f'attachment; filename="{filename}"'
                return response

            except Shop.DoesNotExist:
                return self.not_found_response(
                    resource="Shop",
                    message="Shop not found."
                )
            except Exception as e:
                logger.error(f"PDF generation error: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to generate PDF report.",
                    dev_message=str(e)
                )

        # --- pagination / serialization ---
        paginator = StandardPageNumberPagination()
        if paginate_flag:
            page = paginator.paginate_queryset(products_qs, request, view=self)
            serializer = ProductSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)
        else:
            products = list(products_qs)  # evaluate queryset once
            serializer = ProductSerializer(products, many=True, context={"request": request})
            return self.success_response(
                data={"products": serializer.data},
                message="Products retrieved successfully.",
                dev_message=f"Returned {len(serializer.data)} products from {len(service_ids)} service(s)."
            )

    except Exception as e:
        logger.error(f"list_with_variants error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to fetch products right now. Please try again.",
            dev_message=str(e)
        )
