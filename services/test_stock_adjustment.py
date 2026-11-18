"""Database-backed tests for the product stock-adjustment API
(require PostgreSQL - the project's migrations do not run on SQLite)."""
import threading
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from authentication.models import User
from services.api.v3.serializers import ProductVariantSerializer, ProductVariantViewSerializer
from services.constants.stock_adjustment import StockAdjustmentMessages as M
from services.models import (
    GeneralService,
    MainServiceCategory,
    Product,
    ProductVariant,
    ProductVariantStockAdjustment,
    ServiceAttributeTemplate,
    ServiceAttributeValue,
    ShopOwnerService,
)
from services.utils.stock_adjustment import StockAdjustmentError, apply_stock_adjustments
from shop.models import Shop, ShopUser
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

API = "/api/v3/service/products/{}/stock-adjustments/"


def make_product(shop, name="Wedding Gown", service_name="Dress Rental"):
    category, _ = MainServiceCategory.objects.get_or_create(name="Dress")
    general, _ = GeneralService.objects.get_or_create(name=service_name, defaults={"main_category": category})
    service, _ = ShopOwnerService.objects.get_or_create(shop=shop, general_service=general)
    return Product.objects.create(shop_service=service, name=name)


class StockAdjustmentAPITestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.shop = Shop.objects.create(name="Shop A", place="Kochi")
        cls.other_shop = Shop.objects.create(name="Shop B", place="Calicut")
        cls.owner = User.objects.create_user(phone="+919000000101", password="x", role="OWNER", full_name="Owner A")
        ShopUser.objects.create(shop=cls.shop, user=cls.owner, role="OWNER")

        cls.product = make_product(cls.shop)
        cls.small = ProductVariant.objects.create(product=cls.product, attribute="S", stock=2)
        cls.medium = ProductVariant.objects.create(product=cls.product, attribute="M", stock=5)
        cls.large = ProductVariant.objects.create(product=cls.product, attribute="L", stock=2)

        cls.fabric_product = make_product(cls.shop, name="Silk Fabric")
        cls.fabric = ProductVariant.objects.create(
            product=cls.fabric_product, attribute="Red", stock=1, fabric_length=Decimal("20.00"),
        )

        cls.other_product = make_product(cls.shop, name="Sherwani")
        cls.other_variant = ProductVariant.objects.create(product=cls.other_product, attribute="M", stock=3)
        cls.foreign_product = make_product(cls.other_shop, name="Foreign Gown")

    def setUp(self):
        patcher = mock.patch.object(SubscriptionActivePermission, "has_permission", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.client.credentials(HTTP_X_ACTIVE_SHOP_ID=str(self.shop.id))

    def adjust(self, action, variants, reason="damage", product=None, **extra):
        payload = {"action": action, "reason": reason, "variants": variants, **extra}
        return self.client.post(API.format((product or self.product).id), payload, format="json")

    def assert_error(self, response, msg, status_code=400):
        self.assertEqual(response.status_code, status_code, response.content)
        self.assertEqual(response.json()["message"], msg if isinstance(msg, str) else msg.message)

    def assert_variant(self, variant, stock, unavailable_stock, fabric=None, unavailable_fabric=Decimal("0.00")):
        variant.refresh_from_db()
        self.assertEqual((variant.stock, variant.unavailable_stock), (stock, unavailable_stock))
        self.assertEqual((variant.fabric_length, variant.unavailable_fabric_length), (fabric, unavailable_fabric))


class MarkAndRestoreTests(StockAdjustmentAPITestCase):
    def test_mark_one_variant_fully_unavailable_multi_variant_product(self):
        """Example A: both units of S damaged; M and L untouched."""
        structure_before = ProductVariant.objects.get(id=self.small.id).structure_updated_at
        response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 2}], note="Torn")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["message"], M.MARKED_UNAVAILABLE)
        item = body["data"]["variants"][0]
        self.assertEqual(
            (item["stock"], item["unavailable_stock"], item["total_stock"], item["reason"], item["note"]),
            (0, 2, 2, "damage", "Torn"),
        )
        self.assert_variant(self.small, 0, 2)
        self.assert_variant(self.medium, 5, 0)
        self.assert_variant(self.large, 2, 0)
        self.assertEqual(ProductVariant.objects.get(id=self.small.id).structure_updated_at, structure_before)

        row = ProductVariantStockAdjustment.objects.get()
        self.assertEqual(str(row.batch_id), body["data"]["batch_id"])
        self.assertEqual(
            (row.action, row.reason, row.quantity, row.previous_stock, row.updated_stock,
             row.previous_unavailable_stock, row.updated_unavailable_stock, row.created_by_id, row.shop_id),
            ("mark_unavailable", "damage", 2, 2, 0, 0, 2, self.owner.id, self.shop.id),
        )

    def test_partial_mark_then_partial_restore(self):
        """Examples B and C: 5 units, 3 damaged, 1 repaired, then over-restore rejected."""
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 3}])
        self.assert_variant(self.medium, 2, 3)

        response = self.adjust("restore", [{"variant_id": self.medium.id, "quantity": 1}], note="Stitched")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["message"], M.RESTORED)
        self.assert_variant(self.medium, 3, 2)

        response = self.adjust("restore", [{"variant_id": self.medium.id, "quantity": 3}])
        self.assert_error(response, "Only 2 unit(s) of Wedding Gown (M) are unavailable.")
        self.assertIn(f"Variant {self.medium.id}: quantity 3 exceeds unavailable_stock 2.", response.json()["dev_message"])
        self.assert_variant(self.medium, 3, 2)
        self.assertEqual(ProductVariantStockAdjustment.objects.count(), 2)

    def test_mark_more_than_usable_stock_rejected(self):
        response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 3}])
        self.assert_error(response, "Only 2 unit(s) of Wedding Gown (S) are available.")
        self.assert_variant(self.small, 2, 0)

    def test_fabric_mark_and_restore(self):
        """Example D."""
        response = self.adjust(
            "mark_unavailable", [{"variant_id": self.fabric.id, "fabric_length": "4.50"}],
            reason="maintenance", product=self.fabric_product,
        )
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["data"]["variants"][0]
        self.assertEqual(
            (item["available_fabric_length"], item["unavailable_fabric_length"], item["total_fabric_length"], item["quantity"]),
            ("15.50", "4.50", "20.00", 0),
        )
        self.assert_variant(self.fabric, 1, 0, Decimal("15.50"), Decimal("4.50"))

        response = self.adjust(
            "restore", [{"variant_id": self.fabric.id, "fabric_length": "5"}],
            reason="maintenance", product=self.fabric_product,
        )
        self.assert_error(response, "Only 4.50 m of Silk Fabric (Red) is unavailable.")

        self.adjust("restore", [{"variant_id": self.fabric.id, "fabric_length": "4.5"}], reason="maintenance", product=self.fabric_product)
        self.assert_variant(self.fabric, 1, 0, Decimal("20.00"), Decimal("0.00"))

    def test_units_and_fabric_in_one_item(self):
        response = self.adjust(
            "mark_unavailable", [{"variant_id": self.fabric.id, "quantity": 1, "fabric_length": "2"}],
            product=self.fabric_product,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_variant(self.fabric, 0, 1, Decimal("18.00"), Decimal("2.00"))
        self.assertEqual(ProductVariantStockAdjustment.objects.count(), 1)

    def test_per_item_reason_and_note_override(self):
        self.adjust(
            "mark_unavailable",
            [{"variant_id": self.small.id, "quantity": 1},
             {"variant_id": self.medium.id, "quantity": 1, "reason": "maintenance", "note": "Zip repair"}],
            note="Event damage",
        )
        rows = {row.variant_id: row for row in ProductVariantStockAdjustment.objects.all()}
        self.assertEqual((rows[self.small.id].reason, rows[self.small.id].note), ("damage", "Event damage"))
        self.assertEqual((rows[self.medium.id].reason, rows[self.medium.id].note), ("maintenance", "Zip repair"))
        self.assertEqual(len({row.batch_id for row in rows.values()}), 1)


class ValidationTests(StockAdjustmentAPITestCase):
    def test_batch_is_all_or_nothing(self):
        """Example E: the second item fails, so the first is not applied either."""
        response = self.adjust(
            "mark_unavailable",
            [{"variant_id": self.medium.id, "quantity": 1}, {"variant_id": self.small.id, "quantity": 9}],
        )
        self.assert_error(response, "Only 2 unit(s) of Wedding Gown (S) are available.")
        self.assert_variant(self.medium, 5, 0)
        self.assert_variant(self.small, 2, 0)
        self.assertFalse(ProductVariantStockAdjustment.objects.exists())

    def test_duplicate_and_foreign_variants(self):
        """Example F."""
        response = self.adjust(
            "mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}, {"variant_id": self.small.id, "quantity": 1}],
        )
        self.assert_error(response, M.DUPLICATE_VARIANTS)
        self.assertIn(str(self.small.id), response.json()["dev_message"])

        response = self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}])
        self.assert_error(response, M.INVALID_VARIANTS)
        self.assertIn(str(self.other_variant.id), response.json()["dev_message"])
        self.assert_variant(self.other_variant, 3, 0)

    def test_deleted_variant_rejected(self):
        self.large.delete()  # soft delete
        response = self.adjust("mark_unavailable", [{"variant_id": self.large.id, "quantity": 1}])
        self.assert_error(response, M.INVALID_VARIANTS)

    def test_fabric_on_variant_without_fabric(self):
        """Example G."""
        response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "fabric_length": "1"}])
        self.assert_error(response, "Wedding Gown (S) does not track fabric length.")

    def test_other_reason_needs_note(self):
        """Example H."""
        response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}], reason="other")
        self.assert_error(response, M.NOTE_REQUIRED_FOR_OTHER)
        response = self.adjust(
            "mark_unavailable", [{"variant_id": self.small.id, "quantity": 1, "note": "Lost"}], reason="other",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_reason_is_optional(self):
        response = self.client.post(API.format(self.product.id), {
            "action": "mark_unavailable", "variants": [{"variant_id": self.small.id, "quantity": 1}],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["data"]["variants"][0]["reason"])
        self.assertIsNone(ProductVariantStockAdjustment.objects.get().reason)
        response = self.adjust("restore", [{"variant_id": self.small.id, "quantity": 1}], reason=None)
        self.assertEqual(response.status_code, 200, response.content)

    def test_request_shape_messages(self):
        cases = [
            ({"action": "x"}, M.INVALID_ACTION),
            ({"reason": "lost"}, M.INVALID_REASON),
            ({"variants": []}, M.VARIANTS_REQUIRED),
            ({"variants": [{"variant_id": i, "quantity": 1} for i in range(1, 102)]}, M.TOO_MANY_VARIANTS),
            ({"variants": [{"variant_id": self.small.id}]}, M.AMOUNT_REQUIRED),
            ({"variants": [{"variant_id": self.small.id, "quantity": 0}]}, M.INVALID_QUANTITY),
            ({"variants": [{"variant_id": self.small.id, "fabric_length": "1.234"}]}, M.INVALID_FABRIC_LENGTH),
            ({"variants": [{"variant_id": "abc", "quantity": 1}]}, M.INVALID_VARIANT_ID),
            ({"note": "x" * 501}, M.NOTE_TOO_LONG),
        ]
        base = {"action": "mark_unavailable", "reason": "damage", "variants": [{"variant_id": self.small.id, "quantity": 1}]}
        for override, msg in cases:
            with self.subTest(msg=msg.message):
                response = self.client.post(API.format(self.product.id), {**base, **override}, format="json")
                self.assert_error(response, msg)
                self.assertTrue(response.json()["dev_message"].startswith(msg.dev_message.split("{")[0]))
        self.assertFalse(ProductVariantStockAdjustment.objects.exists())

    def test_product_of_other_shop_is_404(self):
        response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}], product=self.foreign_product)
        self.assert_error(response, M.PRODUCT_NOT_FOUND, status_code=404)
        self.assertEqual(self.client.get(API.format(self.foreign_product.id)).status_code, 404)
        self.assertEqual(self.client.get(API.format(self.foreign_product.id) + "summary/").status_code, 404)

    def test_database_conflict_returns_409(self):
        from django.db import OperationalError

        with mock.patch("services.api.v3.views.stock_adjustment.create.apply_stock_adjustments", side_effect=OperationalError):
            response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        self.assert_error(response, M.CONFLICT, status_code=409)

    def test_unexpected_error_returns_500_without_internals(self):
        with mock.patch("services.api.v3.views.stock_adjustment.create.apply_stock_adjustments", side_effect=RuntimeError("secret")):
            response = self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        self.assert_error(response, M.SERVER_ERROR, status_code=500)
        self.assertNotIn("secret", response.content.decode())


