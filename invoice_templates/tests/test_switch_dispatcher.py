from types import SimpleNamespace as NS
from unittest import mock

from django.test import SimpleTestCase, override_settings

from invoice_templates.v3 import dispatcher, switch
from whatsapp_integration.v3 import pdf_template as legacy

from .fixtures import make_booking, make_client, make_sale, make_shop, make_tailor_order

SHOP = NS(id=7)


class SwitchTests(SimpleTestCase):
    @override_settings(INVOICE_CUSTOMIZATION_ENABLED=False)
    def test_globally_off_never_queries(self):
        with mock.patch.object(switch.ShopInvoicePreference, "objects") as objects:
            self.assertFalse(switch.is_customization_enabled(SHOP))
        objects.filter.assert_not_called()

    @override_settings(INVOICE_CUSTOMIZATION_ENABLED=True)
    def test_globally_on_checks_the_shop(self):
        with mock.patch.object(switch.ShopInvoicePreference, "objects") as objects:
            objects.filter.return_value.exists.return_value = True
            self.assertTrue(switch.is_customization_enabled(SHOP))
            objects.filter.assert_called_once_with(shop_id=7, is_customization_enabled=True)
            objects.filter.return_value.exists.return_value = False
            self.assertFalse(switch.is_customization_enabled(SHOP))


class DispatcherTests(SimpleTestCase):
    kwargs = dict(shop=SHOP, client=None, obj=None, items=[])

    def test_switch_off_returns_none(self):
        with mock.patch.object(dispatcher, "is_customization_enabled", return_value=False), \
                mock.patch("invoice_templates.v3.services.rendering.render_invoice_base64") as render:
            self.assertIsNone(dispatcher.try_render_customized("booking", **self.kwargs))
        render.assert_not_called()

    def test_switch_on_renders(self):
        with mock.patch.object(dispatcher, "is_customization_enabled", return_value=True), \
                mock.patch("invoice_templates.v3.services.rendering.render_invoice_base64", return_value="QkFTRTY0"):
            self.assertEqual(dispatcher.try_render_customized("sale", **self.kwargs), "QkFTRTY0")

    @override_settings(INVOICE_CUSTOMIZATION_FALLBACK_ON_ERROR=True)
    def test_render_error_falls_back_to_existing_invoice(self):
        with mock.patch.object(dispatcher, "is_customization_enabled", return_value=True), \
                mock.patch("invoice_templates.v3.services.rendering.render_invoice_base64", side_effect=RuntimeError):
            self.assertIsNone(dispatcher.try_render_customized("booking", **self.kwargs))

    @override_settings(INVOICE_CUSTOMIZATION_FALLBACK_ON_ERROR=False)
    def test_render_error_can_be_raised(self):
        with mock.patch.object(dispatcher, "is_customization_enabled", return_value=True), \
                mock.patch("invoice_templates.v3.services.rendering.render_invoice_base64", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                dispatcher.try_render_customized("booking", **self.kwargs)

    def test_switch_failure_never_breaks_invoices(self):
        with mock.patch.object(dispatcher, "is_customization_enabled", side_effect=RuntimeError("db down")):
            self.assertIsNone(dispatcher.try_render_customized("booking", **self.kwargs))


class LegacyEntryPointTests(SimpleTestCase):
    """The existing generators short-circuit only when the dispatcher returns a PDF."""

    def call_all(self):
        booking, booking_items = make_booking(1)
        sale, sale_items = make_sale(1)
        order, order_items = make_tailor_order(1)
        return [
            legacy.BookingPDFService.generate_transaction_pdf(make_shop(), make_client(), booking, booking_items),
            legacy.SalesPDFService.generate_sale_pdf(make_shop(), make_client(), sale, sale_items),
            legacy.TailorOrderPDFService.generate_tailor_order_pdf(make_shop(), make_client(), order, order_items),
        ]

    def test_customized_result_is_returned_as_is(self):
        with mock.patch("invoice_templates.v3.dispatcher.try_render_customized", return_value="CUSTOM") as hook, \
                mock.patch.object(legacy.BasePDFService, "generate_pdf") as legacy_pdf:
            self.assertEqual(self.call_all(), ["CUSTOM", "CUSTOM", "CUSTOM"])
        legacy_pdf.assert_not_called()
        self.assertEqual([c.args[0] for c in hook.call_args_list], ["booking", "sale", "tailor_order"])

    def test_existing_implementation_runs_when_switch_is_off(self):
        with mock.patch("invoice_templates.v3.dispatcher.try_render_customized", return_value=None), \
                mock.patch("shop.models.ShopNumberingSequence.objects"), \
                mock.patch.object(legacy.BasePDFService, "generate_pdf", return_value=b"%PDF-legacy") as legacy_pdf:
            results = self.call_all()
        self.assertEqual(legacy_pdf.call_count, 3)
        self.assertTrue(all(isinstance(r, str) and r for r in results))
