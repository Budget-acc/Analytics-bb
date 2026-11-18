"""
Simple template - clean white invoice (shop logo + details top-left, bold
"INVOICE" top-right, CANCELLED label beside the customer block, borderless
item table, totals on the right, purple accents).

Fixed by the design: the font sizes from the reference spec (title 30, shop
name 15, shop details 8, cancelled 12, product specifications 6, grand total
12, watermark 8, others 10), labels, layout and spacing. The shop customizes
only the five colors and the font.
"""
from ..base import TemplateDefinition

DEFINITION = TemplateDefinition(
    code="simple",
    name="Simple",
    description="Clean white invoice with the shop logo and details at the top-left and a bold title.",
    version=1,
    default_colors={
        "background": "#FFFFFF",
        "primary": "#7C3AED",
        "text": "#1F2937",
        "border": "#E5E7EB",
        "cancelled": "#EF4444",
    },
    default_font="inter",
    renderer_path="invoice_templates.v3.registry.simple.renderer:SimpleRenderer",
    display_order=3,
)