class ReadEndpointTests(StockAdjustmentAPITestCase):
    def test_history_filters_and_pagination(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}, {"variant_id": self.medium.id, "quantity": 2}])
        self.adjust("restore", [{"variant_id": self.medium.id, "quantity": 1}], reason="maintenance")

        data = self.client.get(API.format(self.product.id)).json()["data"]
        self.assertEqual(data["count"], 3)
        first = data["data"][0]
        self.assertEqual((first["action"], first["variant"]["id"]), ("restore", self.medium.id))
        self.assertEqual(first["created_by"], {"id": self.owner.id, "full_name": "Owner A"})

        def count(**params):
            return self.client.get(API.format(self.product.id), params).json()["data"]["count"]

        self.assertEqual(count(variant_id=self.medium.id), 2)
        self.assertEqual(count(action="restore"), 1)
        self.assertEqual(count(reason="damage"), 2)
        self.assertEqual(count(page_size=1), 3)

        response = self.client.get(API.format(self.product.id), {"action": "lost"})
        self.assert_error(response, M.INVALID_FILTERS)
        self.assertIn("action", response.json()["dev_message"])

    def test_summary(self):
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 3}])
        response = self.client.get(API.format(self.product.id) + "summary/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        medium = next(v for v in data["variants"] if v["variant_id"] == self.medium.id)
        self.assertEqual((medium["stock"], medium["unavailable_stock"], medium["total_stock"]), (2, 3, 5))
        self.assertIsNone(medium["total_fabric_length"])
        self.assertEqual(data["totals"], {"stock": 6, "unavailable_stock": 3, "total_stock": 9})

        fabric = self.client.get(API.format(self.fabric_product.id) + "summary/").json()["data"]["variants"][0]
        self.assertEqual((fabric["fabric_length"], fabric["unavailable_fabric_length"], fabric["total_fabric_length"]),
                         ("20.00", "0.00", "20.00"))

    def test_post_query_count_does_not_grow_with_items(self):
        def queries(variant_ids):
            with CaptureQueriesContext(connection) as ctx:
                response = self.adjust("mark_unavailable", [{"variant_id": vid, "quantity": 1} for vid in variant_ids])
            self.assertEqual(response.status_code, 200, response.content)
            return len(ctx)

        queries([self.large.id])  # warm-up: ContentType lookup for the activity log is cached after this
        self.assertEqual(queries([self.small.id]), queries([self.small.id, self.medium.id, self.large.id]))


class BackwardCompatibilityTests(StockAdjustmentAPITestCase):
    def test_product_variant_serializer_adds_unavailable_fields(self):
        """Product responses gain the two read-only fields; every existing field is kept."""
        response = self.adjust(
            "mark_unavailable", [{"variant_id": self.fabric.id, "fabric_length": "4.50"}], product=self.fabric_product,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.fabric.refresh_from_db()
        data = ProductVariantSerializer(self.fabric).data
        self.assertEqual(list(data), [
            "id", "attribute", "stock", "fabric_length", "unique_code", "external_qr_code",
            "unavailable_stock", "unavailable_fabric_length",
        ])
        # Both fabric values are numbers, so clients can add them.
        self.assertIsInstance(data["fabric_length"], float)
        self.assertIsInstance(data["unavailable_fabric_length"], float)
        self.assertEqual(data["fabric_length"] + data["unavailable_fabric_length"], 20.0)

        serializer = ProductVariantSerializer(
            self.small, data={"unavailable_stock": 9, "unavailable_fabric_length": 9}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data, {})  # read-only: writes are ignored

    def test_existing_variant_view_serializer_unchanged(self):
        """The variant API response does not expose the new columns."""
        self.assertEqual(
            list(ProductVariantViewSerializer(self.small).data),
            ["id", "product", "attribute", "price", "stock", "fabric_length", "external_qr_code"],
        )

    def test_new_fields_not_editable_in_forms(self):
        from django.forms import modelform_factory

        form_class = modelform_factory(ProductVariant, exclude=[])
        self.assertNotIn("unavailable_stock", form_class.base_fields)
        self.assertNotIn("unavailable_fabric_length", form_class.base_fields)


@skipUnlessDBFeature("has_select_for_update")
class ConcurrencyTests(TransactionTestCase):
    def test_parallel_marks_never_go_negative(self):
        """Example I: two requests each mark 2 of 2 units at the same time."""
        shop = Shop.objects.create(name="Race shop", place="Kochi")
        product = make_product(shop, name="Race Gown")
        variant = ProductVariant.objects.create(product=product, attribute="M", stock=2)
        product = Product.objects.select_related("shop_service").get(id=product.id)

        results, barrier = [], threading.Barrier(2)

        def attempt():
            try:
                barrier.wait()
                apply_stock_adjustments(
                    product=product, action="mark_unavailable", user=None,
                    items=[{"variant_id": variant.id, "quantity": 2, "reason": "damage", "note": ""}],
                )
                results.append("applied")
            except StockAdjustmentError:
                results.append("rejected")
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["applied", "rejected"])
        variant.refresh_from_db()
        self.assertEqual((variant.stock, variant.unavailable_stock), (0, 2))
        self.assertEqual(ProductVariantStockAdjustment.objects.filter(variant=variant).count(), 1)


UNAVAILABLE_LIST_API = "/api/v3/service/products/unavailable-stock/"


class UnavailableStockListTests(StockAdjustmentAPITestCase):
    def setUp(self):
        super().setUp()
        # ProductViewSet's 60/min throttle counts across the whole test run.
        from services.api.v3.views.product.helpers.exceptions import ProductSearchThrottle

        patcher = mock.patch.object(ProductSearchThrottle, "allow_request", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, **params):
        response = self.client.get(UNAVAILABLE_LIST_API, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def listed(self, **params):
        """{product_id: [variant_id, ...]} of the current page."""
        return {item["id"]: [v["id"] for v in item["variants"]] for item in self.fetch(**params)["data"]}

    def test_url_resolves_to_action_and_detail_still_works(self):
        from django.urls import resolve

        self.assertEqual(resolve(UNAVAILABLE_LIST_API).func.actions, {"get": "unavailable_stock"})
        self.assertEqual(resolve(f"/api/v3/service/products/{self.product.id}/").func.actions["get"], "retrieve")

    def test_empty_when_nothing_unavailable(self):
        data = self.fetch()
        self.assertEqual((data["count"], data["data"]), (0, []))

    def test_only_products_and_variants_with_unavailable_amounts(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 2}])
        self.adjust(
            "mark_unavailable", [{"variant_id": self.fabric.id, "fabric_length": "4.50"}], product=self.fabric_product,
        )
        # medium / large (no unavailable stock) and other_product are left out.
        self.assertEqual(self.listed(), {self.fabric_product.id: [self.fabric.id], self.product.id: [self.small.id]})

        data = self.fetch()["data"]
        fabric_item, gown_item = data  # newest product first
        self.small.refresh_from_db()
        self.assertEqual(gown_item["totals"], {"unavailable_stock": 2, "unavailable_fabric_length": "0.00"})
        self.assertEqual(gown_item["variants"][0], {
            "id": self.small.id, "attribute": "S", "unique_code": self.small.unique_code,
            "stock": 0, "unavailable_stock": 2, "total_stock": 2,
            "fabric_length": None, "unavailable_fabric_length": "0.00", "total_fabric_length": None,
            "latest_adjustment": {"reason": "damage", "note": None,
                                  "unavailable_stock": 2, "unavailable_fabric_length": None},
        })
        self.assertEqual(gown_item["service_name"], "Dress Rental")
        # Fabric-only variant (unavailable_stock 0) is included.
        self.assertEqual(fabric_item["totals"], {"unavailable_stock": 0, "unavailable_fabric_length": "4.50"})
        variant = fabric_item["variants"][0]
        self.assertEqual(
            (variant["fabric_length"], variant["unavailable_fabric_length"], variant["total_fabric_length"]),
            ("15.50", "4.50", "20.00"),
        )

    def test_fully_restored_variant_and_product_disappear(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1},
                                         {"variant_id": self.medium.id, "quantity": 2}])
        self.adjust("restore", [{"variant_id": self.small.id, "quantity": 1}])
        self.assertEqual(self.listed(), {self.product.id: [self.medium.id]})
        self.adjust("restore", [{"variant_id": self.medium.id, "quantity": 2}])
        self.assertEqual(self.listed(), {})

    def test_inactive_deleted_and_foreign_records_excluded(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1},
                                         {"variant_id": self.medium.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}], product=self.other_product)
        foreign_variant = ProductVariant.objects.create(product=self.foreign_product, attribute="M", stock=0)
        ProductVariant.objects.filter(id=foreign_variant.id).update(unavailable_stock=3)

        ProductVariant.objects.filter(id=self.small.id).update(is_active=False)
        self.medium.delete()  # soft delete
        Product.objects.filter(id=self.other_product.id).update(is_active=False)
        self.assertEqual(self.listed(), {})

    def test_inactive_service_excluded(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        ShopOwnerService.objects.filter(id=self.product.shop_service_id).update(is_active=False)
        self.assertEqual(self.listed(), {})

    def test_shop_service_filter(self):
        rental_service = self.product.shop_service
        tailoring = make_product(self.shop, name="Kurta Cloth", service_name="Tailoring")
        cloth = ProductVariant.objects.create(product=tailoring, attribute="Blue", stock=4)
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": cloth.id, "quantity": 1}], product=tailoring)

        self.assertEqual(self.listed(shop_service_id=rental_service.id), {self.product.id: [self.small.id]})
        self.assertEqual(self.listed(shop_service_id=tailoring.shop_service_id), {tailoring.id: [cloth.id]})
        self.assertEqual(set(self.listed()), {self.product.id, tailoring.id})

    def test_shop_service_errors(self):
        for value in ("abc", "0", "-1", "1.5"):
            response = self.client.get(UNAVAILABLE_LIST_API, {"shop_service_id": value})
            self.assert_error(response, M.INVALID_SERVICE_ID)

        inactive = make_product(self.shop, name="Old", service_name="Old Service").shop_service
        ShopOwnerService.objects.filter(id=inactive.id).update(is_active=False)
        for service_id in (self.foreign_product.shop_service_id, inactive.id, 999999):
            response = self.client.get(UNAVAILABLE_LIST_API, {"shop_service_id": service_id})
            self.assert_error(response, M.SERVICE_NOT_FOUND, status_code=404)

    def test_pagination(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}], product=self.other_product)
        data = self.fetch(page_size=1)
        self.assertEqual((data["count"], data["total_pages"], len(data["data"])), (2, 2, 1))
        self.assertEqual(self.client.get(UNAVAILABLE_LIST_API, {"page": 3}).status_code, 404)

    def test_query_count_is_constant(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.fetch()
            return len(ctx)

        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        one_product = queries()
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 1},
                                         {"variant_id": self.large.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}], product=self.other_product)
        self.adjust(
            "mark_unavailable", [{"variant_id": self.fabric.id, "fabric_length": "1.00"}], product=self.fabric_product,
        )
        self.assertEqual(queries(), one_product)


