import json
import logging
import firebase_admin
from firebase_admin import credentials, messaging, initialize_app
from django.conf import settings
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

logger = logging.getLogger(__name__)

FIREBASE_APP = None


def get_firebase_app():
    global FIREBASE_APP
    if FIREBASE_APP:
        logger.debug("Reusing existing Firebase app instance")
        return FIREBASE_APP

    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
    if not service_account_path or not os.path.exists(service_account_path):
        logger.error("FIREBASE_SERVICE_ACCOUNT_PATH is not set or file does not exist")
        raise ValueError("FIREBASE_SERVICE_ACCOUNT_PATH must be set to the service account JSON file path")

    try:
        logger.info(f"Initializing Firebase app using credentials at {service_account_path}")
        cred = credentials.Certificate(service_account_path)
        FIREBASE_APP = initialize_app(cred)
        logger.info("Firebase app initialized successfully")
        return FIREBASE_APP
    except Exception as e:
        logger.exception("Failed to initialize Firebase app")
        raise

def send_multicast(tokens, title, body, data=None, image=None):
    if not tokens:
        logger.warning("No tokens provided for notification")
        return None
        
    try:
        app = get_firebase_app()
        notification_kwargs = {"title": title, "body": body}
        if image:
            notification_kwargs["image"] = image
                
        message = messaging.MulticastMessage(
            notification=messaging.Notification(**notification_kwargs),
            data=data or {},
            tokens=tokens
        )
        
        response = messaging.send_multicast(message, app=app)
        
        # Log results
        logger.info(f"Notification sent. Success: {response.success_count}, Failed: {response.failure_count}")
        
        # Handle failed tokens
        if response.failure_count > 0:
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    logger.error(f"Failed to send to token {tokens[idx]}: {resp.exception}")
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to send multicast notification: {e}")
        return None


def _send_one_token(token, notification_kwargs, data, app):
    """Helper: send to a single token, return a tuple describing the result."""
    try:
        msg = messaging.Message(
            notification=messaging.Notification(**notification_kwargs),
            data=data,
            token=token,
            # Add Android and APNS specific configurations for better delivery
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    title=notification_kwargs['title'],
                    body=notification_kwargs['body'],
                    channel_id='default',  # Make sure this matches your app's channel
                    priority='high'
                )
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(
                            title=notification_kwargs['title'],
                            body=notification_kwargs['body']
                        ),
                        badge=1,
                        sound='default'
                    )
                )
            )
        )
        # NOTE: this uses HTTP v1 and does NOT call any /batch endpoint
        resp = messaging.send(msg, app=app)
        return {"token": token, "ok": True, "resp": resp, "error": None, "code": None}
    except Exception as e:
        code = getattr(e, "code", None)  # FCM error code when available
        return {"token": token, "ok": False, "resp": None, "error": e, "code": code}


def send_to_tokens(tokens, title, body, data=None, image=None, max_workers=16):
    """
    Replacement for send_multicast: sends one message per token (concurrently).
    Adds verbose logging of every step and returns a detailed summary.
    """
    logger.info("Step 1: Preparing to send notifications")
    if not tokens:
        logger.warning("Step 1: No tokens provided")
        return None

    # basic token hygiene
    valid_tokens = [t.strip() for t in tokens if isinstance(t, str) and t.strip()]
    if not valid_tokens:
        logger.error("Step 1: No valid tokens after filtering")
        return None

    logger.info(f"Step 2: Found {len(valid_tokens)} valid tokens (input had {len(tokens)})")

    app = get_firebase_app()

    # Build notification
    notification_kwargs = {"title": title, "body": body}
    if image:
        notification_kwargs["image"] = image
    logger.debug(f"Step 3: Notification payload = {notification_kwargs}")

    # FCM data must be string:string
    formatted_data = {}
    if data:
        for k, v in data.items():
            formatted_data[str(k)] = "" if v is None else str(v)
    logger.debug(f"Step 4: Data payload (stringified) = {formatted_data}")

    # Send concurrently
    max_workers = max(1, min(max_workers, 32))
    logger.info(f"Step 5: Sending one-by-one with concurrency (max_workers={max_workers})")

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_send_one_token, t, notification_kwargs, formatted_data, app)
                   for t in valid_tokens]
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if r["ok"]:
                logger.debug(f"Step 6: ✓ Sent to token={r['token'][:20]}... resp={r['resp']}")
            else:
                logger.error(f"Step 6: ✗ Failed token={r['token'][:20]}... code={r['code']} err={r['error']}")

    # Summarize
    success = sum(1 for r in results if r["ok"])
    fail = len(results) - success
    invalid_tokens = [r["token"] for r in results if (not r["ok"] and r["code"] in {"UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"})]
    failed_tokens = [r["token"] for r in results if (not r["ok"] and r["token"] not in invalid_tokens)]

    logger.info(f"Step 7: Done. Success: {success}, Failed: {fail}, Invalid: {len(invalid_tokens)}")

    return {
        "success_count": success,
        "failure_count": fail,
        "invalid_tokens": invalid_tokens,
        "failed_tokens": failed_tokens,
        "raw_results": results,
    }


