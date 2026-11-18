from ..mixins import InvoiceTemplateBaseViewSet


class InvoiceTemplateViewSet(InvoiceTemplateBaseViewSet):
    """
    Predefined invoice templates and the active shop's settings for them:
    list, detail (schema + defaults + saved settings), save, reset and a
    one-page PDF preview. Templates are addressed by their stable `code`.
    """
    lookup_field = "code"
    lookup_value_regex = "[a-z0-9_-]+"
