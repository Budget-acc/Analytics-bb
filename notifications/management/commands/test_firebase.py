import os
from django.core.management.base import BaseCommand
from firebase_admin import credentials, messaging
import firebase_admin

class Command(BaseCommand):
    help = 'Test Firebase configuration and connection using service account file path from .env'

    def handle(self, *args, **options):
        self.stdout.write("Testing Firebase configuration...")

        service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
        if not service_account_path or not os.path.exists(service_account_path):
            self.stdout.write(
                self.style.ERROR('FIREBASE_SERVICE_ACCOUNT_PATH not set or file does not exist')
            )
            return

        try:
            # Try to initialize Firebase
            if firebase_admin._apps:
                # If already initialized, delete existing apps
                for app in firebase_admin._apps.values():
                    firebase_admin.delete_app(app)
                firebase_admin._apps.clear()

            cred = credentials.Certificate(service_account_path)
            app = firebase_admin.initialize_app(cred)

            self.stdout.write(
                self.style.SUCCESS("✓ Firebase app initialized successfully")
            )

            # Test creating a message (without sending)
            try:
                test_message = messaging.Message(
                    notification=messaging.Notification(
                        title="Test Notification",
                        body="This is a test message"
                    ),
                    token="dummy_token"  # This is just for testing message creation
                )
                self.stdout.write(
                    self.style.SUCCESS("✓ Message creation test passed")
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"✗ Message creation test failed: {e}")
                )

            self.stdout.write(
                self.style.SUCCESS("\n🎉 Firebase configuration appears to be correct!")
            )
            self.stdout.write(
                "If you're still getting 404 errors, check:"
            )
            self.stdout.write("1. Your project_id matches your Firebase project")
            self.stdout.write("2. Firebase Cloud Messaging API is enabled in Google Cloud Console")
            self.stdout.write("3. Your service account has the 'Firebase Admin SDK Administrator Service Agent' role")

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"✗ Firebase initialization failed: {e}")
            )
            self.stdout.write("\nTroubleshooting steps:")
            self.stdout.write("1. Check if Firebase Cloud Messaging API is enabled")
            self.stdout.write("2. Verify service account permissions")
            self.stdout.write("3. Ensure project_id is correct")