class UnavailableStockSearchTests(StockAdjustmentAPITestCase):
    """search / search_by on the unavailable-stock list (same rules as the product list API)."""

    def setUp(self):
        super().setUp()
        from services.api.v3.views.product.helpers.exceptions import ProductSearchThrottle

        patcher = mock.patch.object(ProductSearchThrottle, "allow_request", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mark(self, product, attribute="M", **fields):
        """A product with one variant that has a unit out of service."""
        for key, value in fields.items():
            setattr(product, key, value)
        product.save()
        variant = ProductVariant.objects.create(product=product, attribute=attribute, stock=0)
        ProductVariant.objects.filter(id=variant.id).update(unavailable_stock=1)
        return product

    def ids(self, **params):
        response = self.client.get(UNAVAILABLE_LIST_API, params)
        self.assertEqual(response.status_code, 200, response.content)
        return [item["id"] for item in response.json()["data"]["data"]]

    def test_name_search_is_case_insensitive_and_keeps_list_order(self):
        contains = self.mark(make_product(self.shop, name="Wedding Gown Deluxe"))
        exact = self.mark(make_product(self.shop, name="Gown"))
        starts = self.mark(make_product(self.shop, name="Gown Long"))
        self.mark(make_product(self.shop, name="Sherwani Royal"))
        # No relevance ranking: same order as the unsearched list (no adjustment rows -> newest product first).
        self.assertEqual(self.ids(search="GOWN"), [starts.id, exact.id, contains.id])
        self.assertEqual(self.ids(search="  gown ", search_by=" Name "), [starts.id, exact.id, contains.id])

    def test_color_category_model_search(self):
        product = self.mark(make_product(self.shop, name="Coat"), color="Maroon", category="Bridal", model="X1")
        self.mark(make_product(self.shop, name="Other"), color="Blue", category="Casual", model="Y2")
        for field, text in (("color", "maro"), ("category", "brid"), ("model", "x1")):
            self.assertEqual(self.ids(search=text, search_by=field), [product.id], field)

    def test_blank_search_is_no_search(self):
        self.mark(make_product(self.shop, name="A"))
        self.mark(make_product(self.shop, name="B"))
        self.assertEqual(self.ids(search="   "), self.ids())
        self.assertEqual(len(self.ids()), 2)

    def test_matching_product_without_unavailable_stock_not_listed(self):
        self.mark(make_product(self.shop, name="Gown Listed"))
        # self.product ("Wedding Gown") matches but has nothing out of service.
        self.assertEqual(len(self.ids(search="gown")), 1)

    def test_service_attribute_search(self):
        product = self.mark(make_product(self.shop, name="Saree"))
        other = self.mark(make_product(self.shop, name="Saree 2"))
        general = product.shop_service.general_service
        template = ServiceAttributeTemplate.objects.create(
            general_service=general, attribute_key="fabric_type", attribute_label="Fabric", attribute_type="text",
        )
        ServiceAttributeValue.objects.create(product=product, attribute=template, value="Pure Silk")
        ServiceAttributeValue.objects.create(product=other, attribute=template, value="Cotton")
        self.assertEqual(self.ids(search="silk", search_by="FABRIC_TYPE"), [product.id])
        self.assertEqual(self.ids(search="silk", search_by="unknown_key"), [])

    def test_attribute_of_another_service_does_not_match(self):
        product = self.mark(make_product(self.shop, name="Saree"))
        foreign_general = make_product(self.shop, name="X", service_name="Tailoring").shop_service.general_service
        template = ServiceAttributeTemplate.objects.create(
            general_service=foreign_general, attribute_key="fabric_type", attribute_label="Fabric",
            attribute_type="text",
        )
        ServiceAttributeValue.objects.create(product=product, attribute=template, value="Silk")
        self.assertEqual(self.ids(search="silk", search_by="fabric_type"), [])

    def test_gadget_service_matches_active_variant_attribute(self):
        phone = self.mark(make_product(self.shop, name="Phone", service_name="Gadget Rental"), attribute="128GB")
        inactive = self.mark(make_product(self.shop, name="Tablet", service_name="Gadget Rental"), attribute="Blue")
        ProductVariant.objects.create(product=inactive, attribute="128GB", stock=1, is_active=False)
        dress = self.mark(make_product(self.shop, name="Dress"), attribute="128GB")  # not a gadget service
        self.assertEqual(self.ids(search="128gb"), [phone.id])
        self.assertNotIn(dress.id, self.ids(search="128gb"))

    def test_search_with_service_filter_and_pagination(self):
        gadget = make_product(self.shop, name="Phone", service_name="Gadget Rental")
        self.mark(gadget, attribute="Phone Case")  # name and variant both match: still one row
        ProductVariant.objects.create(product=gadget, attribute="Phone Pro", stock=1)
        self.mark(make_product(self.shop, name="Phone Stand", service_name="Gadget Rental"))
        self.mark(make_product(self.shop, name="Phone Bag"))  # other service

        response = self.client.get(UNAVAILABLE_LIST_API, {
            "search": "phone", "shop_service_id": gadget.shop_service_id, "page_size": 1,
        })
        data = response.json()["data"]
        self.assertEqual((data["count"], data["total_pages"]), (2, 2))
        self.assertIn(gadget.id, self.ids(search="phone", shop_service_id=gadget.shop_service_id))
        self.assertEqual(len(self.ids(search="phone")), 3)


class UnavailableStockOrderAndLatestAdjustmentTests(StockAdjustmentAPITestCase):
    """Products ordered by their listed variants' latest adjustment; each variant's latest_adjustment."""

    def setUp(self):
        super().setUp()
        from services.api.v3.views.product.helpers.exceptions import ProductSearchThrottle

        patcher = mock.patch.object(ProductSearchThrottle, "allow_request", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, **params):
        response = self.client.get(UNAVAILABLE_LIST_API, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def ids(self, **params):
        return [item["id"] for item in self.fetch(**params)["data"]]

    def latest(self, variant):
        for item in self.fetch()["data"]:
            for row in item["variants"]:
                if row["id"] == variant.id:
                    return row["latest_adjustment"]
        self.fail(f"variant {variant.id} not listed")

    def test_most_recently_adjusted_product_first(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}], product=self.other_product)
        self.assertEqual(self.ids(), [self.other_product.id, self.product.id])
        # A newer adjustment on another variant of the older product moves it to the top.
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 1}])
        self.assertEqual(self.ids(), [self.product.id, self.other_product.id])
        # So does a partial restore of a listed variant.
        self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}], product=self.other_product)
        self.adjust("restore", [{"variant_id": self.small.id, "quantity": 1}])  # small no longer listed
        self.assertEqual(self.ids(), [self.other_product.id, self.product.id])

    def test_restore_of_unlisted_variant_does_not_move_product(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1},
                                         {"variant_id": self.large.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": self.other_variant.id, "quantity": 1}], product=self.other_product)
        self.adjust("restore", [{"variant_id": self.large.id, "quantity": 1}])  # large fully restored
        self.assertEqual(self.ids(), [self.other_product.id, self.product.id])

    def test_product_listed_once_with_count(self):
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1},
                                         {"variant_id": self.medium.id, "quantity": 1}])
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 1}], reason="maintenance")
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        data = self.fetch(search="gown")
        self.assertEqual(data["count"], 1)
        self.assertEqual([item["id"] for item in data["data"]], [self.product.id])
        self.assertEqual([v["id"] for v in data["data"][0]["variants"]], [self.small.id, self.medium.id])

    def test_search_follows_adjustment_order(self):
        gown = make_product(self.shop, name="Gown")
        gown_variant = ProductVariant.objects.create(product=gown, attribute="M", stock=2)
        self.adjust("mark_unavailable", [{"variant_id": gown_variant.id, "quantity": 1}], product=gown)
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])  # "Wedding Gown", newer
        self.assertEqual(self.ids(search="gown"), [self.product.id, gown.id])

    def test_product_without_adjustment_rows_listed_last(self):
        ProductVariant.objects.filter(id=self.other_variant.id).update(unavailable_stock=1)  # no history row
        self.adjust("mark_unavailable", [{"variant_id": self.small.id, "quantity": 1}])
        self.assertEqual(self.ids(), [self.product.id, self.other_product.id])
        self.assertIsNone(self.latest(self.other_variant))

    def test_latest_adjustment_is_newest_mark_unavailable_row(self):
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 1}], note="Stain")
        self.adjust("mark_unavailable", [{"variant_id": self.medium.id, "quantity": 2}],
                    reason="maintenance", note="Re-stitch")
        self.assertEqual(self.latest(self.medium), {
            "reason": "maintenance", "note": "Re-stitch", "unavailable_stock": 2, "unavailable_fabric_length": None,
        })
        # A partial restore is not shown; the latest mark_unavailable row still is.
        self.adjust("restore", [{"variant_id": self.medium.id, "quantity": 1}])
        latest = self.latest(self.medium)
        self.assertEqual(latest["note"], "Re-stitch")
        self.assertNotIn("action", latest)

    def test_fabric_only_row_without_reason_or_note(self):
        payload = {"action": "mark_unavailable", "variants": [{"variant_id": self.fabric.id, "fabric_length": "4.50"}]}
        response = self.client.post(API.format(self.fabric_product.id), payload, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.latest(self.fabric), {
            "reason": None, "note": None, "unavailable_stock": None, "unavailable_fabric_length": "4.50",
        })
