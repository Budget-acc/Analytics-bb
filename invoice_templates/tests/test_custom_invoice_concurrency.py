"""
Race test for the per-shop creation limit (requires PostgreSQL: real
transactions + SELECT ... FOR UPDATE across threads).
"""
import threading

from django.db import connection
from django.test import TransactionTestCase, skipUnlessDBFeature

from invoice_templates.models import InvoiceTemplate, ShopCustomInvoice
from invoice_templates.v3.registry import get_definition
from invoice_templates.v3.services.catalog import sync_catalog
from invoice_templates.v3.services.custom_invoices import LimitReached, create_custom_invoice
from shop.models import Shop


@skipUnlessDBFeature("has_select_for_update")
class CreationLimitRaceTests(TransactionTestCase):
    def test_parallel_creates_never_exceed_the_limit(self):
        sync_catalog()
        shop = Shop.objects.create(name="Race shop", place="Kochi")
        template = InvoiceTemplate.objects.get(code="classic")
        definition = get_definition("classic")
        for i in range(3):
            create_custom_invoice(shop.id, template, definition, {}, None, name=f"Existing {i}")

        results, barrier = [], threading.Barrier(4)

        def attempt(n):
            try:
                barrier.wait()
                create_custom_invoice(shop.id, template, definition, {}, None, name=f"Racer {n}")
                results.append("created")
            except LimitReached:
                results.append("limit")
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["created", "limit", "limit", "limit"])
        self.assertEqual(ShopCustomInvoice.objects.filter(shop=shop).count(), 4)
