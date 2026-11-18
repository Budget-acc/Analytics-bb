"""
Booking List PDF export tests.

- BookingList*Tests (SimpleTestCase): no database - filters' validation and
  ordering rules, report helpers, request parsing and the PDF renderer.
- BookingListParityTests / BookingListExportAPITests (TestCase, PostgreSQL):
  the PDF's bookings must be exactly the ones (and in the same order) the
  Mobile V4 list and the Desktop V5 desktop-list return for the same filters.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django.utils.timezone import localdate
from rest_framework.test import APIClient

from pdf_service.api.v3.utils.booking_list_pdf import (
    BookingListFilterError, BookingListPDFService, build_queryset, collect_rows, download_filename,
    filter_lines,
)
from pdf_service.api.v3.utils.booking_list_pdf.report import status_label
from pdf_service.api.v3.views.export_booking_list import ExportBookingListPDFView, MSG_INVALID_BODY, MSG_PLATFORM

TODAY = date(2026, 10, 26)
SHOP_INFO = {"name": "BB & Sons <Bridal>", "address": "Chamayam Tower", "city": "Kozhikode",
             "pincode": "679333", "phone": "+91 8848099399", "email": "shop@example.com"}


def _pages(pdf_bytes):
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))


def _row(sno, **overrides):
    row = {"sno": sno, "pickup": "01/11/2026 10:00 AM", "invoice_id": f"{sno:06d}",
           "products": ["Silk Saree", "Gold Necklace"], "client_name": "Rahul Menon",
           "client_phone": "+919876543210", "paid": Decimal("2000"), "pending": Decimal("1500")}
    row.update(overrides)
    return row


def _order(qs):
    return [str(term) for term in qs.query.order_by]


class BookingListFilterRulesTests(SimpleTestCase):
    """Validation and ordering mirror the list APIs (querysets are lazy: no DB)."""

    def test_unknown_platform(self):
        with self.assertRaises(BookingListFilterError) as ctx:
            build_queryset("tablet", 1, {}, TODAY)
        self.assertEqual(ctx.exception.field, "platform")

    def test_desktop_rejects_what_desktop_list_rejects(self):
        cases = {
            "status": ({"status": "future"}, "Invalid status filter."),
            "start_date": ({"start_date": "2026/11/01"}, "Invalid start_date format. Use DD-MM-YYYY or YYYY-MM-DD."),
            "end_date": ({"end_date": "31-02-2026"}, "Invalid end_date format. Use DD-MM-YYYY or YYYY-MM-DD."),
            "range": ({"start_date": "10-11-2026", "end_date": "01-11-2026"}, "start_date cannot be after end_date."),
            "purchase_mode": ({"purchase_mode": "drone"},
                              "Invalid purchase_mode filter. Must be one of: normal, package, courier, pickup."),
            "search": ({"search": "x" * 101}, "Search query too long. Maximum 100 characters."),
        }
        for name, (params, message) in cases.items():
            with self.subTest(name):
                with self.assertRaises(BookingListFilterError) as ctx:
                    build_queryset("desktop", 1, params, TODAY)
                self.assertEqual(ctx.exception.message, message)

    def test_mobile_is_as_lenient_as_mobile_list(self):
        # Unknown status, bad dates and long search are ignored by the mobile list.
        build_queryset("mobile", 1, {"status": "returns", "start_date": "bad", "search": "x" * 300}, TODAY)
        with self.assertRaises(BookingListFilterError) as ctx:
            build_queryset("mobile", 1, {"purchase_mode": "drone"}, TODAY)
        self.assertEqual(ctx.exception.message, "Invalid purchase_mode.")

    def test_mobile_ordering(self):
        self.assertEqual(_order(build_queryset("mobile", 1, {"status": "all"}, TODAY)), ["-created_at", "-id"])
        self.assertEqual(_order(build_queryset("mobile", 1, {"status": "past"}, TODAY)), ["-pickup_date"])
        self.assertEqual(_order(build_queryset("mobile", 1, {"status": "Upcoming"}, TODAY)), ["pickup_date"])
        self.assertEqual(_order(build_queryset("mobile", 1, {"search": "ra"}, TODAY)), ["search_rank", "-id"])

    def test_desktop_ordering(self):
        def order(**params):
            return _order(build_queryset("desktop", 1, params, TODAY))

        self.assertEqual(order(status="UPCOMING"), ["pickup_date", "created_at"])
        self.assertEqual(order(status="not_returned"), ["return_date", "pickup_date"])
        self.assertEqual(order(status="all"), ["-created_at", "-id"])
        self.assertIn("completed_at", order(status="cancelled")[0])
        self.assertEqual(order(search="000"), ["search_rank", "sort_key", "-id"])


class BookingListReportTests(SimpleTestCase):
    def test_status_label_follows_each_list(self):
        self.assertEqual(status_label("desktop", {"status": " Upcoming "}), "Upcoming")
        self.assertEqual(status_label("mobile", {"status": "Upcoming"}), "All")  # mobile is case-sensitive
        self.assertEqual(status_label("mobile", {"status": "measurements"}), "Measurements")
        self.assertEqual(status_label("desktop", {}), "All")

    def test_filter_lines_and_filename(self):
        params = {"status": "not_returned", "start_date": "01-11-2026", "end_date": "2026-11-30",
                  "purchase_mode": "courier", "search": "rahul"}
        line, generated = filter_lines("desktop", params, timezone.now())
        # Platform (Mobile/Desktop) and Status are not printed.
        self.assertEqual(line, 'Pickup: 01/11/2026 - 30/11/2026 | Purchase mode: Courier | Search: "rahul"')
        self.assertTrue(generated.startswith("Generated on "))
        only_generated = filter_lines("mobile", {"status": "upcoming"}, timezone.now())
        self.assertEqual(len(only_generated), 1)
        self.assertTrue(only_generated[0].startswith("Generated on "))
        self.assertEqual(download_filename("desktop", params, TODAY), "Bookings_Desktop_Not_Returned_26-10-2026.pdf")
        self.assertEqual(download_filename("mobile", {}, TODAY), "Bookings_Mobile_All_26-10-2026.pdf")

    def test_collect_rows_amounts_and_order(self):
        when = timezone.make_aware(datetime(2026, 11, 1, 10, 0))
        bookings = [
            NS(pickup_date=when, shop_booking_id="000912", advance_amount=2000, total_amount=4000, discount_amount=500,
               client_identity=NS(client_name="Rahul", phone_1_e164="+919876543210"),
               pdf_items=[NS(product_name="Saree"), NS(product_name=""), NS(product_name="Necklace")]),
            NS(pickup_date=when, shop_booking_id=None, advance_amount=3000, total_amount=2500, discount_amount=None,
               client_identity=None, pdf_items=[]),
        ]
        rows, totals = collect_rows(NS(iterator=lambda chunk_size: iter(bookings)))
        self.assertEqual([r["sno"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["products"], ["Saree", "Necklace"])
        self.assertEqual((rows[0]["paid"], rows[0]["pending"]), (Decimal(2000), Decimal(1500)))
        self.assertEqual(rows[1]["pending"], Decimal(0))  # never negative (desktop remaining_amount)
        self.assertEqual(totals, {"count": 2, "paid": Decimal(5000), "pending": Decimal(1500)})


class BookingListRequestParsingTests(SimpleTestCase):
    def read(self, body):
        return ExportBookingListPDFView()._read_body(body)

    def test_valid_body_normalized(self):
        params, error = self.read({"platform": " Desktop ", "status": "upcoming", "search": 912})
        self.assertIsNone(error)
        self.assertEqual(params, {"platform": "desktop", "status": "upcoming", "search": "912",
                                  "start_date": None, "end_date": None, "purchase_mode": None})

    def test_invalid_bodies(self):
        for body, message in (([1, 2], MSG_INVALID_BODY), ({}, MSG_PLATFORM), ({"platform": "tablet"}, MSG_PLATFORM),
                              ({"platform": "mobile", "status": ["upcoming"]}, MSG_INVALID_BODY),
                              ({"platform": "mobile", "search": True}, MSG_INVALID_BODY)):
            with self.subTest(body=body):
                params, error = self.read(body)
                self.assertIsNone(params)
                self.assertEqual(error.status_code, 400)
                self.assertEqual(error.data["message"], message)


class BookingListPDFRendererTests(SimpleTestCase):
    def generate(self, rows):
        totals = {"count": len(rows), "paid": sum((r["paid"] for r in rows), Decimal(0)),
                  "pending": sum((r["pending"] for r in rows), Decimal(0))}
        return BookingListPDFService.generate(SHOP_INFO, rows, totals, ["Desktop | Status: All", "Generated on x"])

    def test_valid_pdf_with_escaped_text(self):
        pdf = self.generate([_row(1, client_name="A & B <C>", products=["Saree <XL>", "Belt & Pin"])])
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(_pages(pdf), 1)

    def test_empty_result(self):
        self.assertTrue(self.generate([]).startswith(b"%PDF"))

    def test_long_list_spans_pages(self):
        self.assertGreater(_pages(self.generate([_row(i) for i in range(1, 301)])), 3)


# ---------------------------------------------------------------------------
# Database tests (PostgreSQL)
# ---------------------------------------------------------------------------
class _BookingFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        from authentication.models import User
        from booking_v2.models import BookingV2, Client
        from shop.models import Shop, ShopUser

        cls.shop = Shop.objects.create(name="PDF Shop", place="Kochi")
        cls.other_shop = Shop.objects.create(name="Other Shop", place="Calicut")
        cls.owner = User.objects.create_user(phone="+919000000101", password="x", role="OWNER")
        ShopUser.objects.create(shop=cls.shop, user=cls.owner, role="OWNER")

        rahul = Client.objects.create(shop=cls.shop, client_name="Rahul Menon", phone_1_e164="+919876543210")
        anu = Client.objects.create(shop=cls.shop, client_name="Anu", phone_1_e164="+919812345678")
        now = timezone.now()
        day = timedelta(days=1)
        specs = [
            # (pickup offset, return offset, booking_status, delivery_status, purchase_mode, client, completed)
            (-5, -2, "upcoming", "booked", "normal", rahul, False),      # not returned / expired / past
            (-1, 2, "upcoming", "delivered", "courier", anu, False),     # returns / pending / future
            (3, 5, "upcoming", "booked", "pickup", rahul, False),        # upcoming
            (10, 12, "upcoming", "cancelled", "normal", anu, False),     # cancelled (desktop) / upcoming (mobile)
            (-9, -7, "completed", "returned", "package", rahul, True),   # completed
            (-3, -1, "upcoming", "returned", "courier", None, False),    # returned but still upcoming
            (0, 1, "upcoming", "booked", "normal", anu, False),          # pickup today
        ]
        for pickup, ret, booking_status, delivery, mode, client, completed in specs:
            BookingV2.objects.create(
                user=cls.owner, shop=cls.shop, client_identity=client, total_amount=4000, advance_amount=1500,
                discount_amount=500, pickup_date=now + pickup * day, return_date=now + ret * day,
                booking_status=booking_status, delivery_status=delivery, purchase_mode=mode,
                completed_at=now + pickup * day if completed else None,
            )
        BookingV2.objects.create(
            user=cls.owner, shop=cls.other_shop, total_amount=100, advance_amount=0,
            pickup_date=now, purchase_mode="normal",
        )

    def setUp(self):
        from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

        patcher = mock.patch.object(SubscriptionActivePermission, "has_permission", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.client.credentials(HTTP_X_ACTIVE_SHOP_ID=str(self.shop.id))


class BookingListParityTests(_BookingFixtureMixin, TestCase):
    MOBILE_URL = "/api/v4/bookings/bookings/"
    DESKTOP_URL = "/api/v5/bookings/bookings/desktop-list/"

    def list_ids(self, url, params):
        response = self.client.get(url, {**{k: v for k, v in params.items() if v is not None}, "page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        return [row["id"] for row in response.json()["data"]["data"]]

    def pdf_ids(self, platform, params):
        return [b.id for b in build_queryset(platform, self.shop.id, params, localdate())]

    def assert_parity(self, platform, url, statuses, extra_filters):
        for status in statuses:
            for extra in extra_filters:
                params = {"status": status, **extra}
                with self.subTest(platform=platform, **params):
                    self.assertEqual(self.pdf_ids(platform, params), self.list_ids(url, params))

    EXTRA_FILTERS = [
        {},
        {"search": "rahul"},
        {"search": "0000"},
        {"purchase_mode": "courier"},
        {"start_date": (localdate() - timedelta(days=4)).strftime("%d-%m-%Y"),
         "end_date": (localdate() + timedelta(days=4)).isoformat()},
    ]

    def test_mobile_matches_v4_list(self):
        self.assert_parity("mobile", self.MOBILE_URL,
                           [None, "completed", "upcoming", "future", "past", "measurements", "all", "unknown"],
                           self.EXTRA_FILTERS)

    def test_desktop_matches_v5_desktop_list(self):
        self.assert_parity("desktop", self.DESKTOP_URL,
                           [None, "completed", "upcoming", "returns", "pending", "not_returned", "cancelled",
                            "expired", "all"],
                           self.EXTRA_FILTERS)

    def test_other_shops_bookings_never_included(self):
        ids = self.pdf_ids("desktop", {"status": "all"})
        self.assertEqual(len(ids), 7)


class BookingListExportAPITests(_BookingFixtureMixin, TestCase):
    URL = "/api/v3/pdf/bookings/export/pdf/"
    TASK = "pdf_service.api.v3.views.export_booking_list.generate_booking_list_pdf_task"

    def post(self, body):
        return self.client.post(self.URL, body, format="json")

    def test_creates_task_and_reuses_identical_request(self):
        from pdf_service.models import PDFTask

        with mock.patch(self.TASK) as task:
            first = self.post({"platform": "desktop", "status": "all"})
            second = self.post({"platform": "desktop", "status": "all"})
        self.assertEqual(first.status_code, 200, first.content)
        data = first.json()["data"]
        self.assertEqual((data["status"], data["platform"], data["estimated_bookings"]), ("pending", "desktop", 7))
        task.delay.assert_called_once()
        self.assertTrue(second.json()["data"]["reused"])
        self.assertEqual(second.json()["data"]["task_id"], data["task_id"])
        self.assertEqual(PDFTask.objects.filter(pdf_type="booking_list").count(), 1)
        stored = PDFTask.objects.get(task_id=data["task_id"]).request_params
        self.assertEqual(stored["today"], localdate().isoformat())

    def test_validation_errors(self):
        with mock.patch(self.TASK) as task:
            self.assertEqual(self.post({"platform": "tablet"}).json()["message"], MSG_PLATFORM)
            response = self.post({"platform": "desktop", "status": "future"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["message"], "Invalid status filter.")
        task.delay.assert_not_called()

    def test_row_limit(self):
        with mock.patch(self.TASK) as task, self.settings(BOOKING_LIST_PDF_MAX_ROWS=3):
            response = self.post({"platform": "mobile", "status": "all"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("maximum 3", response.json()["message"])
        task.delay.assert_not_called()

    def test_queue_unavailable_marks_task_failed(self):
        from pdf_service.models import PDFTask

        with mock.patch(self.TASK) as task:
            task.delay.side_effect = ConnectionError("broker down")
            response = self.post({"platform": "mobile"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(PDFTask.objects.get(pdf_type="booking_list").status, "failed")
