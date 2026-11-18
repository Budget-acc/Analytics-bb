"""Share-code tests (DB tests require PostgreSQL - partial unique constraint + migrations)."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from invoice_templates.api.v3.utils import messages
from invoice_templates.models import InvoiceTemplateShare, ShopCustomInvoice, ShopInvoicePreference
from invoice_templates.v3.services import sharing

from .base import InvoiceTemplateAPITestCase


class CodeFormatTests(SimpleTestCase):
    def test_normalize(self):
        self.assertEqual(sharing.normalize_code(" k7qm-2xrp 9htd "), "K7QM2XRP9HTD")
        for bad in (None, 123, "", "K7QM2XRP9HT", "K7QM2XRP9HTD1", "O0I1L-----XX", "K7QM2XRP9HT!"):
            self.assertIsNone(sharing.normalize_code(bad), bad)

    def test_display_and_qr(self):
        self.assertEqual(sharing.display_code("K7QM2XRP9HTD"), "K7QM-2XRP-9HTD")
        self.assertEqual(sharing.qr_payload("K7QM2XRP9HTD"), "bookiebuddy://invoice-template/import?code=K7QM2XRP9HTD")

    def test_expiry(self):
        created = datetime(2026, 10, 16, 4, 50, tzinfo=dt_timezone.utc)
        explicit = created + timedelta(hours=2)
        self.assertEqual(sharing.share_expires_at(SimpleNamespace(expires_at=explicit, created_at=created)), explicit)
        # Legacy codes stored without expires_at expire 24h after creation.
        self.assertEqual(
            sharing.share_expires_at(SimpleNamespace(expires_at=None, created_at=created)),
            created + timedelta(hours=24),
        )


class ShareAPITests(InvoiceTemplateAPITestCase):
    def setUp(self):
        super().setUp()
        self.invoice_id = self.create_invoice_id(name="Wedding", configuration={"colors": {"primary": "#343434", "text": "#112233"}})

    def generate(self, invoice_id=None):
        return self.post(f"share/{invoice_id or self.invoice_id}/generate/")

    def test_generate_reuses_code_until_invoice_changes(self):
        first = self.generate()
        self.assertEqual(first.status_code, 201)
        data = first.json()["data"]
        code = data["share_code"]
        self.assertEqual(len(code), 12)
        self.assertTrue(set(code) <= set(sharing.ALPHABET))
        self.assertEqual((data["invoice_name"], data["custom_invoice_id"]), ("Wedding", self.invoice_id))

        again = self.generate()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["data"]["share_code"], code)
        self.assertEqual(again.json()["data"]["expires_at"], data["expires_at"])

        self.patch(f"custom-invoices/{self.invoice_id}/", {"configuration": {"colors": {"primary": "#404040"}}})
        self.assertTrue(self.get(f"share/{self.invoice_id}/").json()["data"]["is_outdated"])
        regenerated = self.generate()
        self.assertEqual(regenerated.status_code, 201)
        self.assertNotEqual(regenerated.json()["data"]["share_code"], code)
        self.assertEqual(InvoiceTemplateShare.objects.filter(revoked_at__isnull=True).count(), 1)

    def test_generate_sets_24h_expiry(self):
        data = self.generate().json()["data"]
        share = InvoiceTemplateShare.objects.get(code=data["share_code"])
        self.assertIsNotNone(share.expires_at)
        self.assertAlmostEqual(share.expires_at - share.created_at, timedelta(hours=24), delta=timedelta(seconds=5))
        self.assertIsNotNone(data["expires_at"])

    def test_expired_code_is_replaced(self):
        code = self.generate().json()["data"]["share_code"]
        InvoiceTemplateShare.objects.filter(code=code).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertIsNone(self.get(f"share/{self.invoice_id}/").json()["data"])

        response = self.generate()
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.json()["data"]["share_code"], code)
        self.assertIsNotNone(InvoiceTemplateShare.objects.get(code=code).revoked_at)
        self.assertEqual(InvoiceTemplateShare.objects.filter(revoked_at__isnull=True).count(), 1)

        # Replaced by generate (revoked_at set) but still reported as expired.
        self.as_user(self.owner_b, self.shop_b)
        self.assert_expired(self.post("share/import/", {"share_code": code}))

    def test_legacy_code_without_expiry(self):
        code = self.generate().json()["data"]["share_code"]
        legacy = InvoiceTemplateShare.objects.filter(code=code)

        legacy.update(expires_at=None, created_at=timezone.now() - timedelta(hours=1))
        reused = self.generate()
        self.assertEqual((reused.status_code, reused.json()["data"]["share_code"]), (200, code))
        self.assertIsNotNone(reused.json()["data"]["expires_at"])

        legacy.update(expires_at=None, created_at=timezone.now() - timedelta(hours=25))
        self.assertIsNone(self.get(f"share/{self.invoice_id}/").json()["data"])
        self.as_user(self.owner_b, self.shop_b)
        self.assert_expired(self.post("share/import/", {"share_code": code}))
        self.as_user(self.owner_a, self.shop_a)
        response = self.generate()
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.json()["data"]["share_code"], code)

    def assert_expired(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        body = response.json()
        self.assertEqual(body["message"], messages.SHARE_CODE_EXPIRED)
        self.assertEqual(body["dev_message"], messages.SHARE_CODE_EXPIRED_DEV)
        self.assertEqual(body["error_code"], messages.SHARE_CODE_EXPIRED_ERROR_CODE)
        self.assertIsNone(body["data"])

    def test_import_expired_code_rejected(self):
        code = self.generate().json()["data"]["share_code"]
        InvoiceTemplateShare.objects.filter(code=code).update(expires_at=timezone.now() - timedelta(seconds=1))

        self.as_user(self.owner_b, self.shop_b)
        self.assert_expired(self.post("share/import/", {"share_code": code}))
        self.assert_expired(self.get(f"share/lookup/{code}/"))
        self.assertFalse(ShopCustomInvoice.objects.filter(shop=self.shop_b).exists())
        self.assertEqual(InvoiceTemplateShare.objects.get(code=code).import_count, 0)

    def test_code_expiring_during_import_is_rejected(self):
        code = self.generate().json()["data"]["share_code"]
        # The lookup sees the code as valid; the locked re-check inside the
        # import transaction sees it as expired.
        now = timezone.now()
        self.as_user(self.owner_b, self.shop_b)
        with mock.patch.object(
            sharing, "share_expires_at", side_effect=[now + timedelta(hours=1), now - timedelta(seconds=1)],
        ) as expiry:
            response = self.post("share/import/", {"share_code": code})
        self.assertEqual(expiry.call_count, 2)
        self.assert_expired(response)
        self.assertFalse(ShopCustomInvoice.objects.filter(shop=self.shop_b).exists())
        self.assertEqual(InvoiceTemplateShare.objects.get(code=code).import_count, 0)

    def test_revoked_unexpired_code_stays_generic(self):
        code = self.generate().json()["data"]["share_code"]
        self.delete(f"share/{self.invoice_id}/")
        self.as_user(self.owner_b, self.shop_b)
        for response in (self.post("share/import/", {"share_code": code}), self.get(f"share/lookup/{code}/")):
            self.assertEqual(response.json()["message"], messages.INVALID_SHARE_CODE)
            self.assertNotIn("error_code", response.json())

    def test_revoke(self):
        code = self.generate().json()["data"]["share_code"]
        self.assertEqual(self.delete(f"share/{self.invoice_id}/").status_code, 200)
        self.assertEqual(self.delete(f"share/{self.invoice_id}/").status_code, 404)
        self.assertIsNone(self.get(f"share/{self.invoice_id}/").json()["data"])
        self.as_user(self.owner_b, self.shop_b)
        self.assertEqual(self.post("share/import/", {"share_code": code}).json()["message"], messages.INVALID_SHARE_CODE)

    def test_deleting_invoice_invalidates_its_code(self):
        code = self.generate().json()["data"]["share_code"]
        self.delete(f"custom-invoices/{self.invoice_id}/")
        self.assertFalse(InvoiceTemplateShare.objects.exists())
        self.as_user(self.owner_b, self.shop_b)
        self.assertEqual(self.post("share/import/", {"share_code": code}).json()["message"], messages.INVALID_SHARE_CODE)

    def test_lookup_and_import_creates_new_invoice(self):
        self.put("preference/default/", {"custom_invoice_id": None})
        code = self.generate().json()["data"]["display_code"]

        self.as_user(self.owner_b, self.shop_b)
        lookup = self.get(f"share/lookup/{code.lower()}/")
        self.assertEqual(lookup.status_code, 200)
        data = lookup.json()["data"]
        self.assertEqual((data["template_code"], data["invoice_name"]), ("classic", "Wedding"))
        self.assertFalse(data["is_own_code"])
        self.assertNotIn("source_shop", str(data))

        response = self.post("share/import/", {"share_code": code})
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual((data["name"], data["template"]["code"]), ("Wedding", "classic"))
        self.assertEqual(data["usage"]["used"], 1)
        imported = ShopCustomInvoice.objects.get(shop=self.shop_b)
        source = ShopCustomInvoice.objects.get(id=self.invoice_id)
        self.assertEqual(imported.configuration, source.configuration)
        self.assertEqual(imported.configuration["colors"]["primary"], "#343434")
        self.assertFalse(ShopInvoicePreference.objects.filter(shop=self.shop_b).exists())  # default untouched

        # A second import gets a unique default name; independent copies never leak.
        self.assertEqual(self.post("share/import/", {"share_code": code}).json()["data"]["name"], "Wedding 2")
        self.as_user(self.owner_a, self.shop_a)
        self.patch(f"custom-invoices/{self.invoice_id}/", {"configuration": {"colors": {"primary": "#202020"}}})
        imported.refresh_from_db()
        self.assertEqual(imported.configuration["colors"]["primary"], "#343434")
        self.assertEqual(InvoiceTemplateShare.objects.get(code=sharing.normalize_code(code)).import_count, 2)

    def test_import_with_name_and_default(self):
        code = self.generate().json()["data"]["share_code"]
        self.as_user(self.owner_b, self.shop_b)
        response = self.post("share/import/", {"share_code": code, "name": "From main branch", "set_as_default": True})
        self.assertEqual(response.status_code, 201, response.content)
        preference = ShopInvoicePreference.objects.get(shop=self.shop_b)
        self.assertEqual(preference.default_custom_invoice.name, "From main branch")
        self.assertTrue(preference.is_customization_enabled)

        response = self.post("share/import/", {"share_code": code, "name": "from MAIN branch"})
        self.assertEqual(response.json()["message"], messages.NAME_TAKEN)

    def test_import_respects_creation_limit(self):
        code = self.generate().json()["data"]["share_code"]
        self.as_user(self.owner_b, self.shop_b)
        for i in range(4):
            self.create_invoice_id(name=f"B{i}")
        response = self.post("share/import/", {"share_code": code})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], messages.limit_reached_message(4))
        self.assertEqual(ShopCustomInvoice.objects.filter(shop=self.shop_b).count(), 4)
        self.assertEqual(InvoiceTemplateShare.objects.get(code=code).import_count, 0)

    def test_cannot_import_own_code(self):
        code = self.generate().json()["data"]["share_code"]
        self.assertEqual(self.post("share/import/", {"share_code": code}).json()["message"], messages.OWN_SHARE_CODE)

    def test_invalid_codes_share_one_message(self):
        for code in ("ZZZZ-ZZZZ-ZZZZ", "bad", "0000-0000-0000"):
            response = self.post("share/import/", {"share_code": code})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["message"], messages.INVALID_SHARE_CODE)
        self.assertEqual(self.post("share/import/", {}).json()["message"], messages.SHARE_CODE_REQUIRED)

    def test_code_collision_is_retried(self):
        taken = self.generate().json()["data"]["share_code"]
        other = self.create_invoice_id(name="Other")
        with mock.patch.object(sharing, "generate", side_effect=[taken, "ABCDEFGHJKMN"]):
            response = self.generate(other)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["share_code"], "ABCDEFGHJKMN")
