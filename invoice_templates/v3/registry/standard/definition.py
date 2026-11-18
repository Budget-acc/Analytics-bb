"""
Standard template - the existing BookieBuddy invoice design.

The defaults are the colors and font the existing invoice uses
(BasePDFService.get_base_css), so Standard with default settings looks
exactly like today's invoice. Everything other than the six customizable
values is fixed by the design.
"""
from ..base import TemplateDefinition

DEFINITION = TemplateDefinition(
    code="standard",
    name="Standard",
    description="The classic BookieBuddy invoice with purple accents and decorative page background.",
    version=2,
    default_colors={
        "background": "#FFFFFF",
        "primary": "#6C5CE7",
        "text": "#374151",
        "border": "#D9D9D9",
        "cancelled": "#EF4444",
    },
    default_font="inter",
    renderer_path="invoice_templates.v3.registry.standard.renderer:StandardRenderer",
    display_order=1,
)
