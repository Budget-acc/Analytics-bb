from datetime import datetime, timedelta, time
from booking_v2.models import (
    BookedProductVariant,
    ProductVariantAvailability,
)
from services.models import (
    Product,
    ProductVariant,
    ShopOwnerService,
    ServiceAttributeValue,
)
from django.db.models import Q, Sum, Prefetch, F, Case, When, Value, IntegerField
from django.db import models
from rest_framework.views import APIView
from rest_framework import status
from django.utils.dateparse import parse_date
from django.utils import timezone
from public_api.api.v3.views.pagination import ProductCursorPagination
from public_api.api.v3.serializers import PublicProductVariantSerializer
from public_api.api.v3.utils import (
    resolve_public_token,
    build_search_filter_q,
    build_search_priority_case,
    parse_int_list_param,
)
from ERP.utils.mixins import StandardResponseMixin
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Public Shop API"],
    summary="List available products for a public shop",
    description=(
        "Returns products with available variants for the requested rental date range.\n\n"
        "Purpose: Allows customers to see which products (and how many units) are available to "
        "rent during their chosen period, before contacting the shop.\n\n"
        "Context: Called after the customer selects pickup and return dates on the public shop "
        "page. The availability engine is the same one used internally by the booking app — "
        "it accounts for existing bookings, stock levels, and cooling periods. Only products "
        "with at least one available variant are returned.\n\n"
        "No authentication is required. The shop is identified entirely by the public_token "
        "embedded in the URL path.\n\n"
        "Supports cursor-based pagination, optional service/category filtering, free-text "
        "and attribute-based search, and price range filtering.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- public_token: identifies the shop.\n\n"
        "Query parameters — Required:\n"
        "- pickup_date (YYYY-MM-DD): rental start date.\n"
        "- return_date (YYYY-MM-DD): rental end date. Must be >= pickup_date.\n\n"
        "Query parameters — Optional:\n"
        "- service_ids (array of integers): filter products to one or more services/categories.\n"
        "- pickup_time (HH:MM): pickup time for hour-level availability checks.\n"
        "- return_time (HH:MM): return time for hour-level availability checks.\n"
        "- search_value (string): search term matched against product name, color, model, and category (OR logic).\n"
        "- min_price (integer): minimum product price. Default: 0.\n"
        "- max_price (integer): maximum product price. No upper limit if omitted."
    ),
    parameters=[
        OpenApiParameter(
            name="public_token",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Uniquely identifies the shop for public access without exposing the "
                "internal shop database ID. "
                "Location: URL path parameter. "
                "Format/Type: string (10-character nanoid, e.g. X7h29LmQa3). "
                "Required."
            ),
        ),
        OpenApiParameter(
            name="pickup_date",
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            required=True,
            description=(
                "Purpose: Start date of the requested rental period. Used as the lower bound "
                "for the availability calculation. "
                "Location: query parameter. "
                "Format/Type: date string — YYYY-MM-DD (e.g. 2026-07-01). "
                "Required."
            ),
        ),
        OpenApiParameter(
            name="return_date",
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            required=True,
            description=(
                "Purpose: End date of the requested rental period. Must be greater than or "
                "equal to pickup_date. Used as the upper bound for the availability calculation. "
                "Location: query parameter. "
                "Format/Type: date string — YYYY-MM-DD (e.g. 2026-07-04). "
                "Required."
            ),
        ),
        OpenApiParameter(
            name="service_ids",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Restricts the product listing to one or more services/categories. "
                "Applies identically for shop and org public tokens: each requested service ID "
                "is validated against the shop(s) resolved from the token, and only products from "
                "the valid, requested services are returned. "
                "Obtain valid IDs from the services API (/api/v3/public/shops/{public_token}/services/). "
                "Accepts a comma-separated list of service IDs or repeated query params "
                "(e.g. service_ids=1,2 or service_ids=1&service_ids=2). "
                "Location: query parameter. "
                "Format/Type: comma-separated integers or repeated param. "
                "Optional. If omitted, products from all active services of the resolved shop(s) are returned."
            ),
        ),
        OpenApiParameter(
            name="pickup_time",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Pickup time of day, enabling hour-level availability checks instead "
                "of full-day checks. When provided together with return_time, the availability "
                "engine uses datetime overlap logic. "
                "Location: query parameter. "
                "Format/Type: time string — HH:MM in 24-hour format (e.g. 09:30). "
                "Optional. If omitted, full-day date-based availability is used."
            ),
        ),
        OpenApiParameter(
            name="return_time",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Return time of day, used together with pickup_time for datetime-level "
                "availability checks. If pickup_time is provided but return_time is omitted, "
                "return_time defaults to 23:59. "
                "Location: query parameter. "
                "Format/Type: time string — HH:MM in 24-hour format (e.g. 18:00). "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="search_value",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Search term applied across product name, color, model, and category "
                "fields using case-insensitive partial matching (OR logic). If provided, only "
                "products where at least one of these fields contains the search term are returned. "
                "Location: query parameter. "
                "Format/Type: string. "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="min_price",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Lower bound for product price filtering. Products with price below "
                "this value are excluded. Products with a null price are always included, "
                "but listed after all priced products. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. Default: 0."
            ),
        ),
        OpenApiParameter(
            name="max_price",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Upper bound for product price filtering. Products with price above "
                "this value are excluded. Products with a null price are always included, "
                "but listed after all priced products. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. If omitted, no upper price limit is applied."
            ),
        ),
        OpenApiParameter(
            name="shop_ids",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: When using an org public token, restricts results to one or more branch "
                "shops within the organization. Accepts a comma-separated list of shop IDs or repeated "
                "query params (e.g. shop_ids=1,2 or shop_ids=1&shop_ids=2). "
                "Has no effect for shop-level tokens (already scoped to one shop). "
                "Obtain valid IDs from the org shops API. "
                "Location: query parameter. "
                "Format/Type: comma-separated integers or repeated param. "
                "Optional. If omitted, products from all active org shops are returned."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a cursor-paginated list of available products.\n\n"
                "Response shape:\n"
                "- next (string | null): Cursor URL for the next page.\n"
                "- previous (string | null): Cursor URL for the previous page.\n"
                "- page_size (integer): Number of items per page.\n"
                "- data.main_service_name (string | null): Main category name of the filtered "
                "service. Null if no service_ids was provided.\n"
                "- data.products (array): a flat array of available products. "
                "Products with a valid, in-range price are always listed before products "
                "with a null price, regardless of search. "
                "When search_value is supplied, priced/null-price groups are then ordered by "
                "match priority: field priority name > color > model > category, and within "
                "each field, exact_match > starts_with > contains. Without search_value, each "
                "group is ordered by id (plain-browse behavior).\n"
                "  Each product contains: id, name, color, category, model, "
                "image, thumbnail_image, attributes (object of key-value pairs), variants "
                "(array of available variants).\n"
                "  - price (integer, optional): the product's price. Included only when the "
                "resolved shop's (shop token) or organization's (org token) "
                "show_prices_in_public_listing setting is enabled; omitted entirely "
                "(not null) otherwise.\n"
                "  Each variant contains: id, attribute, stock, remaining_stock "
                "(integer — actual bookable quantity for the requested period)."
            )
        ),
        400: OpenApiResponse(
            description=(
                "Validation error. Exact messages include: "
                "Missing required parameters: pickup_date, return_date; "
                "Invalid time format. Use HH:MM.; "
                "Invalid date format. Use YYYY-MM-DD.; "
                "Return date must be greater than or equal to event date.; "
                "Invalid min_price value.; "
                "Invalid max_price value.; "
                "Invalid shop_ids value. All values must be integers.; "
                "One or more shop IDs do not belong to this organization."
            )
        ),
        403: OpenApiResponse(
            description=(
                "Permission error. Exact messages include: "
                "One or more service IDs do not belong to this shop.; "
                "One or more service IDs do not belong to this organization."
            )
        ),
        404: OpenApiResponse(
            description=(
                "Not found. Exact messages include: "
                "Shop not found or public access is disabled."
            )
        ),
    },
)
class PublicShopProductsView(StandardResponseMixin, APIView):
    authentication_classes = []
    permission_classes = []

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------
    def get(self, request, public_token):
        # --- Identify token type by length (12=shop, 10=org) ---
        token_type, token_obj = resolve_public_token(public_token)
        if token_type is None:
            return self.not_found_response(resource="Shop")

        if token_type == "shop":
            shop_ids = [token_obj.id]
        else:  # org
            shop_ids = list(
                token_obj.shops
                .filter(is_active=True, show_in_org_catalog=True)
                .values_list("id", flat=True)
            )
            if not shop_ids:
                return self.not_found_response(resource="Shop")

        is_org = token_type == "org"
        # token_obj is already the Shop (shop token) or Organization (org token) instance
        # fetched by resolve_public_token() - reading this costs no extra query. For org
        # tokens, the org-level setting governs price visibility uniformly across all its
        # branch shops.
        show_prices = token_obj.show_prices_in_public_listing

        # --- Required query params ---
        pickup_date_str = request.GET.get("pickup_date") or request.GET.get("event_date")
        return_date_str = request.GET.get("return_date")

        search_value = request.GET.get("search_value", "").strip().lower()

        if not all([pickup_date_str, return_date_str]):
            return self.validation_error_response(
                errors={"params": ["Missing required parameters: pickup_date, return_date"]},
                message="Missing required parameters.",
            )

        event_date = parse_date(pickup_date_str)
        return_date = parse_date(return_date_str)

        # --- Optional times ---
        pickup_time_str = request.GET.get("pickup_time")
        return_time_str = request.GET.get("return_time")

        if pickup_time_str:
            try:
                pickup_time = datetime.strptime(pickup_time_str, "%H:%M").time()

                if return_time_str:
                    return_time = datetime.strptime(return_time_str, "%H:%M").time()
                else:
                    return_time = time(23, 59)

                event_datetime = datetime.combine(event_date, pickup_time)
                return_datetime = datetime.combine(return_date, return_time)

                if timezone.is_naive(event_datetime):
                    event_datetime = timezone.make_aware(event_datetime, timezone.get_current_timezone())
                if timezone.is_naive(return_datetime):
                    return_datetime = timezone.make_aware(return_datetime, timezone.get_current_timezone())

            except ValueError:
                return self.validation_error_response(
                    errors={"time": ["Invalid time format. Use HH:MM."]},
                    message="Invalid time format.",
                )

        elif return_time_str:
            try:
                return_time = datetime.strptime(return_time_str, "%H:%M").time()
                pickup_time = time(0, 1)

                event_datetime = datetime.combine(event_date, pickup_time)
                return_datetime = datetime.combine(return_date, return_time)

                if timezone.is_naive(event_datetime):
                    event_datetime = timezone.make_aware(event_datetime, timezone.get_current_timezone())
                if timezone.is_naive(return_datetime):
                    return_datetime = timezone.make_aware(return_datetime, timezone.get_current_timezone())

            except ValueError:
                return self.validation_error_response(
                    errors={"time": ["Invalid time format. Use HH:MM."]},
                    message="Invalid time format.",
                )
        else:
            event_datetime = None
            return_datetime = None

        if not event_date or not return_date:
            return self.validation_error_response(
                errors={"date": ["Invalid date format. Use YYYY-MM-DD."]},
                message="Invalid date format.",
            )

        if return_date < event_date:
            return self.validation_error_response(
                errors={"date": ["Return date must be greater than or equal to event date."]},
                message="Return date must be greater than or equal to pickup date.",
            )

        # --- Optional service filter ---
        main_service_name = None
        shop_ids_param = []

        if is_org:
            # Org token: optionally narrow to a subset of branch shops.
            # Strict validation (unlike service_ids): any non-integer value is rejected
            # outright rather than silently dropped, matching pre-existing behavior.
            raw_shop_ids = request.GET.getlist("shop_ids")
            for v in raw_shop_ids:
                shop_ids_param.extend(str(v).split(","))
            shop_ids_param = [x.strip() for x in shop_ids_param if x.strip()]

            if shop_ids_param:
                try:
                    shop_ids_param = [int(x) for x in shop_ids_param]
                except ValueError:
                    return self.validation_error_response(
                        errors={"shop_ids": ["Invalid shop_ids value. All values must be integers."]},
                        message="Invalid shop_ids value.",
                    )
                invalid_shops = set(shop_ids_param) - set(shop_ids)
                if invalid_shops:
                    return self.validation_error_response(
                        errors={"shop_ids": ["One or more shop IDs do not belong to this organization."]},
                        message="Invalid shop_ids.",
                    )
                shop_ids = shop_ids_param

        # Standardized across shop and org tokens: multiple service_ids
        # (comma-separated or repeated param). For shop tokens, shop_ids is
        # always [shop.id] and shop_ids_param is always empty, so this
        # naturally behaves as "full scope" validation (403 on any invalid id).
        raw_values, service_id_list = parse_int_list_param(request, "service_ids")
        # Tracks whether the client actually supplied service_ids, independent of
        # what service_id_list ends up containing after per-shop narrowing below.
        # This matters because narrowing can legitimately reduce service_id_list to
        # an empty list (e.g. none of the requested service_ids belong to any of the
        # requested shops) - that must still mean "return zero products", never fall
        # back to "no service filter was requested at all".
        service_ids_provided = bool(raw_values)

        # Explicit Shop -> valid Service IDs mapping, resolved in a single query.
        shop_to_service_ids = {}
        if service_id_list:
            service_rows = ShopOwnerService.objects.filter(
                shop_id__in=shop_ids,
                id__in=service_id_list,
            ).values_list("shop_id", "id")
            for sid, svc_id in service_rows:
                shop_to_service_ids.setdefault(sid, []).append(svc_id)

            valid_ids = {svc_id for ids in shop_to_service_ids.values() for svc_id in ids}
            if shop_ids_param:
                # Specific shops requested: silently drop services from other shops
                service_id_list = list(valid_ids)
            else:
                # Full scope (single shop token, or org token with no shop_ids
                # narrowing): all service IDs must belong to one of the resolved shops.
                invalid = set(service_id_list) - valid_ids
                if invalid:
                    return self.error_response(
                        message=(
                            "One or more service IDs do not belong to this organization."
                            if is_org else
                            "One or more service IDs do not belong to this shop."
                        ),
                        http_status=status.HTTP_403_FORBIDDEN,
                    )
            # Derive main_service_name from the first matching service
            first_svc = (
                ShopOwnerService.objects
                .select_related("general_service__main_category")
                .filter(id__in=service_id_list, shop_id__in=shop_ids)
                .first()
            )
            if first_svc and first_svc.general_service and first_svc.general_service.main_category:
                main_service_name = first_svc.general_service.main_category.name

        # --- Build product queryset ---
        variant_qs = ProductVariant.objects.filter(
            is_active=True,
            stock__gt=0,
        ).select_related("product")

        if service_ids_provided:
            # Service-based filtering requested: only valid Shop-Service combinations,
            # built explicitly per shop from shop_to_service_ids. A single combined
            # query (Q objects OR into one SQL WHERE, not per-shop queries). Checking
            # service_ids_provided (not service_id_list) here is deliberate - even if
            # narrowing reduced service_id_list to empty (no requested service belongs
            # to any requested shop), that must still mean "return zero products", not
            # silently fall through to the unfiltered "every product" branch below.
            if not shop_to_service_ids:
                product_qs = Product.objects.none()
            else:
                shop_service_filter = Q()
                for shop_id, valid_service_ids in shop_to_service_ids.items():
                    shop_service_filter |= Q(
                        shop_service__shop_id=shop_id,
                        shop_service_id__in=valid_service_ids,
                    )
                product_qs = Product.objects.filter(shop_service_filter, is_active=True)
        else:
            # No service_ids at all: return every available product for every requested shop.
            product_qs = Product.objects.filter(
                shop_service__shop_id__in=shop_ids, is_active=True
            )

        product_qs = product_qs.prefetch_related(
            Prefetch("variants", queryset=variant_qs),
            Prefetch(
                "attribute_values",
                queryset=ServiceAttributeValue.objects.select_related("attribute"),
                to_attr="prefetched_attributes",
            ),
        ).select_related("shop_service", "shop_service__shop")

        # --- Search filter ---
        # Field priority: name > color > model > category. Within each field:
        # exact > starts-with > contains. build_search_filter_q's single icontains
        # OR-filter is a superset of the exact/starts-with tiers, so no extra query
        # is needed to also capture those - build_search_priority_case then ranks
        # the already-matched rows into the 12 priority buckets.
        if search_value:
            product_qs = product_qs.filter(
                build_search_filter_q(search_value)
            ).annotate(
                search_priority=build_search_priority_case(search_value)
            )

        # --- Price filter ---
        try:
            min_price = int(request.GET.get("min_price", 0))
        except ValueError:
            return self.validation_error_response(
                errors={"min_price": ["Invalid min_price value."]},
                message="Invalid min_price value.",
            )

        max_price_raw = request.GET.get("max_price")
        if max_price_raw:
            try:
                max_price = int(max_price_raw)
            except ValueError:
                return self.validation_error_response(
                    errors={"max_price": ["Invalid max_price value."]},
                    message="Invalid max_price value.",
                )
            product_qs = product_qs.filter(
                Q(price__isnull=True) | Q(price__gte=min_price, price__lte=max_price)
            )
        else:
            product_qs = product_qs.filter(
                Q(price__isnull=True) | Q(price__gte=min_price)
            )

        # Priced products (within range) are prioritized over null-price products,
        # which are always included but pushed to the end of the listing.
        product_qs = product_qs.annotate(
            price_priority=Case(
                When(price__isnull=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

        # --- Cursor pagination ---
        # Note: a per-shop Window(RowNumber()) "fairness" ordering was tried here to
        # interleave multi-shop org results, but DRF's CursorPagination filters the
        # next page via `queryset.filter(<ordering[0]>__gt=<value>)`, and Django
        # disallows filtering on a Window-annotated field (raises NotSupportedError
        # on any page beyond the first). Plain "id" ordering is used instead - the
        # combined multi-shop filter (see shop_to_service_ids above) already returns
        # every requested shop's products; they are just ordered by id rather than
        # interleaved fairly across shops.
        paginator = ProductCursorPagination()
        page_size = paginator.get_page_size(request) or paginator.page_size
        if search_value:
            # Order by price_priority first (priced before null-price), then
            # field-match priority, then id as a stable tie-breaker within each
            # priority group (required for correct cursor paging).
            ordering_fields = ("price_priority", "search_priority", "id")
        else:
            ordering_fields = ("price_priority", "id")
        product_qs = product_qs.order_by(*ordering_fields)
        paginator.ordering = ordering_fields

        date_range = [
            event_date + timedelta(days=i)
            for i in range((return_date - event_date).days + 1)
        ]

        # --- Per-product / per-variant availability check ---
        # Extracted to a helper so it can be run over multiple raw batches
        # (see backfill loop below) without duplicating the logic.
        def build_product_data(product):
            available_variants = []
            product_attributes = {}

            for attribute_value in product.attribute_values.all():
                if attribute_value.attribute and attribute_value.attribute.attribute_key:
                    product_attributes[attribute_value.attribute.attribute_key] = attribute_value.value

            for variant in product.variants.all():

                if event_datetime and return_datetime:
                    # Timestamp-based availability check
                    overlapping_query = BookedProductVariant.objects.filter(
                        product_variant=variant,
                        booking__deleted__isnull=True,
                        booking__booking_status="upcoming",
                    ).exclude(
                        booking__delivery_status__in=["returned", "cancelled"]
                    )

                    overlapping_bookings = overlapping_query.annotate(
                        effective_start=Case(
                            When(
                                booking__cooling_period_end__isnull=False,
                                booking__cooling_period_end__lt=F("booking__pickup_date"),
                                then=F("booking__cooling_period_end"),
                            ),
                            default=F("booking__pickup_date"),
                            output_field=models.DateTimeField(),
                        ),
                        effective_end=Case(
                            When(
                                booking__cooling_period_end__isnull=False,
                                booking__cooling_period_end__gt=F("booking__return_date"),
                                then=F("booking__cooling_period_end"),
                            ),
                            default=F("booking__return_date"),
                            output_field=models.DateTimeField(),
                        ),
                    ).filter(
                        effective_start__lt=return_datetime,
                        effective_end__gt=event_datetime,
                    )

                    total_booked = overlapping_bookings.aggregate(total=Sum("quantity"))["total"] or 0
                    remaining_stock = variant.stock - total_booked
                    is_available = remaining_stock > 0

                    if is_available:
                        variant_data = PublicProductVariantSerializer(variant).data
                        variant_data["remaining_stock"] = remaining_stock
                        available_variants.append(variant_data)

                else:
                    # Date-based availability check
                    availabilities = ProductVariantAvailability.objects.filter(
                        variant=variant,
                        date__in=date_range,
                    )

                    booked_map = {a.date: a.booked_quantity for a in availabilities}

                    remaining_per_day = [
                        variant.stock - booked_map.get(d, 0)
                        for d in date_range
                    ]

                    remaining_stock = min(remaining_per_day) if remaining_per_day else variant.stock

                    is_fully_available = all(
                        booked_map.get(d, 0) < variant.stock for d in date_range
                    )

                    if is_fully_available:
                        variant_data = PublicProductVariantSerializer(variant).data
                        variant_data["remaining_stock"] = remaining_stock
                        available_variants.append(variant_data)

            if not available_variants:
                return None

            product_data = {
                "id": product.id,
                "name": product.name,
                "color": product.color,
                "category": product.category,
                "model": product.model,
                "image": product.image,
                "thumbnail_image": product.thumbnail_image,
                "attributes": product_attributes,
                "variants": available_variants,
            }
            if show_prices:
                product_data["price"] = product.price
            # For org tokens, include the branch shop identity
            if is_org:
                product_data["shop_id"] = product.shop_service.shop_id
                product_data["shop_name"] = product.shop_service.shop.name
                product_data["shop_place"] = product.shop_service.shop.place

            return product_data

        def rows_after(last_obj):
            """Q object selecting rows strictly after last_obj in ordering_fields order
            (keyset/seek pagination), so batches can resume exactly where the previous
            one left off - including across price_priority/search_priority group
            boundaries where a plain id__gt filter would be wrong."""
            q = Q()
            for i, field in enumerate(ordering_fields):
                clause = Q(**{f"{field}__gt": getattr(last_obj, field)})
                for prior_field in ordering_fields[:i]:
                    clause &= Q(**{prior_field: getattr(last_obj, prior_field)})
                q |= clause
            return q

        # --- Cursor pagination with backfill ---
        # paginate_queryset decodes the incoming cursor (resuming at the right
        # position) and returns the first raw batch of up to page_size rows, plus
        # has_next/has_previous for that raw batch.
        first_batch = paginator.paginate_queryset(product_qs, request)

        final_products = []
        first_raw_product = None
        last_raw_product = None
        has_more_raw_rows = paginator.has_next

        batch = first_batch
        while True:
            for product in batch:
                last_raw_product = product
                product_data = build_product_data(product)
                if product_data is not None:
                    if first_raw_product is None:
                        first_raw_product = product
                    final_products.append(product_data)
                    if len(final_products) == page_size:
                        break

            if len(final_products) >= page_size:
                break
            if len(batch) < page_size or not has_more_raw_rows:
                # Raw supply exhausted - nothing more to backfill with.
                has_more_raw_rows = False
                break

            # Not enough available products yet: pull the next raw batch,
            # resuming exactly after the last raw row processed.
            batch = list(product_qs.filter(rows_after(last_raw_product))[:page_size])
            has_more_raw_rows = len(batch) == page_size

        # Note: len(final_products) may exceed page_size if the batch that
        # satisfied the count contained extra available products past the
        # page_size-th one - truncate, but recompute has_next relative to the
        # raw row that actually backs the last *shown* item, not the last raw
        # row processed, so no shown/available product is ever skipped or
        # duplicated on the next page.
        final_products = final_products[:page_size]

        if len(final_products) == page_size:
            # last_raw_product is the raw row backing the last *shown* item
            # (set right before breaking out of the inner loop above), so this
            # correctly reflects whether more rows remain beyond what was shown -
            # regardless of how many extra rows in that same raw batch were
            # skipped for being unavailable.
            has_next = product_qs.filter(rows_after(last_raw_product)).exists()
        else:
            has_next = False

        # DRF's get_next_link()/get_previous_link() derive their cursor
        # positions from page[-1]/page[0] respectively - page must contain
        # the raw rows backing the first and last *shown* items, not just
        # the last one, or the previous-page cursor is computed from the
        # wrong row.
        if first_raw_product is not None:
            paginator.page = [first_raw_product, last_raw_product]
        else:
            paginator.page = []
        paginator.has_next = has_next

        return paginator.get_paginated_response({
            "main_service_name": main_service_name,
            "products": final_products,
        })
