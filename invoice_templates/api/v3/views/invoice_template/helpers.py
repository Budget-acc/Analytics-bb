from invoice_templates.v3.registry.fields import font_options

OPTIONS_SHAPE = (
    "- defaults (object): the template's default settings - colors {background, primary, text, "
    "border, cancelled} (each \"#RRGGBB\") and font (string).\n"
    "- fonts (array): fonts the shop can choose - [{value, label}]. Exactly the fonts whose "
    "files are installed with the application; nothing else is listed."
)

OPTIONS_EXAMPLE = {
    "defaults": {
        "colors": {"background": "#FDF5EC", "primary": "#2B2B2B", "text": "#1F1F1F",
                   "border": "#2B2B2B", "cancelled": "#D32F2F"},
        "font": "inter",
    },
    "fonts": [
        {"value": "inter", "label": "Inter"},
        {"value": "noto_sans_malayalam", "label": "Noto Sans Malayalam"},
        {"value": "dejavu_sans", "label": "DejaVu Sans"},
        {"value": "cormorant_garamond", "label": "Cormorant Garamond"},
        {"value": "roboto_mono", "label": "Roboto Mono"},
    ],
}


def template_options_payload(definition) -> dict:
    """The six customizable values' defaults + the font list."""
    return {"defaults": definition.defaults, "fonts": font_options()}
