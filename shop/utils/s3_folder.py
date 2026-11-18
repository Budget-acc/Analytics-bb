"""
Shop S3 folder naming.

Every shop-scoped S3 key starts with a top-level folder segment
(e.g. "BB_Shop/product_images/..."). Historically that segment was
re-derived from the *current* Shop.name on every upload, so renaming a shop
started a new folder. The folder is now stored once on
Shop.s3_folder_name and read via Shop.s3_folder.

- legacy_s3_segment(): the EXACT transform the product-image upload paths
  used before Shop.s3_folder_name existed. Only used as the fallback for
  shops that have not been backfilled yet, and for backfill matching - so a
  not-yet-mapped shop keeps uploading to the same folder it always did.
- build_s3_folder_name(): the standard name for NEW shops (trimmed, spaces
  -> "_", no leading/trailing "_").
- allocate_s3_folder_name(): build_s3_folder_name() plus a guarantee that no
  other shop already owns the folder.
"""
import re

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")
_REPEATED_UNDERSCORES = re.compile(r"_+")
MAX_LEN = 50
MAX_SUFFIX = 100


def legacy_s3_segment(value: str, max_len: int = MAX_LEN) -> str:
    """Replace non-alphanumeric characters (except _ and -) and truncate.

    Unchanged legacy behaviour - do not "improve" this, existing S3 folders
    and keys depend on it producing exactly the same output.
    """
    return _UNSAFE_CHARS.sub("_", value)[:max_len]


def build_s3_folder_name(name: str) -> str:
    """Standard S3 folder name for a new shop.

    "BB Shop" -> "BB_Shop", "  BB   Shop  " -> "BB_Shop",
    "BB's Shop" -> "BB_s_Shop", "_BB Shop_" -> "BB_Shop".
    Returns "" when nothing usable is left (callers supply a fallback).
    """
    base = "_".join((name or "").split())
    base = _UNSAFE_CHARS.sub("_", base)
    base = _REPEATED_UNDERSCORES.sub("_", base).strip("_")
    return base[:MAX_LEN].rstrip("_")


def candidate_s3_folder_names(name, place=None):
    """Yields folder names in preference order: base, base_<place>, base_2..."""
    base = build_s3_folder_name(name) or "shop"
    yield base
    place_seg = build_s3_folder_name(place or "")
    if place_seg:
        yield f"{base}_{place_seg}"[:MAX_LEN].rstrip("_")
    for n in range(2, MAX_SUFFIX):
        yield f"{base}_{n}"


def allocate_s3_folder_name(name, place=None, exclude_shop_id=None, extra_taken=None):
    """Returns a standard folder name that no other shop (incl. soft-deleted
    ones, whose objects still live in S3) is mapped to.

    `extra_taken` lets callers (e.g. the backfill command) add names that are
    reserved but not yet in the DB - folders already present in S3, or names
    assigned earlier in the same run.
    """
    from shop.models import Shop

    base = build_s3_folder_name(name) or "shop"
    taken_qs = Shop.all_objects.filter(s3_folder_name__startswith=base)
    if exclude_shop_id is not None:
        taken_qs = taken_qs.exclude(pk=exclude_shop_id)
    taken = set(taken_qs.values_list("s3_folder_name", flat=True))
    if extra_taken:
        taken |= set(extra_taken)

    for candidate in candidate_s3_folder_names(name, place):
        if candidate not in taken:
            return candidate
    raise ValueError(f"Could not allocate a free S3 folder name for shop name {name!r}")
