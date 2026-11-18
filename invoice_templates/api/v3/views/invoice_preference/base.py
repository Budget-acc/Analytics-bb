from ..mixins import InvoiceTemplateBaseViewSet


class InvoicePreferenceViewSet(InvoiceTemplateBaseViewSet):
    """
    The active shop's invoice preferences - kept separate from template
    settings: the default template, the customization on/off switch and the
    document-specific Terms and Conditions.
    """
