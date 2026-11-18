# LOCAL TEST ONLY - removes all archive test data of shop 1 so you can seed again.
from django.db import transaction
from safedelete.models import HARD_DELETE

from booking_v2.models import BookingV2, Client
from expenses_v2.models import Expenses
from finance.models import DailyBalance, LedgerEntry
from sales.models import Sale

SHOP_ID = 1
MARK = "ARCHIVE-TEST"

with transaction.atomic():
    BookingV2.all_objects.filter(shop_id=SHOP_ID, staff_name=MARK).delete(force_policy=HARD_DELETE)
    Sale.objects.filter(shop_id=SHOP_ID, description=MARK).delete()
    Expenses.all_objects.filter(shop_id=SHOP_ID, description=MARK).delete(force_policy=HARD_DELETE)
    Client.all_objects.filter(shop_id=SHOP_ID, client_name__startswith="Test Client").delete(force_policy=HARD_DELETE)
    LedgerEntry.all_objects.filter(shop_id=SHOP_ID).delete(force_policy=HARD_DELETE)
    DailyBalance.all_objects.filter(shop_id=SHOP_ID).delete(force_policy=HARD_DELETE)

print("Test data removed for shop", SHOP_ID)