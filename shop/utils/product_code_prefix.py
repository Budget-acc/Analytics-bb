"""
Product code prefix generation
===============================

Derives a 5-character, shop-unique prefix for product codes from a shop's
`name` and `place`:

    [2 chars from name] + [3 chars from place]

Examples:
    ("POSH", "KOCHI")           -> "POKCI"
    ("POSH JEWELLERY", "KOLLAM") -> "PJKLM"

This module is intentionally split into a pure candidate-generation
function (`build_base_prefix`) and a collision-resolving wrapper
(`generate_unique_product_code_prefix`) so callers that already hold a set
of taken prefixes in memory (e.g. a backfill command processing many shops)
can resolve collisions without hitting the database per shop.
"""

import re

PREFIX_LENGTH = 5
NAME_PART_LENGTH = 2
PLACE_PART_LENGTH = 3


def _name_part(name: str) -> str:
    """First letter of each of the first two words; single-word names use
    that word's first two letters. Non-alphanumeric characters are ignored."""
    words = [w for w in re.split(r"\s+", (name or "").strip().upper()) if w]

    if not words:
        return "XX"

    if len(words) == 1:
        word = words[0]
        return (word + "XX")[:NAME_PART_LENGTH]

    return "".join(word[0] for word in words[:NAME_PART_LENGTH])


def _place_part(place: str) -> str:
    """First / middle (floor((n-1)/2)) / last character of place, uppercased.
    Whitespace is stripped; a place shorter than 3 chars is padded by
    repeating its last character."""
    cleaned = re.sub(r"\s+", "", (place or "").strip().upper())

    if not cleaned:
        return "X" * PLACE_PART_LENGTH

    while len(cleaned) < PLACE_PART_LENGTH:
        cleaned += cleaned[-1]

    first = cleaned[0]
    last = cleaned[-1]
    mid_index = (len(cleaned) - 1) // 2
    mid = cleaned[mid_index]

    return f"{first}{mid}{last}"


def build_base_prefix(name: str, place: str) -> str:
    """Pure function: derives the base 5-char candidate from name + place.
    Does not check uniqueness — callers resolve collisions separately."""
    return f"{_name_part(name)}{_place_part(place)}"


class ProductCodePrefixGenerationError(Exception):
    """Raised when no unique prefix could be derived after exhausting the
    deterministic collision-resolution fallbacks."""


def generate_unique_product_code_prefix(name: str, place: str, taken_prefixes) -> str:
    """
    Returns a `product_code_prefix` guaranteed not to be in `taken_prefixes`
    (any iterable/set of already-used prefix strings).

    Resolution order:
      1. The base 5-char candidate from name + place.
      2. Base with its last character replaced by digits "1"-"9".
      3. Base with its last two characters replaced by "01"-"99".
      4. Raises ProductCodePrefixGenerationError (should never happen in
         practice) so the caller can surface a clear error rather than
         silently mangling a meaningful prefix.

    `taken_prefixes` is checked purely in memory — callers are responsible
    for loading the relevant existing prefixes up front (e.g. one query per
    backfill run, not one per shop).
    """
    taken = set(taken_prefixes)
    base = build_base_prefix(name, place)

    if base not in taken:
        return base

    for digit in "123456789":
        candidate = base[:4] + digit
        if candidate not in taken:
            return candidate

    for n in range(1, 100):
        candidate = base[:3] + f"{n:02d}"
        if candidate not in taken:
            return candidate

    raise ProductCodePrefixGenerationError(
        f"Could not generate a unique product_code_prefix for name={name!r}, place={place!r} "
        f"(base candidate {base!r} and all deterministic fallbacks are taken)."
    )
