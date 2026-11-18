from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from shop.api.v3.utils import tax_calculation
from shop.api.v3.utils.tax_calculation import (
    APPLY_TAX_ERROR, get_transaction_tax_config, resolve_apply_tax,
)
from shop.models import Shop
from shop.utils.s3_folder import build_s3_folder_name, legacy_s3_segment


class ApplyTaxFlagTests(SimpleTestCase):
    """Per-transaction tax switch shared by the Booking/Sale/Tailor Order create APIs (no DB)."""

    def test_missing_or_null_defaults_to_true(self):
        self.assertEqual(resolve_apply_tax({}), (True, None))
        self.assertEqual(resolve_apply_tax({"apply_tax": None}), (True, None))

    def test_accepted_values(self):
        cases = {
            True: True, False: False, 1: True, 0: False,
            "true": True, "TRUE": True, " yes ": True, "1": True, "on": True,
            "false": False, "False": False, "no": False, "0": False, "off": False,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(resolve_apply_tax({"apply_tax": raw}), (expected, None))

    def test_invalid_values_are_rejected(self):
        for raw in ("maybe", "", 2, -1, 0.5, [], {}):
            with self.subTest(raw=raw):
                self.assertEqual(resolve_apply_tax({"apply_tax": raw}), (True, APPLY_TAX_ERROR))

    def test_tax_config_for_one_transaction(self):
        config = SimpleNamespace(is_enabled=True, tax_rate=18)
        shop = SimpleNamespace(tax_configuration=config)
        self.assertIs(get_transaction_tax_config(shop, True), config)
        self.assertIsNone(get_transaction_tax_config(shop, False))
        # The shop's own configuration is never changed by switching tax off.
        self.assertTrue(config.is_enabled)
        self.assertIs(get_transaction_tax_config(shop), config)

    def test_disabled_or_missing_shop_config_stays_untaxed(self):
        disabled = SimpleNamespace(tax_configuration=SimpleNamespace(is_enabled=False))
        self.assertIsNone(get_transaction_tax_config(disabled, True))
        self.assertIsNone(get_transaction_tax_config(SimpleNamespace(tax_configuration=None), True))

    def test_create_apis_use_the_shared_helpers(self):
        # import_module: the viewset packages export a `create` function that
        # shadows the module name in a plain `from ... import create`.
        from importlib import import_module

        for module in map(import_module, (
            "booking_v2.api.v5.views.booking_viewset.create",
            "sales.api.v5.views.sale_viewset.create",
            "tailored.api.v3.views.tailor_order.create",
        )):
            with self.subTest(module=module.__name__):
                self.assertIs(module.resolve_apply_tax, tax_calculation.resolve_apply_tax)
                self.assertIs(module.get_transaction_tax_config, tax_calculation.get_transaction_tax_config)


class S3FolderNamingTests(SimpleTestCase):
    """Folder naming rules (no DB)."""

    def test_build_s3_folder_name_standard_convention(self):
        cases = {
            "BB Shop": "BB_Shop",
            "  BB   Shop  ": "BB_Shop",
            "_BB Shop_": "BB_Shop",
            "BB's Shop": "BB_s_Shop",
            "": "",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(build_s3_folder_name(name), expected)

    def test_build_s3_folder_name_truncates_without_trailing_underscore(self):
        folder = build_s3_folder_name("x" * 49 + " y")
        self.assertEqual(folder, "x" * 49)

    def test_legacy_segment_is_unchanged(self):
        # Existing S3 folders depend on this exact output.
        self.assertEqual(legacy_s3_segment("BB Shop"), "BB_Shop")
        self.assertEqual(legacy_s3_segment("BB's Shop"), "BB_s_Shop")
        self.assertEqual(legacy_s3_segment(" BB Shop "), "_BB_Shop_")


class ShopS3FolderTests(TestCase):
    """Shop.s3_folder_name is assigned once and survives renames."""

    def test_new_shop_gets_folder_from_name(self):
        shop = Shop.objects.create(name="BB Shop", place="Kochi")
        self.assertEqual(shop.s3_folder_name, "BB_Shop")
        self.assertEqual(shop.s3_folder, "BB_Shop")

    def test_rename_via_save_keeps_folder(self):
        shop = Shop.objects.create(name="BB Shop", place="Kochi")
        shop.name = "BB Boutique"
        shop.save()
        shop.refresh_from_db()
        self.assertEqual(shop.s3_folder_name, "BB_Shop")
        self.assertEqual(shop.s3_folder, "BB_Shop")

    def test_rename_via_update_fields_keeps_folder(self):
        shop = Shop.objects.create(name="BB Shop", place="Kochi")
        shop.name = "BB Boutique"
        shop.save(update_fields=["name"])
        shop.refresh_from_db()
        self.assertEqual(shop.s3_folder, "BB_Shop")

    def test_same_name_other_place_gets_suffixed_folder(self):
        Shop.objects.create(name="BB Shop", place="Kochi")
        other = Shop.objects.create(name="BB Shop", place="Kollam")
        self.assertEqual(other.s3_folder_name, "BB_Shop_Kollam")

    def test_explicit_folder_is_kept_on_create(self):
        shop = Shop.objects.create(name="BB Shop", place="Kochi", s3_folder_name="Old Store")
        self.assertEqual(shop.s3_folder, "Old Store")

    def test_unmapped_shop_falls_back_to_legacy_folder(self):
        shop = Shop.objects.create(name="BB's Shop", place="Kochi")
        Shop.objects.filter(pk=shop.pk).update(s3_folder_name=None)
        shop.refresh_from_db()
        with self.assertLogs("shop.models.shop", level="INFO"):
            self.assertEqual(shop.s3_folder, "BB_s_Shop")

    def test_rename_of_unmapped_shop_does_not_assign_folder(self):
        shop = Shop.objects.create(name="BB Shop", place="Kochi")
        Shop.objects.filter(pk=shop.pk).update(s3_folder_name=None)
        shop.refresh_from_db()
        shop.name = "BB Boutique"
        shop.save()
        shop.refresh_from_db()
        self.assertIsNone(shop.s3_folder_name)
