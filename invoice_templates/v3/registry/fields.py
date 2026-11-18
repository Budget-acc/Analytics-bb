"""
The customization options a shop can set on an invoice template.

Only six values are customizable; everything else (layout, font sizes,
weights, labels, spacing) is fixed by each template's design:

    {
      "colors": {"background": "#RRGGBB", "primary": "#RRGGBB", "text": "#RRGGBB",
                 "border": "#RRGGBB", "cancelled": "#RRGGBB"},
      "font": "<one of FONT_CHOICES>"
    }
"""
import re
from typing import Optional, Tuple

from .fonts import FONT_LABELS

COLOR_KEYS = ("background", "primary", "text", "border", "cancelled")

# Fonts a shop can choose: every font whose files are supplied in the static fonts directory.
FONT_CHOICES = tuple(FONT_LABELS)

# Validation messages (exact strings - also documented in swagger)
ERR_UNKNOWN_FIELD = "'{key}' is not a supported setting."
ERR_COLOR = "'{key}' must be a color in #RRGGBB format."
ERR_FONT = "'font' must be one of: {options}."
ERR_COLORS_OBJECT = "'colors' must be an object."

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate_color(key: str, value) -> Tuple[Optional[str], Optional[str]]:
    """(clean '#RRGGBB' upper-case, None) or (None, error)."""
    if isinstance(value, str) and _COLOR_RE.match(value.strip()):
        return value.strip().upper(), None
    return None, ERR_COLOR.format(key=f"colors.{key}")


def validate_font(value) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(value, str) and value in FONT_CHOICES:
        return value, None
    return None, ERR_FONT.format(options=", ".join(FONT_CHOICES))


def font_options() -> list:
    """[{value, label}] for the font picker."""
    return [{"value": key, "label": FONT_LABELS[key]} for key in FONT_CHOICES]
