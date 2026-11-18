from django.core.management.base import BaseCommand
from notifications.models import DeviceToken
from notifications.api.v3.utils.firebase_utils import test_single_notification
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Clean up invalid FCM tokens and test valid ones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--shop_id',
            type=int,
            help='Specific shop ID to check (optional)',
        )
        parser.add_argument(
            '--test-valid',
            action='store_true',
            help='Send test notification to valid tokens',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually doing it',
        )

    def handle(self, *args, **options):
        shop_id = options.get('shop_id')
        test_valid = options.get('test_valid', False)
        dry_run = options.get('dry_run', False)
        
        self.stdout.write("=== FCM Token Cleanup and Testing ===")
        
        # Base queryset
        tokens_qs = DeviceToken.objects.filter(is_active=True).select_related('user', 'shop')
        
        if shop_id:
            tokens_qs = tokens_qs.filter(shop_id=shop_id)
            self.stdout.write(f"Focusing on shop_id: {shop_id}")
        
        tokens = list(tokens_qs)
        self.stdout.write(f"Found {len(tokens)} active device tokens to check")
        
        if not tokens:
            self.stdout.write(self.style.WARNING("No tokens found to process"))
            return
        
        valid_tokens = []
        invalid_tokens = []
        
        for i, token_obj in enumerate(tokens, 1):
            user_name = token_obj.user.full_name or token_obj.user.phone
            shop_name = token_obj.shop.name if token_obj.shop else "No Shop"
            
            self.stdout.write(f"\n--- Token {i}/{len(tokens)} ---")
            self.stdout.write(f"User: {user_name}")
            self.stdout.write(f"Shop: {shop_name}")
            self.stdout.write(f"Platform: {token_obj.platform}")
            self.stdout.write(f"Updated: {token_obj.updated_at}")
            self.stdout.write(f"Token: {token_obj.token[:30]}...")
            
            # Test the token
            result = test_single_notification(
                token=token_obj.token,
                title="Token Validation Test",
                body=f"Testing token for {user_name}"
            )
            
            if result["success"]:
                self.stdout.write(self.style.SUCCESS(f"✓ Token is VALID"))
                valid_tokens.append(token_obj)
                
                if test_valid:
                    self.stdout.write("  → Test notification sent!")
                    
            else:
                self.stdout.write(self.style.ERROR(f"✗ Token is INVALID: {result['error']}"))
                invalid_tokens.append(token_obj)
                
                if not dry_run:
                    token_obj.is_active = False
                    token_obj.save()
                    self.stdout.write(self.style.WARNING("  → Token deactivated"))
                else:
                    self.stdout.write(self.style.WARNING("  → Would deactivate token (dry-run)"))
        
        # Summary
        self.stdout.write(f"\n=== SUMMARY ===")
        self.stdout.write(f"Valid tokens: {len(valid_tokens)}")
        self.stdout.write(f"Invalid tokens: {len(invalid_tokens)}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No tokens were actually deactivated"))
        else:
            self.stdout.write(f"Deactivated {len(invalid_tokens)} invalid tokens")
        
        if valid_tokens and test_valid:
            self.stdout.write(self.style.SUCCESS(f"Test notifications sent to {len(valid_tokens)} valid tokens"))
        
        # Recommendations
        if invalid_tokens:
            self.stdout.write(f"\nRECOMMENDATIONS:")
            self.stdout.write(f"- Users with invalid tokens should re-login or re-register their devices")
            self.stdout.write(f"- Check if these users updated their apps recently")
            
        if valid_tokens and not test_valid:
            self.stdout.write(f"- Run with --test-valid to send test notifications to valid tokens")
            
        self.stdout.write("\n=== COMPLETE ===")
        
        # Don't return a dict to stdout, just return it
        result = {
            'valid_count': len(valid_tokens),
            'invalid_count': len(invalid_tokens),
            'processed_count': len(tokens)
        }
        
        # Print the result instead of returning it to stdout
        self.stdout.write(f"Result: {result}")
        return result