def send_targeted_notification(title, body, target, data=None, image=None, user_id=None, shop_id=None):
    """
    Updated targeted routing that properly filters DeviceTokens by shop relationship.
    
    - target="all": Send to all active device tokens
    - target="shop": Send to all active device tokens associated with a specific shop
    - target="user": Send to all active device tokens for a specific user (optionally filtered by shop)
    """
    logger.info(f"Route 1: target={target}, user_id={user_id}, shop_id={shop_id}")

    from notifications.models import DeviceToken  # avoid circular imports

    # Start with base queryset of active device tokens
    tokens_qs = DeviceToken.objects.filter(is_active=True)

    if target == "all":
        logger.info("Route 2: Sending to ALL active device tokens")
        # Send to all active tokens regardless of shop
        
    elif target == "shop" and shop_id:
        logger.info(f"Route 2: Filtering device tokens for shop_id={shop_id}")
        # Filter device tokens that are directly associated with the specific shop
        tokens_qs = tokens_qs.filter(shop_id=shop_id)
        # Add shop_id to data payload
        data = {**(data or {}), "shop_id": str(shop_id)}
        
    elif target == "user" and user_id:
        logger.info(f"Route 2: Filtering device tokens for user_id={user_id}")
        # Filter device tokens for specific user
        tokens_qs = tokens_qs.filter(user_id=user_id)
        
        # If shop_id is also provided, further filter by shop
        if shop_id:
            logger.info(f"Route 2a: Further filtering by shop_id={shop_id} for user")
            tokens_qs = tokens_qs.filter(shop_id=shop_id)
            data = {**(data or {}), "shop_id": str(shop_id)}
            
        data = {**(data or {}), "user_id": str(user_id)}
        
    else:
        logger.warning(f"Route 2: Invalid target configuration: target={target}, user_id={user_id}, shop_id={shop_id}")
        return None

    # Extract the actual tokens
    tokens = list(tokens_qs.values_list("token", flat=True))
    logger.info(f"Route 3: Collected {len(tokens)} device tokens for target={target}")

    if not tokens:
        logger.warning("Route 3: No active device tokens found for this target configuration")
        return None

    # Send notifications using the per-token sender
    result = send_to_tokens(tokens, title, body, data=data, image=image)

    # Deactivate invalid tokens to maintain token hygiene
    if result and result.get("invalid_tokens"):
        invalid_count = DeviceToken.objects.filter(
            token__in=result["invalid_tokens"]
        ).update(is_active=False)
        logger.info(f"Route 4: Deactivated {invalid_count} invalid tokens")

    return result


def send_shop_notification(shop_id, title, body, data=None, image=None):
    """
    Convenience function to send notifications to all device tokens associated with a shop.
    This sends to all users who have active device tokens registered for this shop.
    """
    logger.info(f"Sending shop notification to shop_id={shop_id}")
    return send_targeted_notification(
        title=title,
        body=body,
        target="shop",
        shop_id=shop_id,
        data=data,
        image=image
    )


def send_user_notification(user_id, title, body, data=None, image=None, shop_id=None):
    """
    Convenience function to send notifications to a specific user's device tokens.
    If shop_id is provided, only sends to tokens registered for that shop.
    """
    logger.info(f"Sending user notification to user_id={user_id}, shop_id={shop_id}")
    return send_targeted_notification(
        title=title,
        body=body,
        target="user",
        user_id=user_id,
        shop_id=shop_id,
        data=data,
        image=image
    )


def send_user_current_shop_notification(user_id, title, body, data=None, image=None):
    """
    Send notification to user's most recently updated device token (representing current shop).
    This assumes the most recent token update represents the user's currently active shop.
    """
    logger.info(f"Sending notification to user_id={user_id}'s current shop")
    
    from notifications.models import DeviceToken
    
    # Get user's most recent active device token (representing current shop)
    latest_token = DeviceToken.objects.filter(
        user_id=user_id, 
        is_active=True
    ).order_by('-updated_at').first()
    
    if not latest_token:
        logger.warning(f"No active device tokens found for user_id={user_id}")
        return None
    
    logger.info(f"Found current shop_id={latest_token.shop_id} for user_id={user_id}")
    
    return send_user_notification(
        user_id=user_id,
        shop_id=latest_token.shop_id,
        title=title,
        body=body,
        data=data,
        image=image
    )


