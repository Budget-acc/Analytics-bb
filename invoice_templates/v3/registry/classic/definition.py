"""
Classic template - the reference design (cream page, centered display title,
logo panel top-right, bordered item table, totals on the right).

Fixed by the design: the Cormorant Garamond Regular title font and the font sizes from the
reference spec (title 32, shop name 15, cancelled 13, section headings 11,
product specifications 7, grand total 13, watermark 7, others 10), labels,
layout and spacing. The shop customizes only the five colors and the font.
"""
from ..base import TemplateDefinition

DEFINITION = TemplateDefinition(
    code="classic",
    name="Classic",
    description="Elegant cream invoice with a centered display title and the shop logo panel at the top.",
    version=2,
    default_colors={
        "background": "#FDF5EC",
        "primary": "#2B2B2B",
        "text": "#1F1F1F",
        "border": "#2B2B2B",
        "cancelled": "#D32F2F",
    },
    default_font="inter",
    renderer_path="invoice_templates.v3.registry.classic.renderer:ClassicRenderer",
    display_order=2,
)
