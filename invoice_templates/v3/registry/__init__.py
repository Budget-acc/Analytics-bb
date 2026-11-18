"""
Code registry of predefined invoice templates.

To add a template: create registry/<code>/{definition.py, renderer.py} and
markup under templates/invoice_templates/<code>/, add the definition below,
then run `python manage.py sync_invoice_templates` (or ship a data
migration) to create its catalog row. No schema change is needed.
"""
from .classic.definition import DEFINITION as CLASSIC
from .simple.definition import DEFINITION as SIMPLE
from .standard.definition import DEFINITION as STANDARD

DEFAULT_TEMPLATE_CODE = STANDARD.code

REGISTRY = {definition.code: definition for definition in (STANDARD, CLASSIC, SIMPLE)}


def get_definition(code):
    """TemplateDefinition for a code, or None when the code is unknown."""
    return REGISTRY.get(code)
