"""
Template definition + renderer contracts.

A TemplateDefinition is the single source of truth for one predefined
template: its identity, version, default colors/font and the renderer that
turns an InvoiceContext + configuration into HTML/CSS.
"""
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from typing import Callable, Dict, Optional, Set

from .fields import COLOR_KEYS, validate_color, validate_font

# Watermark printed at the bottom of every page by every template.
WATERMARK_TEXT = "Powered by Bookie Buddy"


@dataclass(frozen=True)
class TemplateDefinition:
    code: str
    name: str
    description: str
    version: int
    default_colors: Dict[str, str]          # one value per COLOR_KEYS
    default_font: str                       # one of FONT_CHOICES
    renderer_path: str                      # "module.path:ClassName" (lazy import)
    display_order: int = 0
    # {from_version: fn(config) -> config}; applied in order up to `version`
    migrations: Dict[int, Callable[[dict], dict]] = field(default_factory=dict)

    def __post_init__(self):
        # A definition with invalid defaults is a programming error - fail at import time.
        if set(self.default_colors) != set(COLOR_KEYS):
            raise ValueError(f"{self.code}: default_colors must define exactly {COLOR_KEYS}")
        for key, value in self.default_colors.items():
            if validate_color(key, value)[1]:
                raise ValueError(f"{self.code}: invalid default color {key}={value}")
        if validate_font(self.default_font)[1]:
            raise ValueError(f"{self.code}: invalid default font {self.default_font}")

    @property
    def defaults(self) -> dict:
        """Complete default configuration (a fresh copy every time)."""
        return {
            "colors": {key: validate_color(key, self.default_colors[key])[0] for key in COLOR_KEYS},
            "font": self.default_font,
        }

    @cached_property
    def renderer_class(self):
        module_path, class_name = self.renderer_path.split(":")
        return getattr(import_module(module_path), class_name)


@dataclass
class RenderParts:
    """
    What a renderer produces. The pipeline (services/rendering.py) measures
    `main_html` + `footer_html` to place the Terms & Conditions block, then
    assembles the document exactly like the existing invoices do.
    """
    main_html: str                          # goes inside .content > .main-content
    footer_html: str                        # goes inside .footer-content (terms)
    after_html: str                         # after the footer (measurements page)
    css: str                                # layout CSS (used for measuring too)
    page_css: str = ""                      # @page backgrounds (render only)
    font_keys: Set[str] = field(default_factory=set)


class BaseRenderer:
    def __init__(self, definition: TemplateDefinition, config: dict):
        self.definition = definition
        self.config = config

    def color(self, key) -> str:
        return self.config["colors"][key]

    @property
    def font(self) -> str:
        return self.config["font"]

    def is_default(self, key) -> bool:
        """True when a color (or 'font') equals the template default."""
        if key == "font":
            return self.font == self.definition.default_font
        return self.color(key) == self.definition.defaults["colors"][key]

    def render(self, context) -> RenderParts:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def label_for(row, mapping: Optional[dict] = None) -> str:
        """Template label for a semantic total key, else the data label."""
        if mapping and not row.dynamic and row.key in mapping:
            return mapping[row.key]
        return row.label