def send_all_notifications(title, body, data=None, image=None):
    """
    Convenience function to send notifications to all active device tokens.
    Use with caution - this sends to every active token in the system.
    """
    logger.info("Sending notification to all active device tokens")
    return send_targeted_notification(
        title=title,
        body=body,
        target="all",
        data=data,
        image=image
    )


def get_shop_token_count(shop_id):
    """
    Utility function to get the count of active device tokens for a shop.
    Useful for checking if a shop has any active users before sending notifications.
    """
    from notifications.models import DeviceToken
    
    count = DeviceToken.objects.filter(shop_id=shop_id, is_active=True).count()
    logger.debug(f"Shop {shop_id} has {count} active device tokens")
    return count


def get_user_token_count(user_id, shop_id=None):
    """
    Utility function to get the count of active device tokens for a user.
    Optionally filtered by shop.
    """
    from notifications.models import DeviceToken
    
    tokens_qs = DeviceToken.objects.filter(user_id=user_id, is_active=True)
    if shop_id:
        tokens_qs = tokens_qs.filter(shop_id=shop_id)
    
    count = tokens_qs.count()
    logger.debug(f"User {user_id} has {count} active device tokens" + 
                (f" for shop {shop_id}" if shop_id else ""))
    return count


def debug_device_tokens(shop_id=None, user_id=None):
    """
    Debug function to check device tokens and their validity.
    Use this to troubleshoot notification delivery issues.
    """
    from notifications.models import DeviceToken
    
    tokens_qs = DeviceToken.objects.filter(is_active=True)
    
    if shop_id:
        tokens_qs = tokens_qs.filter(shop_id=shop_id)
        logger.info(f"Debugging device tokens for shop_id={shop_id}")
    
    if user_id:
        tokens_qs = tokens_qs.filter(user_id=user_id)
        logger.info(f"Debugging device tokens for user_id={user_id}")
    
    tokens = tokens_qs.select_related('user', 'shop')
    
    logger.info(f"Found {tokens.count()} active device tokens")
    
    for token in tokens:
        logger.info(f"Token: {token.token[:20]}... | User: {token.user.full_name or token.user.phone} | Shop: {token.shop.name if token.shop else 'None'} | Platform: {token.platform} | Updated: {token.updated_at}")
    
    return tokens


def test_single_notification(token, title="Test", body="Test message"):
    """
    Test function to send a notification to a single token for debugging.
    """
    try:
        app = get_firebase_app()
        
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={"test": "true"},
            token=token,
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    title=title,
                    body=body,
                    channel_id='default',
                    priority='high'
                )
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(title=title, body=body),
                        badge=1,
                        sound='default'
                    )
                )
            )
        )
        
        resp = messaging.send(msg, app=app)
        logger.info(f"Test notification sent successfully. Response: {resp}")
        return {"success": True, "response": resp}
        
    except Exception as e:
        logger.error(f"Test notification failed: {e}")
        return {"success": False, "error": str(e)}


# Add this debugging function to your tasks.py or views.py for testing
def debug_shop_notifications(shop_id):
    """
    Debug function specifically for shop notifications.
    Call this to test your shop notification setup.
    """
    logger.info(f"=== DEBUGGING SHOP {shop_id} NOTIFICATIONS ===")
    
    # Check shop exists and is active
    from shop.models import Shop
    try:
        shop = Shop.objects.get(id=shop_id, is_active=True)
        logger.info(f"✓ Shop found: {shop.name} ({shop.place})")
    except Shop.DoesNotExist:
        logger.error(f"✗ Shop {shop_id} not found or inactive")
        return
    
    # Check device tokens
    from notifications.models import DeviceToken
    tokens = DeviceToken.objects.filter(shop_id=shop_id, is_active=True).select_related('user')
    
    logger.info(f"✓ Found {tokens.count()} active device tokens for shop {shop_id}")
    
    for i, token_obj in enumerate(tokens, 1):
        logger.info(f"  Token {i}: User {token_obj.user.full_name or token_obj.user.phone} | Platform: {token_obj.platform}")
        
        # Test each token individually
        result = test_single_notification(
            token=token_obj.token,
            title=f"Debug Test {i}",
            body=f"Testing token for {token_obj.user.full_name or token_obj.user.phone}"
        )
        
        if result["success"]:
            logger.info(f"  ✓ Token {i} test sent successfully")
        else:
            logger.error(f"  ✗ Token {i} test failed: {result['error']}")
    
    logger.info("=== DEBUG COMPLETE ===")

