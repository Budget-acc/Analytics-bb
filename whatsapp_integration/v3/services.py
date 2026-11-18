# service.py
import base64
import os
import logging
import requests
from typing import Dict, Any, Optional
from django.conf import settings


logger = logging.getLogger("api_requests")


# Short, single-line, user-facing. "Tailor Order" is deliberately labeled
# "Custom Work" to match the frontend's naming for this module; Booking and
# Sales labels are unchanged.
_LIMIT_EXCEEDED_TITLES = {
    "booking": "WhatsApp Message Not Sent",
    "sale": "WhatsApp Message Not Sent",
    "tailor_order": "WhatsApp Message Not Sent",
}

_LIMIT_EXCEEDED_MESSAGES = {
    "booking": "Booking Invoice {doc_id} not sent. WhatsApp message limit has been reached.",
    "sale": "Sales Invoice {doc_id} not sent. WhatsApp message limit has been reached.",
    "tailor_order": "Custom Work Invoice {doc_id} not sent. WhatsApp message limit has been reached.",
}


def notify_whatsapp_limit_exceeded(shop, document_type: str, doc_id, content_object=None):
    """
    Sends a push notification to the shop when an invoice could not be sent
    via WhatsApp because the message limit was exceeded.

    This is a feature-usage-quota exhaustion (ShopFeatureUsage/FeatureUsagePolicy
    for WHATSAPP_MESSAGE), not a subscription problem - the message therefore
    only says "WhatsApp limit reached" and never mentions the subscription,
    upgrading, or renewing.

    document_type: 'booking' | 'sale' | 'tailor_order'
    doc_id: the shop-facing ID to display (shop_booking_id / shop_sale_id / shop_tailor_id)
    content_object: the Booking/Sale/TailorOrder instance, used for generic-FK
        linkage and de-duplication (skip if a notification for this exact
        object+type already exists).
    """
    from django.contrib.contenttypes.models import ContentType
    from notifications.api.v3.utils.notification_service import create_shop_notification
    from notifications.models import Notification

    if content_object is not None:
        content_type = ContentType.objects.get_for_model(content_object)
        already_notified = Notification.objects.filter(
            shop=shop,
            notification_type="whatsapp_limit",
            content_type=content_type,
            object_id=content_object.pk,
        ).exists()
        if already_notified:
            return None

    return create_shop_notification(
        shop=shop,
        title=_LIMIT_EXCEEDED_TITLES[document_type],
        body=_LIMIT_EXCEEDED_MESSAGES[document_type].format(doc_id=doc_id),
        notification_type="whatsapp_limit",
        data={"document_type": document_type, "document_id": str(doc_id)},
        content_object=content_object,
    )


