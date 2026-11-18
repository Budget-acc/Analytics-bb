"""Shared setup for the database-backed invoice template API tests (PostgreSQL)."""
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import User
from invoice_templates.models import InvoiceTemplate
from shop.models import Shop, ShopUser
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

API = "/api/v3/invoice-templates/"


class InvoiceTemplateAPITestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.shop_a = Shop.objects.create(name="Shop A", place="Kochi", terms_and_conditions=["General A"])
        cls.shop_b = Shop.objects.create(name="Shop B", place="Calicut")
        cls.owner_a = User.objects.create_user(phone="+919000000001", password="x", role="OWNER")
        cls.owner_b = User.objects.create_user(phone="+919000000002", password="x", role="OWNER")
        cls.staff_a = User.objects.create_user(phone="+919000000003", password="x", role="STAFF")
        ShopUser.objects.create(shop=cls.shop_a, user=cls.owner_a, role="OWNER")
        ShopUser.objects.create(shop=cls.shop_b, user=cls.owner_b, role="OWNER")
        ShopUser.objects.create(shop=cls.shop_a, user=cls.staff_a, role="STAFF")
        cls.standard = InvoiceTemplate.objects.get(code="standard")
        cls.classic = InvoiceTemplate.objects.get(code="classic")

    def setUp(self):
        # Subscription state is covered by the subscriptions app; assume active here.
        patcher = mock.patch.object(SubscriptionActivePermission, "has_permission", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.as_user(self.owner_a, self.shop_a)

    def as_user(self, user, shop):
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_ACTIVE_SHOP_ID=str(shop.id))

    def get(self, path):
        return self.client.get(API + path)

    def put(self, path, data=None):
        return self.client.put(API + path, data or {}, format="json")

    def post(self, path, data=None):
        return self.client.post(API + path, data or {}, format="json")

    def patch(self, path, data=None):
        return self.client.patch(API + path, data or {}, format="json")

    def delete(self, path):
        return self.client.delete(API + path)

    def classic_defaults(self):
        from invoice_templates.v3.registry import REGISTRY

        return REGISTRY["classic"].defaults

    def create_invoice(self, template_code="classic", name=None, configuration=None):
        """POST custom-invoices/ and return the response."""
        payload = {"template_code": template_code}
        if name is not None:
            payload["name"] = name
        if configuration is not None:
            payload["configuration"] = configuration
        return self.post("custom-invoices/", payload)

    def create_invoice_id(self, **kwargs):
        response = self.create_invoice(**kwargs)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]["id"]
