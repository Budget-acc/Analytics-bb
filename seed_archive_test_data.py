# LOCAL TEST ONLY - creates sample data for the archive job.
# Run:  python manage.py shell -c "exec(open('seed_archive_test_data.py').read())"

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from authentication.models import User
from booking_v2.models import BookingV2, Client, PaymentDetailsV2
from expenses_v2.models import Expenses
from finance.api.v3.utils import create_ledger_entries
from finance.models import Account
from sales.models import Sale, SalesPaymentDetails
from shop.models import Shop, ShopUser

SHOP_ID = 1
MARK = "ARCHIVE-TEST"
now = timezone.now()


def days_ago(n, hour=11):
    return (now - timedelta(days=n)).replace(hour=hour, minute=0, second=0, microsecond=0)


shop = Shop.objects.get(id=SHOP_ID)

if BookingV2.all_objects.filter(shop=shop, staff_name=MARK).exists():
    raise SystemExit("Test data already exists for this shop. Nothing created.")

# A user for the bookings / expenses
shop_user = ShopUser.objects.filter(shop=shop).select_related("user").first()
user = shop_user.user if shop_user else User.objects.filter(is_superuser=True).first() or User.objects.first()
if user is None:
    raise SystemExit("No user found. Create a user / superuser first.")

with transaction.atomic():
    # ---------- accounts ----------
    cash, _ = Account.objects.get_or_create(
        shop=shop, name="Cash in Hand",
        defaults={"account_type": "cash", "is_default": True, "is_active": True},
    )
    bank, _ = Account.objects.get_or_create(
        shop=shop, name="Default Bank Account",
        defaults={"account_type": "bank", "is_default": False, "is_active": True},
    )

    # ---------- clients ----------
    c1 = Client.objects.create(shop=shop, client_name="Test Client One", client_phone_1=9000000001)
    c2 = Client.objects.create(shop=shop, client_name="Test Client Two", client_phone_1=9000000002)

    def add_booking(client, total, status, pickup_days_ago, completed_days_ago=None, pays=()):
        booking = BookingV2.objects.create(
            user=user, shop=shop, client_identity=client,
            total_amount=total, advance_amount=sum(a for a, _, _ in pays),
            pickup_date=days_ago(pickup_days_ago), return_date=days_ago(pickup_days_ago - 1),
            purchase_mode="normal", booking_status=status,
            delivery_status="returned" if status == "completed" else "booked",
            payment_status=status == "completed",
            completed_at=days_ago(completed_days_ago, 18) if completed_days_ago is not None else None,
            staff_name=MARK, created_at=days_ago(pickup_days_ago + 2),
        )
        for amount, method, paid_days_ago in pays:
            account = cash if method == "cash" else bank
            payment = PaymentDetailsV2.objects.create(
                booking=booking, payment_amount=amount, payment_method=method,
                account=account, created_at=days_ago(paid_days_ago, 12),
            )
            create_ledger_entries(
                payments=[{"amount": amount, "direction": "in", "account": account,
                           "payment_method": method, "reference_id": payment.id,
                           "created_at": payment.created_at}],
                shop=shop, transaction_type="booking_payment", reference_type="PaymentDetailsV2",
            )
        return booking

    # SHOULD MOVE: completed 3-5 days ago
    add_booking(c1, 3000, "completed", 7, completed_days_ago=5, pays=[(1000, "cash", 7), (2000, "upi", 5)])
    add_booking(c2, 2500, "completed", 6, completed_days_ago=4, pays=[(2500, "cash", 6)])
    add_booking(c1, 4000, "completed", 5, completed_days_ago=3, pays=[(4000, "upi", 5)])
    # SHOULD STAY: completed yesterday, and an upcoming booking
    add_booking(c2, 1500, "completed", 2, completed_days_ago=1, pays=[(1500, "cash", 1)])
    add_booking(c1, 5000, "upcoming", -3, pays=[(1000, "cash", 0)])

    def add_sale(total, discount, sale_days_ago, pays):
        sale = Sale.objects.create(
            shop=shop, sale_date=days_ago(sale_days_ago).date(), total_amount=total,
            discount=discount, paid_amount=total - discount, description=MARK,
            created_at=days_ago(sale_days_ago),
        )
        for amount, method, paid_days_ago in pays:
            account = cash if method == "cash" else bank
            payment = SalesPaymentDetails.objects.create(
                sale=sale, payment_amount=amount, payment_method=method,
                account=account, created_at=days_ago(paid_days_ago, 13),
            )
            create_ledger_entries(
                payments=[{"amount": amount, "direction": "in", "account": account,
                           "payment_method": method, "reference_id": payment.id,
                           "created_at": payment.created_at}],
                shop=shop, transaction_type="sale_payment", reference_type="SalesPaymentDetails",
            )
        return sale

    # SHOULD MOVE: fully paid, old
    add_sale(1200, 200, 5, [(1000, "cash", 5)])
    add_sale(800, 0, 4, [(500, "upi", 4), (300, "cash", 3)])
    # SHOULD STAY: not fully paid / last payment yesterday
    add_sale(2000, 0, 5, [(500, "cash", 5)])
    add_sale(600, 0, 5, [(300, "cash", 5), (300, "cash", 1)])

    def add_expense(amount, method, exp_days_ago, label):
        account = cash if method == "cash" else bank
        when = days_ago(exp_days_ago, 15)
        expense = Expenses.objects.create(
            shop=shop, user=user, date=when.date(), expense_date_time=when,
            expense=amount, type=label, description=MARK,
            payment_method=method, account=account,
        )
        create_ledger_entries(
            payments=[{"amount": amount, "direction": "out", "account": account,
                       "payment_method": method, "reference_id": expense.id,
                       "created_at": when}],
            shop=shop, transaction_type="expense", reference_type="Expenses",
        )
        return expense

    # SHOULD MOVE
    add_expense(300, "cash", 5, "Electricity")
    add_expense(150, "upi", 3, "Transport")
    # SHOULD STAY
    add_expense(100, "cash", 0, "Tea")

print("Test data created for shop", SHOP_ID)
print("Expected to MOVE : 3 bookings, 2 sales, 2 expenses (+ their payments, ledger rows, old daily balance rows)")
print("Expected to STAY : 2 bookings, 2 sales, 1 expense")