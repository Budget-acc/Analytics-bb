from django.db.models import Case, When, Value, IntegerField, Q
from shop.models import Shop, Organization

# Field priority order for free-text product search: name > color > model > category.
SEARCH_FIELDS = ["name", "color", "model", "category"]
# Match-tier priority within each field, in rank order.
SEARCH_TIERS = ["exact", "starts_with", "contains"]


def build_search_priority_case(search_value):
    """
    Returns a Django Case/When expression ranking a product 0-11 by how it matches
    `search_value` across SEARCH_FIELDS x SEARCH_TIERS (field-major, then match-tier),
    e.g. 0 = name exact, 1 = name starts-with, ..., 11 = category contains.

    Case evaluates its When() clauses top-to-bottom and stops at the first match, so
    this single annotation both ranks and (implicitly) classifies each product - the
    same precedence table is mirrored in classify_product_match() for response grouping,
    so SQL ordering and response bucketing can never disagree.

    Intended for use as `.annotate(search_priority=build_search_priority_case(search_value))`
    on a queryset already filtered to only matching rows.
    """
    whens = []
    rank = 0
    for field in SEARCH_FIELDS:
        whens.append(When(**{f"{field}__iexact": search_value}, then=Value(rank)))
        rank += 1
        whens.append(When(**{f"{field}__istartswith": search_value}, then=Value(rank)))
        rank += 1
        whens.append(When(**{f"{field}__icontains": search_value}, then=Value(rank)))
        rank += 1
    return Case(*whens, default=Value(rank), output_field=IntegerField())


def build_search_filter_q(search_value):
    """
    OR filter across SEARCH_FIELDS using icontains - a superset of exact/starts-with,
    so this single filter still captures every product that belongs in any of the
    12 priority buckets. No extra query is needed to also cover the exact/starts-with
    tiers - those are handled purely by build_search_priority_case()'s ordering.
    """
    q = Q()
    for field in SEARCH_FIELDS:
        q |= Q(**{f"{field}__icontains": search_value})
    return q


def classify_product_match(product, search_value):
    """
    Pure-Python equivalent of build_search_priority_case(), evaluated against an
    already-fetched product instance's name/color/model/category (no DB access).

    Returns (field, tier) e.g. ("name", "exact") for the single highest-priority
    match - the same first-match-wins precedence table used in the SQL annotation,
    so a product is always classified into exactly one response bucket. Returns
    None if the product doesn't actually match (shouldn't happen for a product
    returned by a queryset filtered with build_search_filter_q()).
    """
    needle = (search_value or "").strip().lower()
    if not needle:
        return None
    for field in SEARCH_FIELDS:
        haystack = (getattr(product, field, None) or "").strip().lower()
        if not haystack:
            continue
        if haystack == needle:
            return field, "exact"
        if haystack.startswith(needle):
            return field, "starts_with"
        if needle in haystack:
            return field, "contains"
    return None


def get_public_shop(public_token: str):
    """
    Returns the Shop matching the given public_token if it is active and
    has public listing enabled.  Returns None when no such shop exists.
    """
    try:
        return Shop.objects.get(
            public_token=public_token,
            public_listing_enabled=True,
            is_active=True,
            deleted__isnull=True,
        )
    except Shop.DoesNotExist:
        return None


def resolve_public_token(public_token: str):
    """
    Identifies token type by length — no dual-table lookup needed:
      12 chars → Shop token   → query Shop table only
      10 chars → Org token    → query Organization table only
      other    → (None, None)

    Returns ("shop", shop_instance), ("org", org_instance), or (None, None).
    """
    if len(public_token) == 12:
        try:
            return "shop", Shop.objects.get(
                public_token=public_token,
                public_listing_enabled=True,
                is_active=True,
                deleted__isnull=True,
            )
        except Shop.DoesNotExist:
            return None, None

    if len(public_token) == 10:
        try:
            return "org", Organization.objects.get(
                public_token=public_token,
                is_active=True,
                public_listing_enabled=True,
            )
        except Organization.DoesNotExist:
            return None, None

    return None, None


def parse_int_list_param(request, param_name: str):
    """
    Parses a query param that may be supplied as a comma-separated list
    and/or repeated params (e.g. ids=1,2 or ids=1&ids=2) into a deduped
    list of ints. Non-integer values are silently dropped.

    Returns (raw_values, id_list) where raw_values is the untouched
    getlist() result (useful for distinguishing "param not supplied at
    all" from "param supplied but all values were invalid").
    """
    raw_values = request.GET.getlist(param_name)
    flattened = []
    for v in raw_values:
        flattened.extend(str(v).split(","))
    flattened = [x.strip() for x in flattened]
    id_list = [int(x) for x in flattened if x.isdigit()]
    return raw_values, id_list
