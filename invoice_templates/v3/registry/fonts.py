"""
Fonts available to invoice templates: exactly the font files supplied in the
static fonts directory (`<STATIC_ROOT>/fonts/` and `<STATICFILES_DIRS>/fonts/`,
scanned recursively) - nothing else is listed or used.

Each readable static .ttf is grouped by its family name (name ID 16, else 1)
with its weight from OS/2.usWeightClass. The key is a slug of the family
("Inter" -> inter, "Noto Sans Malayalam" -> noto_sans_malayalam), so keys stay
stable as long as the files do. To add a font, drop its .ttf files into the
fonts directory (licensed for embedding in server-generated PDFs) and restart.

Fonts are registered under reserved "BB <family>" names, so a font installed
on the machine with the same name can never be used instead. Font stacks list
only bundled fonts; Noto Sans Malayalam and Inter follow the chosen font only
for characters it does not have (Malayalam text, the ₹ sign).
"""
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from fontTools.ttLib import TTFont

from whatsapp_integration.v3.pdf_template import BasePDFService

logger = logging.getLogger("api_requests")

FONTS_SUBDIR = "fonts"
FAMILY_PREFIX = "BB "
# Bundled fonts that cover what other fonts may lack: Malayalam text, then Latin / ₹.
GLYPH_FALLBACK_KEYS = ("noto_sans_malayalam", "inter")
# Listed first in the font picker; the rest follow alphabetically.
PREFERRED_ORDER = ("inter", "noto_sans_malayalam", "dejavu_sans")


def _font_dirs():
    """The static fonts directories (no app registry needed, so safe at import time)."""
    roots = list(getattr(settings, "STATICFILES_DIRS", None) or [])
    if getattr(settings, "STATIC_ROOT", None):
        roots.append(settings.STATIC_ROOT)
    dirs = []
    for root in roots:
        root = root[1] if isinstance(root, (list, tuple)) else root  # (prefix, path) entries
        path = Path(root) / FONTS_SUBDIR
        if path.is_dir() and path.resolve() not in dirs:
            dirs.append(path.resolve())
    return dirs


def _slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", family.lower()).strip("_")


def _read_font(path: Path):
    """(family, weight) of a usable static, upright font file, else None."""
    try:
        font = TTFont(str(path), lazy=True)
        if "fvar" in font:
            return None  # variable fonts are not used
        if font["OS/2"].fsSelection & 0x01:
            return None  # italic files are not separate picker entries
        names = font["name"]
        family = names.getDebugName(16) or names.getDebugName(1)
        weight = int(font["OS/2"].usWeightClass)
        font.close()
    except Exception as e:  # unreadable / not a font
        logger.info(f"Invoice font file skipped ({path.name}): {type(e).__name__}: {e}")
        return None
    if not family:
        return None
    return family.strip(), weight


@lru_cache(maxsize=1)
def font_registry() -> dict:
    """{key: {"label", "family", "files": {weight: absolute path}}} for every supplied font."""
    registry = {}
    for directory in _font_dirs():
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() != ".ttf" or not path.is_file():
                continue
            info = _read_font(path)
            if info is None:
                continue
            family, weight = info
            entry = registry.setdefault(_slug(family), {"label": family, "family": family, "files": {}})
            entry["files"].setdefault(weight, str(path))
    order = {key: index for index, key in enumerate(PREFERRED_ORDER)}
    return dict(sorted(registry.items(), key=lambda item: (order.get(item[0], len(order)), item[1]["label"])))


FONT_REGISTRY = font_registry()
FONT_LABELS = {key: data["label"] for key, data in FONT_REGISTRY.items()}


def css_family(font_key: str) -> str:
    """Reserved @font-face family name of a bundled font."""
    return f"{FAMILY_PREFIX}{FONT_REGISTRY[font_key]['family']}"


def _stack_keys(font_key: str) -> list:
    keys = [font_key] + [key for key in GLYPH_FALLBACK_KEYS if key != font_key]
    return [key for key in keys if key in FONT_REGISTRY]


def font_stack(font_key: str) -> str:
    """CSS font-family stack: the chosen bundled font, then bundled glyph fallbacks only."""
    return ", ".join(f"'{css_family(key)}'" for key in _stack_keys(font_key))


@lru_cache(maxsize=None)
def _font_face_rules(font_key: str) -> str:
    rules = []
    for weight, path in sorted(FONT_REGISTRY[font_key]["files"].items()):
        if not os.path.exists(path):
            continue
        url = Path(path).resolve().as_uri()
        rules.append(
            f"@font-face {{ font-family: '{css_family(font_key)}'; src: url('{url}'); "
            f"font-weight: {weight}; font-style: normal; }}"
        )
    return "\n".join(rules)


def font_face_css(font_keys) -> str:
    """
    @font-face CSS: the legacy invoice fonts (used by the Standard template's
    unchanged base stylesheet) plus every bundled font the given keys' stacks
    reference. No keys -> exactly the legacy rules.
    """
    css = [BasePDFService.get_font_css()]
    needed = {stack_key for key in font_keys if key in FONT_REGISTRY for stack_key in _stack_keys(key)}
    css += [_font_face_rules(key) for key in sorted(needed)]
    return "\n".join(rule for rule in css if rule)
