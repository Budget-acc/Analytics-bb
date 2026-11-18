"""
Debug script to check why WhatsApp notifications are not being sent.
"""
import os
import django
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ERP.settings')
django.setup()

from django.utils import timezone
from django.db.models import Q
from booking_v2.models import BookingV2
from subscriptions.models import has_feature_access, ShopSubscription
from shop.models import ShopUser

def debug_pickup_ready():
    """Debug pickup ready notifications"""
    today = timezone.now().date()
    print(f"\n{'='*60}")
    print(f"DEBUG: Pickup Ready Notifications for {today}")
    print(f"{'='*60}\n")
    
    # Check all bookings for today's pickup
    all_bookings = BookingV2.objects.filter(pickup_date__date=today)
    print(f"📅 Total bookings with pickup_date = {today}: {all_bookings.count()}")
    
    if all_bookings.count() == 0:
        print("   ❌ No bookings found for today's pickup date")
        print("   💡 Create a booking with today's pickup date to test\n")
        return
    
    # Check delivery status filter
    status_filtered = all_bookings.filter(delivery_status__in=['booked', 'ready to deliver'])
    print(f"📋 Bookings with status 'booked' or 'ready to deliver': {status_filtered.count()}")
    
    if status_filtered.count() == 0:
        print("   ❌ No bookings with correct delivery status")
        print(f"   Current statuses: {list(all_bookings.values_list('delivery_status', flat=True))}")
        print("   💡 Update booking delivery_status to 'booked' or 'ready to deliver'\n")
        return
    
    # Check same-day exclusion
    not_same_day = status_filtered.exclude(Q(return_date__date=today))
    print(f"📅 Bookings NOT same-day (pickup != return): {not_same_day.count()}")
    
    if not_same_day.count() == 0:
        print("   ⚠️  All bookings are same-day (pickup and return on same day)")
        print("   These are intentionally excluded from pickup notifications\n")
        return
    
    print(f"\n{'─'*60}")
    print("Checking individual bookings:")
    print(f"{'─'*60}\n")
    
    for booking in not_same_day:
        print(f"Booking #{booking.id}:")
        print(f"  Shop: {booking.shop.name} (ID: {booking.shop.id})")
        print(f"  Pickup: {booking.pickup_date.date()}")
        print(f"  Return: {booking.return_date.date() if booking.return_date else 'N/A'}")
        print(f"  Status: {booking.delivery_status}")
        
        # Check client
        if not booking.client_identity:
            print(f"  ❌ No client_identity")
            continue
        
        if not booking.client_identity.client_phone_1:
            print(f"  ❌ Client has no phone number")
            continue
        
        print(f"  ✓ Client: {booking.client_identity.client_name}")
        print(f"  ✓ Phone: {booking.client_identity.client_phone_1}")
        
        # Check subscription
        if not hasattr(booking.shop, 'subscription'):
            print(f"  ❌ Shop has no subscription")
            continue
        
        subscription = booking.shop.subscription
        print(f"  Subscription: {subscription.status}")
        
        if not subscription.is_active():
            print(f"  ❌ Subscription is not active")
            continue
        
        print(f"  ✓ Subscription is active")
        
        # Check feature access
        shop_users = ShopUser.objects.filter(shop_id=booking.shop.id, is_active=True)
        has_feature = False
        
        for shop_user in shop_users:
            if has_feature_access(shop_user, "WHATSAPP_MESSAGE"):
                has_feature = True
                print(f"  ✓ User {shop_user.user.full_name} has WHATSAPP_MESSAGE feature")
                break
        
        if not has_feature:
            print(f"  ❌ No shop user has WHATSAPP_MESSAGE feature")
            print(f"  💡 Grant WHATSAPP_MESSAGE feature to shop subscription")
            continue
        
        # Check shop phone
        if not booking.shop.phone:
            print(f"  ❌ Shop has no phone number")
            continue
        
        print(f"  ✓ Shop phone: {booking.shop.phone}")
        print(f"  ✅ This booking SHOULD send notification\n")


def debug_return_reminder():
    """Debug return reminder notifications"""
    today = timezone.now().date()
    print(f"\n{'='*60}")
    print(f"DEBUG: Return Reminder Notifications for {today}")
    print(f"{'='*60}\n")
    
    # Check all bookings for today's return
    all_bookings = BookingV2.objects.filter(return_date__date=today)
    print(f"📅 Total bookings with return_date = {today}: {all_bookings.count()}")
    
    if all_bookings.count() == 0:
        print("   ❌ No bookings found for today's return date")
        print("   💡 Create a booking with today's return date to test\n")
        return
    
    # Check delivery status filter (includes multiple statuses for shops that forget to update)
    status_filtered = all_bookings.filter(delivery_status__in=['booked', 'ready to deliver', 'delivered'])
    print(f"📋 Bookings with status 'booked', 'ready to deliver', or 'delivered': {status_filtered.count()}")
    
    if status_filtered.count() == 0:
        print("   ❌ No bookings with eligible delivery status")
        print(f"   Current statuses: {list(all_bookings.values_list('delivery_status', flat=True))}")
        print("   💡 Update booking delivery_status to 'booked', 'ready to deliver', or 'delivered'\n")
        return
    
    # Check same-day exclusion
    not_same_day = status_filtered.exclude(Q(pickup_date__date=today))
    print(f"📅 Bookings NOT same-day (pickup != return): {not_same_day.count()}")
    
    if not_same_day.count() == 0:
        print("   ⚠️  All bookings are same-day (pickup and return on same day)")
        print("   These are intentionally excluded from return notifications\n")
        return
    
    print(f"\n{'─'*60}")
    print("Similar checks apply as pickup ready...")
    print(f"{'─'*60}\n")


def check_subscriptions():
    """Check shop subscriptions and features"""
    print(f"\n{'='*60}")
    print("SHOP SUBSCRIPTIONS & FEATURES")
    print(f"{'='*60}\n")
    
    subscriptions = ShopSubscription.objects.select_related('shop').all()
    
    if subscriptions.count() == 0:
        print("❌ No shop subscriptions found")
        print("💡 Create subscriptions for shops\n")
        return
    
    for sub in subscriptions:
        print(f"Shop: {sub.shop.name} (ID: {sub.shop.id})")
        print(f"  Status: {sub.status}")
        print(f"  Active: {sub.is_active()}")
        print(f"  Start: {sub.start_date}")
        print(f"  End: {sub.end_date}")
        
        # Check WHATSAPP_MESSAGE feature
        feature_codes = sub.get_feature_codes()
        has_whatsapp = "WHATSAPP_MESSAGE" in feature_codes
        
        if has_whatsapp:
            print(f"  ✅ Has WHATSAPP_MESSAGE feature")
        else:
            print(f"  ❌ Missing WHATSAPP_MESSAGE feature")
            print(f"  Current features: {', '.join(feature_codes) if feature_codes else 'None'}")
        
        print()


if __name__ == "__main__":
    print("\n🔍 WhatsApp Notification Debug Tool")
    
    # Check subscriptions first
    check_subscriptions()
    
    # Check pickup ready
    debug_pickup_ready()
    
    # Check return reminder
    debug_return_reminder()
    
    print(f"{'='*60}")
    print("✅ Debug Complete")
    print(f"{'='*60}\n")
