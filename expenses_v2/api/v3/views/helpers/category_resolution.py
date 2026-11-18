from expenses_v2.models import ShopCategory


def resolve_category_and_type(data, shop_id):
    """
    Synchronizes `type` and `category` in request data (mutates data in place).

    Priority: category id > type string
    - category provided  → validate it, then overwrite type with category.name
    - type provided only → look up matching ShopCategory (iexact);
                           create one if none exists; always sync type = category.name
    - neither provided   → no change
    - both provided      → category wins, type is overwritten
    """
    category_id = data.get('category')
    type_value = (data.get('type') or '').strip()

    if category_id:
        category = ShopCategory.objects.filter(pk=category_id, shop_id=shop_id).first()
        if not category:
            return {'category': ['Invalid category.']}
        if not category.is_active:
            return {'category': ['This category is inactive.']}
        data['type'] = category.name
        data['category'] = category.pk

    elif type_value:
        category = ShopCategory.objects.filter(
            shop_id=shop_id, name__iexact=type_value
        ).first()

        if not category:
            # No matching category — create one so type and category stay in sync.
            # Wrap in try/except to handle the rare race where two concurrent requests
            # create the same category simultaneously (DB unique constraint fires).
            from django.db import IntegrityError
            try:
                category = ShopCategory.objects.create(
                    shop_id=shop_id,
                    name=type_value,
                    type='expense',
                )
            except IntegrityError:
                # Another request created it first — fetch the existing one.
                category = ShopCategory.objects.filter(
                    shop_id=shop_id, name__iexact=type_value
                ).first()

        if category:
            data['category'] = category.pk
            data['type'] = category.name  # sync type to the stored name (preserves original casing)

    return None
