from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APITestCase

from authentication.models import User
from purchase.api.v3.views.vendor_viewset import VendorViewSet
from purchase.models import Purchase, Vendor
from shop.models import Shop

IST = ZoneInfo("Asia/Kolkata")
ORDER_KEYS = {
    "id",
    "purchase_code",
    "invoice_number",
    "purchase_date",
    "created_at",
    "purchase_total",
    "paid_amount",
}


def ist(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


class VendorMonthlySummaryTests(APITestCase):
    """GET /api/v3/purchases/vendors/monthly-summary/{vendor_id}/"""

    def setUp(self):
        self.shop = Shop.objects.create(name="Main Shop")
        self.other_shop = Shop.objects.create(name="Other Shop")
        self.user = User.objects.create_user(phone="+919876500001", password="pass", role="OWNER")
        self.vendor = Vendor.objects.create(shop=self.shop, name="Acme Fabrics")
        self.other_vendor = Vendor.objects.create(shop=self.shop, name="Other Vendor")

        self.client.force_authenticate(self.user)

        # Isolate the endpoint from subscription/role/shop-header plumbing,
        # which is covered by those components' own behaviour.
        patches = [
            mock.patch.object(VendorViewSet, "permission_classes", [IsAuthenticated]),
            mock.patch(
                "purchase.api.v3.views.vendor_viewset.base.get_active_shop_id",
                return_value=self.shop.id,
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    # ---- helpers ----

    def url(self, vendor_id=None):
        vendor_id = self.vendor.id if vendor_id is None else vendor_id
        return reverse("vendor-vendor-monthly-summary", kwargs={"vendor_id": vendor_id})

    def make_purchase(self, purchase_date, total="100.00", paid="0.00", vendor=None,
                      shop=None, invoice_number=None, created_at=None):
        purchase = Purchase.objects.create(
            shop=shop or self.shop,
            vendor=vendor or self.vendor,
            purchase_date=purchase_date,
            purchase_total=Decimal(total),
            paid_amount=Decimal(paid),
            invoice_number=invoice_number,
        )
        if created_at is not None:
            Purchase.objects.filter(pk=purchase.pk).update(created_at=created_at)
        return purchase

    def get(self, vendor_id=None, **params):
        return self.client.get(self.url(vendor_id), params)

    def months(self, response):
        return response.data["data"]["data"]["monthly_summary"]

    def overall(self, response):
        return response.data["data"]["data"]["overall_summary"]

    def assertOverall(self, response, total, paid):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.overall(response),
            {"overall_total_amount": total, "overall_total_paid_amount": paid},
        )

    def seed_ledger(self):
        """Oct 2025 x2, Sep 2025, Oct 2024 for self.vendor, plus noise that must never count."""
        self.make_purchase(ist(2025, 10, 18), total="12000.00", paid="10000.00")
        self.make_purchase(ist(2025, 10, 2), total="6500.00", paid="0.00")
        self.make_purchase(ist(2025, 9, 29), total="4200.00", paid="4200.00")
        self.make_purchase(ist(2024, 10, 12), total="8000.00", paid="5000.00")
        # Noise: other vendor, other shop, soft-deleted.
        self.make_purchase(ist(2025, 10, 5), total="999.00", paid="999.00", vendor=self.other_vendor)
        foreign_vendor = Vendor.objects.create(shop=self.other_shop, name="Foreign")
        self.make_purchase(ist(2025, 10, 5), total="999.00", paid="999.00",
                           vendor=foreign_vendor, shop=self.other_shop)
        self.make_purchase(ist(2025, 10, 6), total="999.00", paid="999.00").delete()

    # ---- vendor filtering ----

    def test_returns_only_this_vendors_purchases_in_this_shop(self):
        own = self.make_purchase(ist(2025, 10, 5))
        self.make_purchase(ist(2025, 10, 6), vendor=self.other_vendor)
        foreign_vendor = Vendor.objects.create(shop=self.other_shop, name="Foreign")
        self.make_purchase(ist(2025, 10, 7), vendor=foreign_vendor, shop=self.other_shop)
        self.make_purchase(ist(2025, 10, 8)).delete()  # soft-deleted

        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["count"], 1)
        orders = self.months(response)["October 2025"]["vendor_orders"]
        self.assertEqual([o["id"] for o in orders], [own.id])

    def test_unknown_vendor_returns_404(self):
        response = self.get(vendor_id=999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["status"], "not_found")
        self.assertEqual(response.data["message"], "Vendor not found.")

    def test_vendor_of_another_shop_returns_404(self):
        foreign_vendor = Vendor.objects.create(shop=self.other_shop, name="Foreign")
        response = self.get(vendor_id=foreign_vendor.id)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["message"], "Vendor not found.")

    def test_soft_deleted_vendor_returns_404(self):
        self.vendor.delete()
        response = self.get()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["message"], "Vendor not found.")

    # ---- purchase_date grouping ----

    def test_groups_by_purchase_date_not_created_at(self):
        self.make_purchase(ist(2025, 9, 29), created_at=ist(2025, 10, 3))

        months = self.months(self.get())

        self.assertEqual(list(months), ["September 2025"])

    def test_month_boundary_uses_shop_local_time(self):
        # 30 Sep 20:00 UTC == 1 Oct 01:30 IST -> October.
        self.make_purchase(datetime(2025, 9, 30, 20, 0, tzinfo=dt_timezone.utc))

        months = self.months(self.get())

        self.assertEqual(list(months), ["October 2025"])
        order = months["October 2025"]["vendor_orders"][0]
        self.assertEqual(order["purchase_date"], "01-10-2025 01:30:00")

    def test_months_and_orders_are_newest_first(self):
        sep = self.make_purchase(ist(2025, 9, 10))
        oct_early = self.make_purchase(ist(2025, 10, 2))
        oct_late = self.make_purchase(ist(2025, 10, 18))

        months = self.months(self.get())

        self.assertEqual(list(months), ["October 2025", "September 2025"])
        self.assertEqual(
            [o["id"] for o in months["October 2025"]["vendor_orders"]],
            [oct_late.id, oct_early.id],
        )
        self.assertEqual([o["id"] for o in months["September 2025"]["vendor_orders"]], [sep.id])

    # ---- filters ----

    def test_year_and_month_filter_on_purchase_date(self):
        self.make_purchase(ist(2025, 10, 5), created_at=ist(2024, 10, 5))
        self.make_purchase(ist(2024, 10, 5), created_at=ist(2025, 10, 5))
        self.make_purchase(ist(2025, 9, 5))

        months = self.months(self.get(year=2025, month=10))

        self.assertEqual(list(months), ["October 2025"])
        self.assertEqual(len(months["October 2025"]["vendor_orders"]), 1)

    def test_month_without_year_matches_every_year(self):
        self.make_purchase(ist(2025, 10, 5))
        self.make_purchase(ist(2024, 10, 5))
        self.make_purchase(ist(2025, 9, 5))

        months = self.months(self.get(month=10))

        self.assertEqual(list(months), ["October 2025", "October 2024"])

    def test_blank_year_is_treated_as_not_provided(self):
        self.make_purchase(ist(2025, 10, 5))
        response = self.get(year="", month="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["count"], 1)

    # ---- validation ----

    def test_invalid_params_return_exact_messages(self):
        cases = [
            ({"year": "2019"}, "Year must be between 2020 and 2030."),
            ({"year": "abc"}, "Year must be a valid number."),
            ({"month": "13"}, "Month must be between 1 and 12."),
            ({"month": "x"}, "Month must be a valid number."),
        ]
        for params, message in cases:
            with self.subTest(params=params):
                response = self.get(**params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["status"], "validation_error")
                self.assertEqual(response.data["message"], message)
                # dev_message must not reflect raw user input
                self.assertNotIn(next(iter(params.values())), response.data["dev_message"])

    def test_vendor_id_out_of_range_returns_400(self):
        for vendor_id in ("0", "99999999999999999999"):
            with self.subTest(vendor_id=vendor_id):
                response = self.get(vendor_id=vendor_id)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["message"], "Invalid vendor ID provided.")

    # ---- pagination & totals ----

    def test_invalid_page_returns_standard_404(self):
        self.make_purchase(ist(2025, 10, 5))
        for page in ("999", "abc"):
            with self.subTest(page=page):
                response = self.get(page=page)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.data["status"], "not_found")
                self.assertEqual(response.data["message"], "The requested page is not available.")

    def test_month_totals_cover_whole_month_across_pages(self):
        self.make_purchase(ist(2025, 10, 18), total="12000.00", paid="10000.00")
        self.make_purchase(ist(2025, 10, 2), total="6500.00", paid="0.00")
        self.make_purchase(ist(2025, 9, 29), total="4200.00", paid="4200.00")

        for page in (1, 2):
            with self.subTest(page=page):
                october = self.months(self.get(page=page, page_size=1))["October 2025"]
                self.assertEqual(len(october["vendor_orders"]), 1)
                self.assertEqual(october["total"], 18500.0)
                self.assertEqual(october["paid_total"], 10000.0)

        september = self.months(self.get(page=3, page_size=1))["September 2025"]
        self.assertEqual(september["total"], 4200.0)
        self.assertEqual(september["paid_total"], 4200.0)

    # ---- response shape ----

    def test_order_fields_and_formats(self):
        purchase = self.make_purchase(
            ist(2025, 10, 18, 11, 30), total="12000.00", paid="10000.00",
            invoice_number="INV-7781", created_at=ist(2025, 10, 18, 11, 42),
        )

        response = self.get()

        self.assertEqual(response.data["status"], "success")
        order = self.months(response)["October 2025"]["vendor_orders"][0]
        self.assertEqual(set(order), ORDER_KEYS)
        self.assertEqual(order, {
            "id": purchase.id,
            "purchase_code": purchase.purchase_code,
            "invoice_number": "INV-7781",
            "purchase_date": "18-10-2025 11:30:00",
            "created_at": "18-10-2025 11:42:00",
            "purchase_total": 12000.0,
            "paid_amount": 10000.0,
        })

    def test_empty_result_returns_empty_object(self):
        response = self.get(year=2021)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["count"], 0)
        self.assertEqual(self.months(response), {})
        self.assertOverall(response, 0.0, 0.0)

    # ---- overall summary ----

    def test_overall_without_filter_covers_all_vendor_purchases(self):
        self.seed_ledger()
        self.assertOverall(self.get(), 30700.0, 19200.0)

    def test_overall_with_year_covers_that_year_only(self):
        self.seed_ledger()
        response = self.get(year=2025)
        self.assertOverall(response, 22700.0, 14200.0)
        self.assertEqual(list(self.months(response)), ["October 2025", "September 2025"])

    def test_overall_with_year_and_month_covers_that_month_only(self):
        self.seed_ledger()
        response = self.get(year=2025, month=10)
        self.assertOverall(response, 18500.0, 10000.0)
        self.assertEqual(list(self.months(response)), ["October 2025"])

    def test_overall_with_month_only_covers_that_month_in_every_year(self):
        self.seed_ledger()
        response = self.get(month=10)
        self.assertOverall(response, 26500.0, 15000.0)
        self.assertEqual(list(self.months(response)), ["October 2025", "October 2024"])

    def test_overall_month_filter_uses_shop_local_time(self):
        # 30 Sep 20:00 UTC == 1 Oct IST -> counts toward October.
        self.make_purchase(datetime(2025, 9, 30, 20, 0, tzinfo=dt_timezone.utc), total="700.00", paid="100.00")
        self.make_purchase(ist(2025, 9, 30, 23, 0), total="50.00", paid="50.00")
        self.assertOverall(self.get(year=2025, month=10), 700.0, 100.0)

    def test_overall_is_identical_on_every_page_and_matches_month_totals(self):
        self.seed_ledger()
        pages = [self.get(page=page, page_size=1) for page in (1, 2, 3, 4)]

        for response in pages:
            self.assertOverall(response, 30700.0, 19200.0)

        month_totals = {}
        for response in pages:
            for key, month in self.months(response).items():
                month_totals[key] = (month["total"], month["paid_total"])
        self.assertEqual(sum(t for t, _ in month_totals.values()), 30700.0)
        self.assertEqual(sum(p for _, p in month_totals.values()), 19200.0)

    # ---- performance ----

    def test_query_count_is_constant(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.get(page_size=100)
            self.assertEqual(response.status_code, 200)
            return len(ctx.captured_queries)

        for day in (1, 2):
            self.make_purchase(ist(2025, 10, day))
        small = count_queries()

        for i in range(28):
            self.make_purchase(ist(2025, 1 + i % 10, 1 + i))
        large = count_queries()

        # vendor lookup + count + page + month totals
        self.assertEqual(small, 4)
        self.assertEqual(large, small)

    # ---- failure handling ----

    def test_database_error_returns_friendly_500(self):
        with mock.patch.object(Purchase.objects, "filter", side_effect=DatabaseError("relation exploded")):
            response = self.get()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["status"], "error")
        self.assertEqual(response.data["message"], "Unable to fetch monthly summary. Please try again.")
        self.assertNotIn("exploded", str(response.data))
