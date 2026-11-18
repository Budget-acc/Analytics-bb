from ..mixins import InvoiceTemplateBaseViewSet


class CustomInvoiceViewSet(InvoiceTemplateBaseViewSet):
    """
    The active shop's customized invoices (name + template + settings):
    list, create (limited per shop), retrieve, update (unlimited), delete and
    a one-page preview. Every lookup is scoped to the active shop, so another
    shop's id is simply "not found".
    """
    lookup_value_regex = r"\d+"
