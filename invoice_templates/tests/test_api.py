"""Database-backed API tests (require PostgreSQL - the project's migrations do not run on SQLite)."""
import base64
from unittest import mock

from invoice_templates.api.v3.utils import messages
from invoice_templates.models import InvoiceTemplateShare, ShopCustomInvoice, ShopInvoicePreference
from invoice_templates.v3.services.configuration import resolve_for_shop
from whatsapp_integration.v3 import pdf_template as legacy
from whatsapp_integration.v3.pdf_template import DEFAULT_SALE_TERMS, DEFAULT_TAILOR_ORDER_TERMS

from .base import InvoiceTemplateAPITestCase
from .fixtures import make_booking, make_client

PDF = b"%PDF-1.7 preview"
RENDER_PREVIEW = "invoice_templates.api.v3.views.mixins.render_preview"


class TemplateCatalogTests(InvoiceTemplateAPITestCase):
    def test_list(self):
        response = self.get("templates/")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        codes = [t["code"] for t in data["templates"]]
        self.assertEqual(codes, ["standard", "classic", "simple"])
        from invoice_templates.v3.registry import REGISTRY
        from invoice_templates.v3.registry.fields import font_options

        for template in data["templates"]:
            self.assertIn("id", template)
            self.assertEqual(template["defaults"], REGISTRY[template["code"]].defaults)
        self.assertEqual(data["fonts"], font_options())

    def test_retrieve_defaults_and_fonts(self):
        data = self.get("templates/classic/").json()["data"]
        self.assertEqual(data["defaults"]["colors"]["background"], "#FDF5EC")
        self.assertEqual(data["defaults"]["font"], "inter")
        self.assertIn({"value": "inter", "label": "Inter"}, data["fonts"])
        for removed in ("fields", "field_groups", "thumbnail_url"):
            self.assertNotIn(removed, data)
        self.assertNotIn("thumbnail_url", self.get("templates/").json()["data"]["templates"][0])

    def test_unknown_or_inactive_template_is_404(self):
        self.assertEqual(self.get("templates/nope/").status_code, 404)
        self.classic.is_active = False
        self.classic.save()
        response = self.get("templates/classic/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["message"], messages.TEMPLATE_NOT_FOUND)

    def test_template_preview_returns_pdf_and_never_saves(self):
        with mock.patch(RENDER_PREVIEW, return_value=PDF) as render:
            response = self.post("templates/classic/preview/", {
                "document_type": "sale", "configuration": {"colors": {"primary": "#123456"}},
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response.content, PDF)
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="Invoice_Preview_Classic_Sale.pdf"')
        self.assertEqual(response["Content-Length"], str(len(PDF)))
        definition, configuration, document_type = render.call_args.args[:3]
        self.assertEqual((definition.code, configuration["colors"]["primary"], document_type), ("classic", "#123456", "sale"))
        self.assertFalse(ShopCustomInvoice.objects.exists())
        self.assertFalse(ShopInvoicePreference.objects.exists())

    def test_template_preview_by_id_needs_no_saved_invoice(self):
        templates = self.get("templates/").json()["data"]["templates"]
        classic_id = next(t["id"] for t in templates if t["code"] == "classic")
        self.assertEqual(classic_id, self.classic.id)
        with mock.patch(RENDER_PREVIEW, return_value=PDF) as render:
            by_id = self.post(f"templates/{classic_id}/preview/", {
                "document_type": "tailor_order", "configuration": {"colors": {"primary": "#123456"}},
            })
            by_code = self.post("templates/classic/preview/", {"document_type": "tailor_order"})
        self.assertEqual((by_id.status_code, by_code.status_code), (200, 200))
        self.assertEqual([c.args[0].code for c in render.call_args_list], ["classic", "classic"])
        self.assertEqual(render.call_args_list[0].args[1]["colors"]["primary"], "#123456")
        self.assertFalse(ShopCustomInvoice.objects.exists())
        self.assertFalse(ShopInvoicePreference.objects.exists())
        self.assertEqual(self.post("templates/999999/preview/", {"document_type": "sale"}).status_code, 404)
        self.assertEqual(self.get(f"templates/{classic_id}/").json()["data"]["code"], "classic")

    def test_template_preview_validation(self):
        response = self.post("templates/classic/preview/", {"document_type": "invoice"})
        self.assertEqual(response.json()["message"], messages.INVALID_DOCUMENT_TYPE)
        response = self.post("templates/classic/preview/", {"document_type": "sale", "configuration": {"colors": {"primary": "big"}}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], messages.INVALID_SETTINGS)

    def test_template_preview_render_failure(self):
        with mock.patch(RENDER_PREVIEW, side_effect=RuntimeError):
            response = self.post("templates/classic/preview/", {"document_type": "sale"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["message"], messages.PREVIEW_FAILED)


class CustomInvoiceCrudTests(InvoiceTemplateAPITestCase):
    def test_create_retrieve_list(self):
        response = self.create_invoice(name="Wedding Season", configuration={"colors": {"primary": "#8B1E3F"}})
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(response.json()["message"], messages.CUSTOM_INVOICE_CREATED)
        self.assertEqual((data["name"], data["template"]["code"]), ("Wedding Season", "classic"))
        expected = self.classic_defaults()
        expected["colors"]["primary"] = "#8B1E3F"
        self.assertEqual(data["configuration"], expected)  # always the complete six values
        self.assertEqual(ShopCustomInvoice.objects.get(id=data["id"]).configuration, expected)
        self.assertEqual(data["usage"], {"used": 1, "limit": 4, "remaining": 3})

        detail = self.get(f"custom-invoices/{data['id']}/").json()["data"]
        self.assertEqual(detail["name"], "Wedding Season")
        self.assertFalse(detail["is_default"])

        listing = self.get("custom-invoices/").json()["data"]
        self.assertEqual(listing["usage"]["used"], 1)
        self.assertEqual([i["name"] for i in listing["custom_invoices"]], ["Wedding Season"])

    def test_create_by_template_id(self):
        response = self.post("custom-invoices/", {"template_id": self.standard.id, "name": "By id"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["template"],
                         {"id": self.standard.id, "code": "standard", "name": "Standard"})
        self.assertEqual(self.post("custom-invoices/", {"name": "x"}).json()["message"],
                         messages.TEMPLATE_CODE_REQUIRED)
        self.assertEqual(self.post("custom-invoices/", {"template_id": 999999}).status_code, 404)

    def test_several_invoices_from_the_same_template(self):
        self.create_invoice_id(name="Wedding")
        self.create_invoice_id(name="Regular")
        self.assertEqual(ShopCustomInvoice.objects.filter(shop=self.shop_a, template=self.classic).count(), 2)

    def test_default_names_are_unique(self):
        first = self.create_invoice().json()["data"]["name"]
        second = self.create_invoice().json()["data"]["name"]
        self.assertEqual((first, second), ("Classic", "Classic 2"))

    def test_name_validation_and_uniqueness(self):
        self.create_invoice_id(name="Wedding")
        response = self.create_invoice(name="  wedding ")
        self.assertEqual(response.json()["message"], messages.NAME_TAKEN)
        for bad in ("", "x" * 61, "<b>x</b>", "a\nb"):
            response = self.create_invoice(name=bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertEqual(response.json()["message"], messages.INVALID_NAME, bad)

    def test_same_name_allowed_in_another_shop(self):
        self.create_invoice_id(name="Wedding")
        self.as_user(self.owner_b, self.shop_b)
        self.create_invoice_id(name="Wedding")

    def test_create_validation(self):
        self.assertEqual(self.post("custom-invoices/", {}).json()["message"], messages.TEMPLATE_CODE_REQUIRED)
        self.assertEqual(self.create_invoice(template_code="nope").status_code, 404)
        response = self.create_invoice(configuration={"colors": {"primary": "red", "accent": "#000000"},
                                                      "font": "comic_sans", "bogus": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()["data"]["validation_errors"]),
                         {"colors.primary", "colors.accent", "font", "bogus"})
        self.assertFalse(ShopCustomInvoice.objects.exists())

    def test_update_name_and_configuration(self):
        invoice_id = self.create_invoice_id(name="Wedding", configuration={"colors": {"primary": "#111111"}})
        response = self.patch(f"custom-invoices/{invoice_id}/", {"name": "Wedding 2026"})
        self.assertEqual(response.status_code, 200, response.content)
        invoice = ShopCustomInvoice.objects.get(id=invoice_id)
        self.assertEqual((invoice.name, invoice.configuration["colors"]["primary"]), ("Wedding 2026", "#111111"))

        # Partial update: only the sent values change.
        self.patch(f"custom-invoices/{invoice_id}/", {"configuration": {"colors": {"text": "#222222"}, "font": "inter"}})
        invoice.refresh_from_db()
        self.assertEqual(
            (invoice.configuration["colors"]["primary"], invoice.configuration["colors"]["text"],
             invoice.configuration["font"]),
            ("#111111", "#222222", "inter"),
        )
        self.assertEqual(invoice.template, self.classic)  # no template sent -> unchanged

    def test_switch_template_keeps_settings(self):
        invoice_id = self.create_invoice_id(name="Wedding", configuration={"colors": {"primary": "#111111"}, "font": "inter"})
        share_code = self.post(f"share/{invoice_id}/generate/").json()["data"]["share_code"]
        for code in ("simple", "standard", "classic"):
            with self.subTest(code=code):
                response = self.patch(f"custom-invoices/{invoice_id}/", {"template_code": code})
                self.assertEqual(response.status_code, 200, response.content)
                invoice = ShopCustomInvoice.objects.get(id=invoice_id)
                self.assertEqual(invoice.template.code, code)
                self.assertEqual((invoice.configuration["colors"]["primary"], invoice.configuration["font"]),
                                 ("#111111", "inter"))
                self.assertEqual(invoice.name, "Wedding")
        # The code shared for the old template was revoked by the switch.
        self.assertIsNotNone(InvoiceTemplateShare.objects.get(code=share_code).revoked_at)

        # Switch + configuration in one request; template_id works too.
        response = self.patch(f"custom-invoices/{invoice_id}/", {
            "template_id": self.standard.id, "configuration": {"colors": {"text": "#222222"}},
        })
        self.assertEqual(response.status_code, 200, response.content)
        invoice = ShopCustomInvoice.objects.get(id=invoice_id)
        self.assertEqual((invoice.template, invoice.configuration["colors"]["primary"],
                          invoice.configuration["colors"]["text"]), (self.standard, "#111111", "#222222"))

    def test_switch_template_errors(self):
        invoice_id = self.create_invoice_id(name="A")
        response = self.patch(f"custom-invoices/{invoice_id}/", {"template_code": "nope"})
        self.assertEqual((response.status_code, response.json()["message"]), (404, messages.TEMPLATE_NOT_FOUND))
        response = self.patch(f"custom-invoices/{invoice_id}/", {"template_code": "simple", "configuration": {"font": "x"}})
        self.assertEqual(response.json()["message"], messages.INVALID_SETTINGS)
        self.assertEqual(ShopCustomInvoice.objects.get(id=invoice_id).template, self.classic)  # nothing saved

    def test_update_validation(self):
        invoice_id = self.create_invoice_id(name="A")
        self.create_invoice_id(name="B")
        self.assertEqual(self.patch(f"custom-invoices/{invoice_id}/", {}).json()["message"], messages.UPDATE_FIELDS_REQUIRED)
        self.assertEqual(self.patch(f"custom-invoices/{invoice_id}/", {"name": "b"}).json()["message"], messages.NAME_TAKEN)
        response = self.patch(f"custom-invoices/{invoice_id}/", {"configuration": {"font": "x"}})
        self.assertEqual(response.json()["message"], messages.INVALID_SETTINGS)
        # Renaming to the same name (any case) is not a conflict with itself.
        self.assertEqual(self.patch(f"custom-invoices/{invoice_id}/", {"name": "a"}).status_code, 200)

    def test_delete(self):
        invoice_id = self.create_invoice_id(name="A")
        response = self.delete(f"custom-invoices/{invoice_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["usage"]["used"], 0)
        self.assertEqual(self.delete(f"custom-invoices/{invoice_id}/").status_code, 404)

    def test_custom_invoice_preview(self):
        invoice_id = self.create_invoice_id(configuration={"colors": {"primary": "#111111", "text": "#112233"}})
        with mock.patch(RENDER_PREVIEW, return_value=PDF) as render:
            saved = self.post(f"custom-invoices/{invoice_id}/preview/", {"document_type": "booking"})
            edited = self.post(f"custom-invoices/{invoice_id}/preview/", {
                "document_type": "booking", "configuration": {"colors": {"primary": "#444444"}},
            })
        self.assertEqual((saved.status_code, edited.status_code), (200, 200))
        self.assertTrue(saved["Content-Disposition"].startswith('attachment; filename="Invoice_Preview_'))
        saved_config = render.call_args_list[0].args[1]
        edited_config = render.call_args_list[1].args[1]
        self.assertEqual(saved_config["colors"]["primary"], "#111111")
        self.assertEqual((edited_config["colors"]["primary"], edited_config["colors"]["text"]), ("#444444", "#112233"))
        self.assertEqual(ShopCustomInvoice.objects.get(id=invoice_id).configuration["colors"]["primary"], "#111111")


class CreationLimitTests(InvoiceTemplateAPITestCase):
    def test_limit_of_four_per_shop(self):
        ids = [self.create_invoice_id(name=f"Invoice {i}") for i in range(4)]
        response = self.create_invoice(name="Fifth")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["message"], messages.limit_reached_message(4))
        self.assertEqual(body["data"]["usage"], {"used": 4, "limit": 4, "remaining": 0})
        self.assertEqual(ShopCustomInvoice.objects.filter(shop=self.shop_a).count(), 4)

        # Updates are unlimited and never use a slot.
        for i in range(20):
            self.assertEqual(self.patch(f"custom-invoices/{ids[0]}/", {"configuration": {"colors": {"primary": f"#0000{i:02d}"}}}).status_code, 200)
        self.assertEqual(ShopCustomInvoice.objects.filter(shop=self.shop_a).count(), 4)

        # Deleting frees a slot.
        self.delete(f"custom-invoices/{ids[1]}/")
        self.create_invoice_id(name="Replacement")

    def test_limit_is_per_shop(self):
        for i in range(4):
            self.create_invoice_id(name=f"A{i}")
        self.as_user(self.owner_b, self.shop_b)
        self.create_invoice_id(name="B0")
        self.assertEqual(self.get("custom-invoices/").json()["data"]["usage"]["used"], 1)

    def test_limit_is_configurable(self):
        with self.settings(INVOICE_CUSTOM_INVOICE_LIMIT=1):
            self.create_invoice_id()
            response = self.create_invoice()
        self.assertEqual(response.json()["message"], messages.limit_reached_message(1))


class PreferenceTests(InvoiceTemplateAPITestCase):
    def test_default_customized_invoice(self):
        wedding = self.create_invoice_id(name="Wedding")
        regular = self.create_invoice_id(name="Regular", template_code="standard")

        response = self.put("preference/default/", {"custom_invoice_id": wedding})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["default_custom_invoice"], {"id": wedding, "name": "Wedding", "template_code": "classic"})
        self.assertEqual(data["effective_template_code"], "classic")
        self.assertTrue(data["is_customization_enabled"])

        self.put("preference/default/", {"custom_invoice_id": regular})
        self.assertEqual(ShopInvoicePreference.objects.get(shop=self.shop_a).default_custom_invoice_id, regular)
        self.assertEqual(ShopCustomInvoice.objects.filter(shop=self.shop_a).count(), 2)

        data = self.put("preference/default/", {"custom_invoice_id": None}).json()["data"]
        self.assertIsNone(data["default_custom_invoice"])
        self.assertEqual(data["effective_template_code"], "standard")

    def test_default_validation_and_isolation(self):
        self.assertEqual(self.put("preference/default/", {}).json()["message"], messages.DEFAULT_INVOICE_REQUIRED)
        self.assertEqual(self.put("preference/default/", {"custom_invoice_id": 999999}).status_code, 404)
        self.as_user(self.owner_b, self.shop_b)
        other_shop_invoice = self.create_invoice_id()
        self.as_user(self.owner_a, self.shop_a)
        self.assertEqual(self.put("preference/default/", {"custom_invoice_id": other_shop_invoice}).status_code, 404)

    def test_deleting_default_falls_back_to_standard(self):
        invoice_id = self.create_invoice_id()
        self.put("preference/default/", {"custom_invoice_id": invoice_id})
        self.assertTrue(self.delete(f"custom-invoices/{invoice_id}/").json()["data"]["was_default"])
        preference = ShopInvoicePreference.objects.get(shop=self.shop_a)
        self.assertIsNone(preference.default_custom_invoice)
        self.assertEqual(resolve_for_shop(self.shop_a.id).definition.code, "standard")

    def test_toggle_customization(self):
        invoice_id = self.create_invoice_id()
        self.put("preference/default/", {"custom_invoice_id": invoice_id})
        self.assertEqual(self.patch("preference/customization/", {"enabled": "false"}).status_code, 400)
        self.assertEqual(self.patch("preference/customization/", {"enabled": False}).status_code, 200)
        preference = ShopInvoicePreference.objects.get(shop=self.shop_a)
        self.assertFalse(preference.is_customization_enabled)
        self.assertEqual(preference.default_custom_invoice_id, invoice_id)  # nothing lost

    def test_terms(self):
        response = self.patch("preference/terms/", {"booking": ["Booking term"], "sale": None})
        self.assertEqual(response.status_code, 200, response.content)
        data = self.get("preference/").json()["data"]
        self.assertEqual(data["terms"], {"booking": ["Booking term"], "sale": None, "tailor_order": None})
        self.assertEqual(data["effective_terms"]["booking"], ["Booking term"])
        # Not set -> the existing default terms (shop-wide "General A" is not used).
        self.assertEqual(data["effective_terms"]["sale"], list(DEFAULT_SALE_TERMS))
        self.assertEqual(data["effective_terms"]["tailor_order"], list(DEFAULT_TAILOR_ORDER_TERMS))
        self.assertNotIn("legacy_terms", data)
        self.assertEqual(self.patch("preference/terms/", {}).json()["message"], messages.TERMS_REQUIRED)
        response = self.patch("preference/terms/", {"booking": [""]})
        self.assertEqual(response.json()["message"], messages.INVALID_TERMS)
        self.assertIn("booking", response.json()["data"]["validation_errors"])


class PermissionAndIsolationTests(InvoiceTemplateAPITestCase):
    def test_staff_cannot_access(self):
        self.as_user(self.staff_a, self.shop_a)
        self.assertEqual(self.get("custom-invoices/").status_code, 403)
        self.assertEqual(self.create_invoice().status_code, 403)

    def test_cannot_act_on_another_shop(self):
        self.as_user(self.owner_a, self.shop_b)
        self.assertEqual(self.create_invoice().status_code, 403)
        self.assertFalse(ShopCustomInvoice.objects.exists())

    def test_other_shops_invoices_are_not_found(self):
        self.as_user(self.owner_b, self.shop_b)
        invoice_id = self.create_invoice_id(name="B only", configuration={"colors": {"primary": "#343434"}})
        self.as_user(self.owner_a, self.shop_a)
        for response in (
            self.get(f"custom-invoices/{invoice_id}/"),
            self.patch(f"custom-invoices/{invoice_id}/", {"name": "Hijacked"}),
            self.delete(f"custom-invoices/{invoice_id}/"),
            self.post(f"custom-invoices/{invoice_id}/preview/", {"document_type": "sale"}),
            self.post(f"share/{invoice_id}/generate/"),
        ):
            self.assertEqual(response.status_code, 404)
        invoice = ShopCustomInvoice.objects.get(id=invoice_id)
        self.assertEqual((invoice.name, invoice.configuration["colors"]["primary"]), ("B only", "#343434"))
        self.assertNotIn(invoice_id, [i["id"] for i in self.get("custom-invoices/").json()["data"]["custom_invoices"]])


class InvoiceResolutionTests(InvoiceTemplateAPITestCase):
    def test_resolution_defaults_to_standard(self):
        resolved = resolve_for_shop(self.shop_a.id)
        self.assertEqual(resolved.definition.code, "standard")
        self.assertIsNone(resolved.custom_invoice)

    def test_resolution_uses_default_invoice_in_one_query(self):
        invoice = ShopCustomInvoice.objects.create(
            shop=self.shop_a, template=self.classic, name="W",
            configuration={"colors": {"primary": "#444444"}, "font": "inter"},
        )
        ShopInvoicePreference.objects.create(shop=self.shop_a, default_custom_invoice=invoice, is_customization_enabled=True)
        with self.assertNumQueries(1):
            resolved = resolve_for_shop(self.shop_a.id)
        self.assertEqual(resolved.definition.code, "classic")
        self.assertEqual(resolved.configuration["colors"]["primary"], "#444444")
        self.assertEqual(resolved.configuration["colors"]["background"], "#FDF5EC")  # default filled in

    def test_inactive_template_falls_back_to_standard(self):
        invoice = ShopCustomInvoice.objects.create(shop=self.shop_a, template=self.classic, name="W")
        ShopInvoicePreference.objects.create(shop=self.shop_a, default_custom_invoice=invoice)
        self.classic.is_active = False
        self.classic.save()
        self.assertEqual(resolve_for_shop(self.shop_a.id).definition.code, "standard")

    def test_existing_generator_uses_customized_invoice_when_enabled(self):
        invoice = ShopCustomInvoice.objects.create(shop=self.shop_a, template=self.classic, name="W")
        ShopInvoicePreference.objects.create(shop=self.shop_a, default_custom_invoice=invoice, is_customization_enabled=True)
        booking, items = make_booking(1)
        with self.settings(INVOICE_CUSTOMIZATION_ENABLED=True), \
                mock.patch("invoice_templates.v3.services.rendering.render_pdf_bytes", return_value=b"%PDF-custom") as render:
            result = legacy.BookingPDFService.generate_transaction_pdf(self.shop_a, make_client(), booking, items)
        self.assertEqual(base64.b64decode(result), b"%PDF-custom")
        self.assertEqual(render.call_args.args[0].code, "classic")

    def test_existing_generator_unchanged_when_shop_switched_off(self):
        invoice = ShopCustomInvoice.objects.create(shop=self.shop_a, template=self.classic, name="W")
        ShopInvoicePreference.objects.create(shop=self.shop_a, default_custom_invoice=invoice, is_customization_enabled=False)
        booking, items = make_booking(1)
        with self.settings(INVOICE_CUSTOMIZATION_ENABLED=True), \
                mock.patch("invoice_templates.v3.services.rendering.render_pdf_bytes") as render, \
                mock.patch.object(legacy.BasePDFService, "generate_pdf", return_value=b"%PDF-legacy"):
            result = legacy.BookingPDFService.generate_transaction_pdf(self.shop_a, make_client(), booking, items)
        render.assert_not_called()
        self.assertEqual(base64.b64decode(result), b"%PDF-legacy")
