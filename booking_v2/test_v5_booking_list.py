"""Database-backed tests for the v5 mobile booking list with tab counts
(require PostgreSQL - the project's migrations do not run on SQLite)."""
import sys
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import User
from booking_v2.models import BookedProductVariant, BookingV2, Client
from shop.models import Shop, ShopUser
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

LIST_API = "/api/v5/bookings/bookings/"
TODAY = date(2026, 10, 15)
list_module = sys.modules["booking_v2.api.v5.views.booking_viewset.list"]

MOBILE_FIELDS = {
    "id", "shop_booking_id", "client", "pickup_date", "return_date", "cooling_period_end",
    "booking_status", "payment_status", "staff_name", "staff_color", "delivery_status",
    "created_at", "booked_items",
}


def at(days):
    return timezone.make_aware(datetime.combine(TODAY + timedelta(days=days), time(12, 0)))


class BookingListV5Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.shop = Shop.objects.create(name="Shop A", place="Kochi")
        cls.other_shop = Shop.objects.create(name="Shop B", place="Calicut")
        cls.owner = User.objects.create_user(phone="+919000000201", password="x", role="OWNER", full_name="Owner A")
        ShopUser.objects.create(shop=cls.shop, user=cls.owner, role="OWNER")
        cls.anjali = Client.objects.create(shop=cls.shop, client_name="Anjali", phone_1_e164="+919812345678")
        cls.bala = Client.objects.create(shop=cls.shop, client_name="Bala", phone_1_e164="+919898989898")

        def booking(code, pickup, ret, shop=None, client=None, **extra):
            return BookingV2.objects.create(
                user=cls.owner, shop=shop or cls.shop, client_identity=client or cls.anjali,
                shop_booking_id=code, total_amount=1000, advance_amount=200, purchase_mode="normal",
                pickup_date=at(pickup), return_date=None if ret is None else at(ret), **extra,
            )

        cls.future = booking("BK001", 3, 5)
        cls.today_pickup = booking("BK002", 0, 2, purchase_mode="courier")
        cls.ongoing = booking("BK003", -2, 1, client=cls.bala, delivery_status="delivered")
        cls.overdue = booking("BK004", -5, -2, delivery_status="delivered")
        cls.returned_late = booking("BK005", -5, -2, delivery_status="returned")
        cls.no_return = booking("BK006", -1, None)
        cls.completed = booking(
            "BK007", -10, -8, booking_status="completed", delivery_status="returned", completed_at=at(-8),
        )
        cls.cancelled = booking("BK008", -5, -3, delivery_status="cancelled")
        booking("BK009", 3, 5, shop=cls.other_shop)
        booking("BK010", 3, 5).delete()  # soft delete

        for name in ("Wedding Gown", "Bridal Veil"):
            BookedProductVariant.objects.create(
                booking=cls.future, user=cls.owner, shop=cls.shop, amount=500, product_name=name,
            )

    def setUp(self):
        for patcher in (
            mock.patch.object(SubscriptionActivePermission, "has_permission", return_value=True),
            mock.patch.object(list_module, "localdate", return_value=TODAY),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.client.credentials(HTTP_X_ACTIVE_SHOP_ID=str(self.shop.id))

    def fetch(self, **params):
        response = self.client.get(LIST_API, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def ids(self, **params):
        return [item["id"] for item in self.fetch(**params)["data"]]

    def assert_invalid(self, msg, **params):
        response = self.client.get(LIST_API, params)
        self.assertEqual(response.status_code, 400, response.content)
        body = response.json()
        self.assertEqual((body["message"], body["dev_message"]), (msg.message, msg.dev_message))

    def test_url_resolves_to_list_action(self):
        self.assertEqual(resolve(LIST_API).func.actions, {"get": "list", "post": "create"})
        self.assertEqual(resolve(f"{LIST_API}desktop-list/").func.actions, {"get": "desktop_list"})

    def test_each_tab_lists_its_bookings_in_desktop_order(self):
        expected = {
            "upcoming": [self.today_pickup, self.future],
            "returns": [self.ongoing, self.today_pickup],
            "pending": [self.overdue, self.returned_late, self.ongoing, self.no_return],
            "not_returned": [self.overdue],
            "expired": [self.overdue, self.returned_late, self.cancelled],
            "completed": [self.completed],
            "cancelled": [self.cancelled],
        }
        for tab, bookings in expected.items():
            with self.subTest(tab=tab):
                self.assertEqual(self.ids(status=tab), [booking.id for booking in bookings])

    def test_all_lists_every_shop_booking_newest_first(self):
        shop_bookings = BookingV2.objects.filter(shop=self.shop).order_by("-created_at", "-id")
        self.assertEqual(self.ids(status="ALL"), [booking.id for booking in shop_bookings])
        self.assertEqual(len(self.ids(status="all")), 8)

    def test_status_counts_match_tab_lists_and_ignore_filters(self):
        counts = self.fetch()["status_counts"]
        self.assertEqual(counts, {
            "upcoming": 2, "returns": 2, "pending": 4, "not_returned": 1,
            "expired": 3, "completed": 1, "cancelled": 1, "all": 8,
        })
        for tab, count in counts.items():
            with self.subTest(tab=tab):
                self.assertEqual(self.fetch(status=tab)["count"], count)
        filtered = self.fetch(status="upcoming", search="Bala", purchase_mode="courier", start_date="15-10-2026")
        self.assertEqual(filtered["status_counts"], counts)

    def test_items_use_mobile_list_shape(self):
        item = next(i for i in self.fetch(status="upcoming")["data"] if i["id"] == self.future.id)
        self.assertEqual(set(item), MOBILE_FIELDS)
        self.assertEqual(item["booked_items"], ["Wedding Gown", "Bridal Veil"])
        self.assertEqual((item["client"], item["shop_booking_id"]), ("Anjali", "BK001"))

    def test_filters(self):
        self.assertEqual(self.ids(status="pending", search="bala"), [self.ongoing.id])
        self.assertEqual(self.ids(purchase_mode="Courier"), [self.today_pickup.id])
        self.assertEqual(self.ids(start_date="2026-10-15", end_date="18-10-2026"), [self.today_pickup.id, self.future.id])
        self.assertEqual(self.ids(status="upcoming", end_date="14-10-2026"), [])

    def test_search_ranks_booking_id_before_client_name(self):
        self.assertEqual(self.ids(search="BK004")[0], self.overdue.id)
        self.assertEqual(self.ids(search="98123"), [booking.id for booking in BookingV2.objects.filter(
            shop=self.shop, client_identity=self.anjali).order_by("-id")])

    def test_validation_errors(self):
        self.assert_invalid(list_module.INVALID_STATUS, status="future")
        self.assert_invalid(list_module.INVALID_PURCHASE_MODE, purchase_mode="air")
        self.assert_invalid(list_module.INVALID_START_DATE, start_date="2026/10/01")
        self.assert_invalid(list_module.INVALID_END_DATE, end_date="31-31-2026")
        self.assert_invalid(list_module.INVALID_DATE_RANGE, start_date="20-10-2026", end_date="2026-10-01")
        self.assert_invalid(list_module.SEARCH_TOO_LONG, search="x" * 101)

    def test_page_past_the_end_is_404(self):
        self.assertEqual(self.client.get(LIST_API, {"page": 99}).status_code, 404)
        page = self.fetch(status="all", page_size=3, page=2)
        self.assertEqual((page["count"], page["total_pages"], len(page["data"])), (8, 3, 3))

    def test_requires_shop_header(self):
        self.client.credentials()
        self.assertEqual(self.client.get(LIST_API).status_code, 403)

    def test_query_count_is_constant(self):
        def queries(**params):
            with CaptureQueriesContext(connection) as ctx:
                self.fetch(**params)
            return len(ctx.captured_queries)

        self.assertEqual(queries(status="cancelled"), queries(status="all"))
