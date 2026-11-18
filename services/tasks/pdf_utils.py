import logging
from django.db.models import Q, Prefetch

logger = logging.getLogger("api_requests")


def _get_service_ids_fast(shop_id, raw_service_id):
    """Fast service ID retrieval."""
    from services.models import ShopOwnerService

    if raw_service_id:
        try:
            service_id = int(raw_service_id)
            return [service_id]
        except Exception:
            pass
    return list(
        ShopOwnerService.objects
        .filter(shop_id=shop_id, is_active=True)
        .values_list("id", flat=True)
    )


def _get_products_count_fast(service_ids, request_params):
    """Fast product count for estimation with dynamic filtering."""
    from services.models import Product, ProductVariant, ShopOwnerService

    base_filter = Q(shop_service_id__in=service_ids, is_active=True)

    search = request_params.get('search', '').strip()
    search_by = request_params.get('search_by', 'name')
    in_stock_only = request_params.get('in_stock_only', False)
    min_price = request_params.get('min_price', '').strip()
    max_price = request_params.get('max_price', '').strip()

    if search:
        if search_by == 'name':
            base_filter &= Q(name__icontains=search)
        elif search_by == 'color':
            base_filter &= Q(color__icontains=search)
        elif search_by == 'category':
            base_filter &= Q(category__icontains=search)
        elif search_by == 'model':
            base_filter &= Q(model__icontains=search)
        else:
            base_filter &= Q(name__icontains=search)

    if min_price:
        try:
            base_filter &= Q(price__gte=int(min_price))
        except ValueError:
            pass

    if max_price:
        try:
            base_filter &= Q(price__lte=int(max_price))
        except ValueError:
            pass

    if in_stock_only is not None:
        service_categories = ShopOwnerService.objects.filter(
            id__in=service_ids,
            is_active=True
        ).select_related(
            'general_service__main_category'
        ).values_list(
            'general_service__main_category__name',
            flat=True
        ).distinct()

        is_materials_category = 'Materials' in [cat for cat in service_categories if cat]

        if in_stock_only:
            if is_materials_category:
                base_filter &= Q(variants__is_active=True, variants__fabric_length__gt=0)
            else:
                base_filter &= Q(variants__is_active=True, variants__stock__gt=0)
        else:
            if is_materials_category:
                base_filter &= Q(variants__is_active=True) & (
                    Q(variants__fabric_length=0) | Q(variants__fabric_length__isnull=True)
                )
            else:
                base_filter &= Q(variants__is_active=True, variants__stock=0)

    products_qs = Product.objects.filter(base_filter).distinct()

    if search:
        if search_by == 'name':
            products_qs = products_qs.filter(name__icontains=search)
        elif search_by == 'color':
            products_qs = products_qs.filter(color__icontains=search)
        elif search_by == 'category':
            products_qs = products_qs.filter(category__icontains=search)
        elif search_by == 'model':
            products_qs = products_qs.filter(model__icontains=search)

    if min_price:
        try:
            products_qs = products_qs.filter(price__gte=int(min_price))
        except ValueError:
            pass

    if max_price:
        try:
            products_qs = products_qs.filter(price__lte=int(max_price))
        except ValueError:
            pass

    return products_qs.count()


def _build_streaming_products_queryset(service_ids, request_params, extra_only=None):
    """
    Build streaming queryset with OPTIMIZED field selection.
    Handles stock vs fabric_length filtering based on main category.

    extra_only: optional iterable of additional Product field names to include
    in the .only() projection (e.g. for report variants needing fields the
    default PDF report doesn't use). Defaults preserve existing PDF behavior.
    """
    from services.models import Product, ProductVariant, ShopOwnerService

    base_filter = Q(shop_service_id__in=service_ids, is_active=True)

    search = request_params.get('search', '').strip()
    search_by = request_params.get('search_by', 'name')
    in_stock_only = request_params.get('in_stock_only')
    min_price = request_params.get('min_price', '').strip()
    max_price = request_params.get('max_price', '').strip()

    if search:
        if search_by == 'name':
            base_filter &= Q(name__icontains=search)
        elif search_by == 'color':
            base_filter &= Q(color__icontains=search)
        elif search_by == 'category':
            base_filter &= Q(category__icontains=search)
        elif search_by == 'model':
            base_filter &= Q(model__icontains=search)
        else:
            base_filter &= Q(name__icontains=search)

    if min_price:
        try:
            price_filter = Q(price__gte=int(min_price)) | Q(variants__price__gte=int(min_price))
            base_filter &= price_filter
        except ValueError:
            pass

    if max_price:
        try:
            price_filter = Q(price__lte=int(max_price)) | Q(variants__price__lte=int(max_price))
            base_filter &= price_filter
        except ValueError:
            pass

    service_categories = ShopOwnerService.objects.filter(
        id__in=service_ids,
        is_active=True
    ).select_related(
        'general_service__main_category'
    ).values_list(
        'general_service__main_category__name',
        flat=True
    ).distinct()

    is_materials_category = 'Materials' in [cat for cat in service_categories if cat]

    variant_qs = ProductVariant.objects.filter(is_active=True)

    if in_stock_only is not None:
        if in_stock_only:
            if is_materials_category:
                variant_qs = variant_qs.filter(fabric_length__gt=0)
            else:
                variant_qs = variant_qs.filter(stock__gt=0)
        else:
            if is_materials_category:
                variant_qs = variant_qs.filter(
                    Q(fabric_length=0) | Q(fabric_length__isnull=True)
                )
            else:
                variant_qs = variant_qs.filter(stock=0)

    variant_qs = variant_qs.only(
        'id', 'attribute', 'stock', 'price', 'fabric_length', 'product_id'
    )

    if in_stock_only is not None:
        if in_stock_only:
            if is_materials_category:
                base_filter &= Q(variants__is_active=True, variants__fabric_length__gt=0)
            else:
                base_filter &= Q(variants__is_active=True, variants__stock__gt=0)
        else:
            if is_materials_category:
                base_filter &= Q(variants__is_active=True) & (
                    Q(variants__fabric_length=0) | Q(variants__fabric_length__isnull=True)
                )
            else:
                base_filter &= Q(variants__is_active=True, variants__stock=0)

    only_fields = [
        'id', 'name', 'color', 'category', 'model',
        'thumbnail_image', 'price', 'purchase_price',
        'shop_service_id',
        'shop_service__id',
        'shop_service__general_service__id',
        'shop_service__general_service__name',
        'shop_service__general_service__main_category__id',
        'shop_service__general_service__main_category__name',
    ]
    if extra_only:
        only_fields.extend(f for f in extra_only if f not in only_fields)

    qs = (
        Product.objects
        .filter(base_filter)
        .select_related("shop_service__general_service__main_category")
        .prefetch_related(Prefetch("variants", queryset=variant_qs))
        .only(*only_fields)
        .distinct()
        .order_by('-id')
    )

    return qs
