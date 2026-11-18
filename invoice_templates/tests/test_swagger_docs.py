"""Swagger docs: every operation is tagged, and every message it can return is documented."""
import inspect
import re

from django.test import SimpleTestCase
from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator

from invoice_templates.api.v3.utils import messages
from invoice_templates.api.v3.views import (
    CustomInvoiceViewSet, InvoicePreferenceViewSet, InvoiceTemplateViewSet, TemplateShareViewSet,
)

PREFIX = "/api/v3/invoice-templates/"
TAG = "Invoice Template Management V3"

# (path, method) -> view function
OPERATIONS = {
    ("templates/", "get"): InvoiceTemplateViewSet.list,
    ("templates/{code}/", "get"): InvoiceTemplateViewSet.retrieve,
    ("templates/{code}/preview/", "post"): InvoiceTemplateViewSet.preview,
    ("custom-invoices/", "get"): CustomInvoiceViewSet.list,
    ("custom-invoices/", "post"): CustomInvoiceViewSet.create,
    ("custom-invoices/{id}/", "get"): CustomInvoiceViewSet.retrieve,
    ("custom-invoices/{id}/", "patch"): CustomInvoiceViewSet.partial_update,
    ("custom-invoices/{id}/", "delete"): CustomInvoiceViewSet.destroy,
    ("custom-invoices/{id}/preview/", "post"): CustomInvoiceViewSet.preview_custom_invoice,
    ("preference/", "get"): InvoicePreferenceViewSet.list,
    ("preference/default/", "put"): InvoicePreferenceViewSet.set_default,
    ("preference/customization/", "patch"): InvoicePreferenceViewSet.toggle_customization,
    ("preference/terms/", "patch"): InvoicePreferenceViewSet.update_terms,
    ("share/{custom_invoice_id}/", "get"): TemplateShareViewSet.retrieve,
    ("share/{custom_invoice_id}/", "delete"): TemplateShareViewSet.destroy,
    ("share/{custom_invoice_id}/generate/", "post"): TemplateShareViewSet.generate,
    ("share/lookup/{share_code}/", "get"): TemplateShareViewSet.lookup,
    ("share/import/", "post"): TemplateShareViewSet.import_shared,
}

# Shared helpers whose messages are documented in the common responses.
HELPER_MESSAGES = {"SERVER_ERROR", "SERVER_ERROR_DEV"}

# Operations that return messages through a shared helper; that helper's
# source is scanned too, so moving a response into it can't hide it from
# the documentation check.
OPERATION_HELPERS = {
    ("share/import/", "post"): [TemplateShareViewSet.share_error_response],
    ("share/lookup/{share_code}/", "get"): [TemplateShareViewSet.share_error_response],
}

# share_error_response maps every ShareError reason, but lookup never raises
# OWN_CODE (it reports is_own_code instead), so it is not documented there.
NOT_RETURNED = {("share/lookup/{share_code}/", "get"): {"OWN_SHARE_CODE"}}


class SwaggerDocsTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patterns = [path(PREFIX[1:], include("invoice_templates.api.v3.urls"))]
        cls.schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)

    def operation(self, route, method):
        return self.schema["paths"][PREFIX + route][method]

    def test_all_operations_documented_and_tagged(self):
        documented = {
            (p[len(PREFIX):], m) for p, ops in self.schema["paths"].items() for m in ops if m != "parameters"
        }
        self.assertEqual(documented, set(OPERATIONS))
        for (route, method) in OPERATIONS:
            op = self.operation(route, method)
            self.assertEqual(op["tags"], [TAG], (route, method))
            self.assertTrue(op.get("summary") and op.get("description"), (route, method))
            self.assertIn("X-Active-Shop-ID", [p["name"] for p in op.get("parameters", [])], (route, method))

    def test_returned_messages_are_documented(self):
        for (route, method), func in OPERATIONS.items():
            op = self.operation(route, method)
            documented = " ".join(r.get("description", "") for r in op["responses"].values())
            source = "\n".join(
                inspect.getsource(inspect.unwrap(f)) for f in [func, *OPERATION_HELPERS.get((route, method), [])]
            )
            # User-facing messages only (dev_message is developer guidance, not an error message).
            returned = set(re.findall(r"(?<!dev_)message=messages\.([A-Z_]+)", source))
            for constant in returned - HELPER_MESSAGES - NOT_RETURNED.get((route, method), set()):
                value = getattr(messages, constant)
                self.assertIn(value, documented, f"{method.upper()} {route}: '{value}' not documented")

    def test_permission_messages_documented_everywhere(self):
        for (route, method) in OPERATIONS:
            description = self.operation(route, method)["responses"]["403"]["description"]
            for message in messages.PERMISSION_MESSAGES:
                self.assertIn(message, description, (route, method))