class WhatsAppService:
    def __init__(self):
        self.base_url = os.getenv('WACTO_BASE_URL', settings.WHATSAPP_CONFIG.get('BASE_URL', ''))
        self.token = os.getenv('WACTO_API_KEY', settings.WHATSAPP_CONFIG.get('TOKEN', ''))
        
        if not self.base_url or not self.token:
            logger.warning("WhatsApp credentials not configured. Set WHATSAPP_BASE_URL and WHATSAPP_TOKEN environment variables.")
        
        self.headers = {
            'Content-Type': 'application/json'
        }       

    def send_booking_invoice_template(
        self,
        phone_number: str,
        customer_name: str,
        shop_name: str,
        shop_phone: str,
        pdf_url: str,        # PUBLIC URL
        filename: str
    ) -> Dict[str, Any]:
        """
        Send WhatsApp booking invoice using Wacto TEMPLATE API.
        Wacto requires a PUBLIC document URL (no base64, no media_id).
        """

        formatted_phone = self._format_phone_number(phone_number)
        formatted_shop_phone = self._format_phone_number(shop_phone)
        shop_phone_display = self._with_plus(formatted_shop_phone)

        url = f"https://{self.base_url}/message/send-message?token={self.token}"

        payload = {
            "to": formatted_phone,
            "type": "template",
            "template": {
                "name": "rental_invoice_5",
                "language": {
                    "policy": "deterministic",
                    "code": "en"
                },
                "components": [
                    {
                        "type": "header",
                        "parameters": [
                            {
                                "type": "document",
                                "document": {
                                    "link": pdf_url,     # REQUIRED BY WACTO
                                    "filename": filename
                                }
                            }
                        ]
                    },
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": customer_name},
                            {"type": "text", "text": shop_name},
                            {"type": "text", "text": shop_phone_display}
                        ]
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {"type": "text", "text": formatted_shop_phone}
                        ]
                    }
                ]
            }
        }

        try:
            logger.info(f"Sending booking invoice template to {formatted_phone}")
            logger.info(f"PDF URL: {pdf_url}")

            response = requests.post(url, json=payload, timeout=30)

            logger.info(f"Wacto response {response.status_code}: {response.text}")

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": response.text,
                    "message": "Failed to send booking invoice template"
                }

            return {
                "success": True,
                "data": response.json(),
                "message": "Booking invoice sent successfully via WhatsApp"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Wacto request failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "WhatsApp template request failed"
            }


    def send_sale_invoice_template(
            self,
            phone_number: str,
            customer_name: str,
            shop_name: str,
            shop_phone: str,
            pdf_url: str,
            filename: str
        ) -> Dict[str, Any]:
            """
            Send WhatsApp sale invoice using Wacto TEMPLATE API.
            Wacto requires a PUBLIC document URL (no base64, no media_id).
            """
            formatted_phone = self._format_phone_number(phone_number)
            formatted_shop_phone = self._format_phone_number(shop_phone)
            print("\nFormatted phone numbers:",formatted_phone, formatted_shop_phone)

            url = f"https://{self.base_url}/message/send-message?token={self.token}"

            payload = {
                "to": formatted_phone,
                "type": "template",
                "template": {
                    "name": "sale_invoice_2",
                    "language": {
                        "policy": "deterministic",
                        "code": "en"
                    },
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "document",
                                    "document": {
                                        "link": pdf_url,
                                        "filename": filename
                                    }
                                }
                            ]
                        },
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": customer_name},
                                {"type": "text", "text": shop_name}
                            ]
                        },
                        {
                            "type": "button",
                            "sub_type": "url",
                            "index": "0",
                            "parameters": [
                                {"type": "text", "text": formatted_shop_phone}
                            ]
                        }
                    ]
                }
            }

            try:
                logger.info(f"Sending Wacto sale template to {formatted_phone}")
                logger.info(f"PDF URL: {pdf_url}")
                logger.info(f"Customer: {customer_name}, Shop: {shop_name}")

                response = requests.post(url, json=payload, timeout=30)

                logger.info(f"Wacto response status: {response.status_code}")
                logger.info(f"Wacto response: {response.text}")

                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": response.text,
                        "message": f"Wacto API error: {response.status_code}"
                    }

                return {
                    "success": True,
                    "data": response.json(),
                    "message": "Sale invoice sent successfully via WhatsApp"
                }

            except requests.exceptions.RequestException as e:
                error_msg = f"Wacto request failed: {str(e)}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "error": str(e),
                    "message": error_msg
                }

    def send_tailor_order_invoice_template(
        self,
        phone_number: str,
        customer_name: str,
        shop_name: str,
        shop_phone: str,
        pdf_url: str,
        filename: str
    ) -> Dict[str, Any]:
        """
        Send WhatsApp tailor order invoice using Wacto TEMPLATE API.
        Wacto requires a PUBLIC document URL (no base64, no media_id).
        """
        formatted_phone = self._format_phone_number(phone_number)
        formatted_shop_phone = self._format_phone_number(shop_phone)
        shop_phone_display = self._with_plus(formatted_shop_phone)

        url = f"https://{self.base_url}/message/send-message?token={self.token}"

        payload = {
            "to": formatted_phone,
            "type": "template",
            "template": {
                "name": "custom_work_2",
                "language": {
                    "policy": "deterministic",
                    "code": "en"
                },
                "components": [
                    {
                        "type": "header",
                        "parameters": [
                            {
                                "type": "document",
                                "document": {
                                    "link": pdf_url,
                                    "filename": filename
                                }
                            }
                        ]
                    },
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": customer_name},
                            {"type": "text", "text": shop_name},
                            {"type": "text", "text": shop_phone_display}
                        ]
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {"type": "text", "text": formatted_shop_phone}
                        ]
                    }
                ]
            }
        }

        try:
            logger.info(f"Sending tailor order invoice template to {formatted_phone}")
            logger.info(f"PDF URL: {pdf_url}")

            response = requests.post(url, json=payload, timeout=30)

            logger.info(f"Wacto response {response.status_code}: {response.text}")

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": response.text,
                    "message": "Failed to send tailor order invoice template"
                }

            return {
                "success": True,
                "data": response.json(),
                "message": "Tailor order invoice sent successfully via WhatsApp"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Wacto request failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "WhatsApp template request failed"
            }

    def send_pickup_ready_template(
        self,
        phone_number: str,
        customer_name: str,
        shop_name: str,
        shop_phone: str
    ) -> Dict[str, Any]:
        """
        Send 'Pickup Ready' WhatsApp notification using Wacto template.
        Template: rental_pickup_ready_template
        """
        formatted_phone = self._format_phone_number(phone_number)
        formatted_shop_phone = self._format_phone_number(shop_phone)
        shop_phone_display = self._with_plus(formatted_shop_phone)

        url = f"https://{self.base_url}/message/send-message?token={self.token}"

        payload = {
            "to": formatted_phone,
            "type": "template",
            "template": {
                "name": "pickup_reminder_2",
                "language": {
                    "policy": "deterministic",
                    "code": "en"
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": customer_name},
                            {"type": "text", "text": shop_name},
                            {"type": "text", "text": shop_phone_display}
                        ]
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {"type": "text", "text": formatted_shop_phone}
                        ]
                    }
                ]
            }
        }

        try:
            logger.info(f"Sending pickup ready notification to {formatted_phone}")
            response = requests.post(url, json=payload, timeout=30)
            logger.info(f"Wacto response status: {response.status_code}")

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": response.text,
                    "message": f"Wacto API error: {response.status_code}"
                }

            return {
                "success": True,
                "data": response.json(),
                "message": "Pickup ready notification sent successfully"
            }

        except requests.exceptions.RequestException as e:
            error_msg = f"Wacto request failed: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": str(e),
                "message": error_msg
            }

    def send_return_reminder_template(
        self,
        phone_number: str,
        customer_name: str,
        shop_name: str,
        shop_phone: str
    ) -> Dict[str, Any]:
        """
        Send 'Return Reminder' WhatsApp notification using Wacto template.
        Template: rental_return_reminder
        """
        formatted_phone = self._format_phone_number(phone_number)
        formatted_shop_phone = self._format_phone_number(shop_phone)
        shop_phone_display = self._with_plus(formatted_shop_phone)

        url = f"https://{self.base_url}/message/send-message?token={self.token}"

        payload = {
            "to": formatted_phone,
            "type": "template",
            "template": {
                "name": "return_reminder_1",
                "language": {
                    "policy": "deterministic",
                    "code": "en"
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": customer_name},
                            {"type": "text", "text": shop_name},
                            {"type": "text", "text": shop_phone_display}
                        ]
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {"type": "text", "text": formatted_shop_phone}
                        ]
                    }
                ]
            }
        }

        try:
            logger.info(f"Sending return reminder to {formatted_phone}")
            response = requests.post(url, json=payload, timeout=30)
            logger.info(f"Wacto response status: {response.status_code}")

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": response.text,
                    "message": f"Wacto API error: {response.status_code}"
                }

            return {
                "success": True,
                "data": response.json(),
                "message": "Return reminder sent successfully"
            }

        except requests.exceptions.RequestException as e:
            error_msg = f"Wacto request failed: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": str(e),
                "message": error_msg
            }
        


    def _upload_media(self, file_base64: str, media_type: str, filename: str) -> Optional[str]:
        """Upload media (PDF) to Wacto API and get media_id"""
        url = f"https://{self.base_url}/v1/media/upload"
        
        # Wacto expects base64 in JSON payload
        payload = {
            "token": self.token,
            "type": media_type,
            "filename": filename,
            "data": file_base64  # Send base64 string directly
        }

        try:
            logger.info(f"Uploading media to Wacto: {filename}")
            logger.info(f"Upload URL: {url}")
            logger.info(f"Payload keys: {list(payload.keys())}")
            logger.info(f"Data length: {len(file_base64)}")
            
            response = requests.post(url, headers=self.headers, json=payload, timeout=60)
            
            logger.info(f"Media upload response status: {response.status_code}")
            logger.info(f"Media upload response: {response.text}")
            
            if response.status_code != 200:
                logger.error(f"Media upload failed with status {response.status_code}: {response.text}")
                return None
            
            result = response.json()
            logger.info(f"Parsed response JSON: {result}")
            
            # Try multiple possible keys for media_id
            media_id = (result.get('media_id') or 
                       result.get('id') or 
                       result.get('mediaId') or 
                       result.get('media') or
                       result.get('file_id'))
            
            if media_id:
                logger.info(f"✅ Media uploaded successfully, ID: {media_id}")
                return str(media_id)
            else:
                logger.error(f"Media upload response missing media_id. Full response: {result}")
                logger.error(f"Available keys in response: {list(result.keys())}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to upload media: {str(e)} | Response: {getattr(e.response, 'text', '')}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error uploading media: {str(e)}", exc_info=True)
            return None

    def upload_pdf_to_s3(self, pdf_base64: str, filename: str) -> Optional[str]:
        """
        Upload PDF to S3 and return public URL.
        Required for Wacto API which only accepts public URLs.
        """
        try:
            import boto3
            import uuid
            from datetime import datetime
            
            # Decode base64
            pdf_data = base64.b64decode(pdf_base64)
            
            # Generate unique S3 key
            date_prefix = datetime.now().strftime('%Y/%m/%d')
            unique_id = uuid.uuid4().hex[:8]
            s3_key = f"whatsapp-invoices/{date_prefix}/{unique_id}_{filename}"
            
            # Upload to S3
            s3 = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            )
            
            s3.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=s3_key,
                Body=pdf_data,
                ContentType='application/pdf'
            )
            
            # Generate public URL (bucket policy should allow public read)
            pdf_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"
            
            logger.info(f"PDF uploaded to S3: {pdf_url}")
            return pdf_url
            
        except Exception as e:
            logger.error(f"Failed to upload PDF to S3: {str(e)}")
            return None

    def _format_phone_number(self, phone_number: str) -> str:
        """
        Format phone number for WhatsApp API (Wacto).

        Rules:
        - If starts with '+' → remove '+' (already E164)
        - If starts with country code (no '+') → send as is
        - If 10-digit number → assume India (add 91)
        - If 11-digit starting with 0 → remove 0, add 91
        """

        if not phone_number:
            return ""

        raw = str(phone_number).strip()

        # Remove spaces, dashes, brackets
        cleaned = ''.join(c for c in raw if c.isdigit() or c == '+')

        # Case 1: Already E164
        if cleaned.startswith('+'):
            return cleaned[1:]  # remove '+'

        # Case 2: 10-digit (India local)
        if len(cleaned) == 10 and cleaned.isdigit():
            return "91" + cleaned

        # Case 3: 11-digit starting with 0
        if len(cleaned) == 11 and cleaned.startswith("0"):
            return "91" + cleaned[1:]

        # Case 4: Already country code without '+'
        if cleaned.isdigit():
            return cleaned

        # Fallback safety
        return cleaned

    def _with_plus(self, formatted_phone: str) -> str:
        """
        Prefix an already-formatted (digits-only, country-code-included)
        phone number with '+' for display in template bodies.
        """
        if not formatted_phone:
            return ""
        return f"+{formatted_phone}"

    def debug_whatsapp_config(self):
        """Debug the complete WhatsApp configuration"""
        print("=== WHATSAPP CONFIG DEBUG ===")
        
        # Check config values
        print(f"📱 Phone Number ID: {self.config['PHONE_NUMBER_ID']}")
        print(f"🌐 Base URL: {self.config['BASE_URL']}")
        print(f"🔢 API Version: {self.config['API_VERSION']}")
        print(f"🔑 Token Length: {len(self.config['ACCESS_TOKEN']) if self.config['ACCESS_TOKEN'] else 'MISSING'}")
        
        # Test API connectivity
        test_url = f"{self.base_url}/{self.config['PHONE_NUMBER_ID']}"
        try:
            response = requests.get(test_url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                phone_info = response.json()
                print("✅ Phone Number Connection: SUCCESS")
                print(f"   Business: {phone_info.get('verified_name')}")
                print(f"   Number: {phone_info.get('display_phone_number')}")
                print(f"   ID: {phone_info.get('id')}")
            else:
                print(f"❌ Phone Number Connection: FAILED - {response.status_code}")
                print(f"   Response: {response.text}")
        except Exception as e:
            print(f"❌ Connection Test Failed: {e}")

    def test_simple_message(self, phone_number: str) -> Dict[str, Any]:
        """Test with simple text message (session message)"""
        formatted_phone = self._format_phone_number(phone_number)
        
        url = f"{self.base_url}/{self.config['PHONE_NUMBER_ID']}/messages"
        
        payload = {
            "messaging_product": "whatsapp",
            "to": formatted_phone,
            "type": "text",
            "text": {"body": "Test message from Bookie Buddy"}
        }
        
        print("🧪 Testing simple message...")
        print(f"   URL: {url}")
        print(f"   To: {formatted_phone}")
        
        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.text}")
            
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as e:
            print(f"❌ Simple message failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"   Error details: {e.response.json()}")
            return {"success": False, "error": str(e)}

    def debug_template_structure(self, template_name: str):
        """Check what parameters the template expects"""
        print(f"=== TEMPLATE STRUCTURE DEBUG: {template_name} ===")
        
        # Based on your template screenshot, here's what it needs:
        if template_name == "bookie_buddy_production":
            print("📋 Template Requirements:")
            print("   HEADER: Document (PDF)")
            print("   BODY: 2 parameters:")
            print("     - {{customer_name}} (text)")
            print("     - {{shop_name}} (text)") 
            print("   BUTTON: URL button with 1 parameter:")
            print("     - {{1}} (becomes part of URL)")
            print("   URL Format: https://bookiebuddy.in/call/{{1}}/")
        
        elif template_name == "sale_invoice":
            print("📋 Template Requirements:")
            print("   HEADER: Document (PDF)")
            print("   BODY: 2 parameters:")
            print("     - {{customer_name}} (text)")
            print("     - {{sale_id}} (text)")

    def validate_payload(self, payload: dict, template_name: str):
        """Validate payload matches template structure"""
        print("🔍 Validating payload structure...")
        
        try:
            components = payload["template"]["components"]
            
            # Check header
            header = next((c for c in components if c["type"] == "header"), None)
            if header:
                print("✅ Header: Present")
            
            # Check body
            body = next((c for c in components if c["type"] == "body"), None)
            if body:
                param_count = len(body.get("parameters", []))
                print(f"✅ Body: {param_count} parameters")
            
            # Check buttons
            buttons = [c for c in components if c["type"] == "button"]
            for btn in buttons:
                print(f"✅ Button: {btn.get('sub_type')} with {len(btn.get('parameters', []))} parameters")
                
        except Exception as e:
            print(f"❌ Payload validation error: {e}")

