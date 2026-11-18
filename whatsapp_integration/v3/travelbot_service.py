# travelbot_service.py
"""
Shop-specific WhatsApp sending via TravelBot (Wayon), used only when a shop has
its own ShopWhatsAppConfiguration with a usable access_token. Kept fully
separate from services.py (the app-level Wacto client) so the existing Wacto
flow is never touched by this module.
"""
import logging
import os
from typing import Any, Dict, Optional

import requests
from django.conf import settings

from whatsapp_integration.models import ShopWhatsAppConfiguration

logger = logging.getLogger("api_requests")

TRAVELBOT_API_URL = "https://travelbot.wayon.in/api/public/v1/whatsapp/messages"
TRAVELBOT_TIMEOUT_SECONDS = 30


def get_active_shop_travelbot_config(shop) -> Optional[ShopWhatsAppConfiguration]:
    """
    Return the shop's WhatsApp configuration if it exists and has a usable
    access_token, else None. This is the single gate deciding whether a send
    routes through TravelBot instead of the existing Wacto flow.
    """
    try:
        config = shop.whatsapp_configuration
        return config if config.access_token else None
    except ShopWhatsAppConfiguration.DoesNotExist:
        return None
    except Exception as e:
        logger.error(f"[TravelBot] Failed to resolve WhatsApp configuration for shop {getattr(shop, 'id', None)}: {e}")
        return None


def get_bookiebuddy_travelbot_token() -> Optional[str]:
    """
    Return the platform-wide BookieBuddy Wayon token if configured, else None.
    This is the tier-2 fallback used only when a shop has no Wayon token of
    its own (see get_active_shop_travelbot_config above).
    """
    token = os.getenv("BOOKIEBUDDY_WAYON_ACCESS_TOKEN", getattr(settings, "BOOKIEBUDDY_WAYON_ACCESS_TOKEN", ""))
    return token or None


def resolve_whatsapp_provider(shop) -> Dict[str, Any]:
    """
    Central 3-tier provider resolver: Shop Wayon -> BookieBuddy Wayon -> Wacto.

    Returns {"provider": "shop_wayon" | "bookiebuddy_wayon" | "wacto",
             "service": TravelBotService instance, or None for "wacto"}.

    Callers must dispatch to the TravelBotService method matching the
    returned provider - shop_wayon uses send_invoice_template/
    send_reminder_template (customer name only), bookiebuddy_wayon uses
    send_bookiebuddy_invoice_template/send_bookiebuddy_reminder_template
    (customer name + shop identity + button), and wacto falls back to the
    existing WhatsAppService call at the call site.
    """
    shop_config = get_active_shop_travelbot_config(shop)
    if shop_config:
        return {"provider": "shop_wayon", "service": TravelBotService(shop_config.access_token)}

    bb_token = get_bookiebuddy_travelbot_token()
    if bb_token:
        return {"provider": "bookiebuddy_wayon", "service": TravelBotService(bb_token)}

    return {"provider": "wacto", "service": None}


def first_name(full_name: str) -> str:
    """Extract the first name for TravelBot's single-variable template payload."""
    cleaned = (full_name or "").strip()
    if not cleaned:
        return "Customer"
    return cleaned.split()[0]


def _build_header(pdf_url: str, filename: str) -> Dict[str, Any]:
    """
    Builds the `template.header` object for invoice messages, matching Wayon's
    documented request structure exactly:

        "template": {
            "name": ...,
            "variables": [...],
            "header": {"type": "document", "url": pdf_url, "filename": filename}
        }

    Nested under `template`, not a top-level `attachments` key.
    """
    return {
        "type": "document",
        "url": pdf_url,
        "filename": filename,
    }


class TravelBotService:
    """Thin HTTP client for a single shop's TravelBot WhatsApp account."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: Dict[str, Any], log_context: str) -> Dict[str, Any]:
        try:
            logger.info(f"[TravelBot] Sending {log_context} to {payload.get('to')}")

            response = requests.post(
                TRAVELBOT_API_URL,
                headers=self.headers,
                json=payload,
                timeout=TRAVELBOT_TIMEOUT_SECONDS,
            )

            logger.info(f"[TravelBot] Response {response.status_code}: {response.text}")

            # Accept any 2xx as success — Wayon's API doesn't always return
            # 200 specifically for a successful send (e.g. 201 Created has
            # been observed), so gating on exactly 200 misclassified real
            # successes as failures.
            if not (200 <= response.status_code < 300):
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code,
                    "message": f"TravelBot API error: {response.status_code}",
                }

            return {
                "success": True,
                "data": response.json(),
                "message": f"{log_context} sent successfully via TravelBot",
            }

        except requests.exceptions.RequestException as e:
            error_msg = f"TravelBot request failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": error_msg,
            }

    def send_invoice_template(
        self,
        phone_number: str,
        customer_first_name: str,
        template_name: str,
        pdf_url: str,
        filename: str,
    ) -> Dict[str, Any]:
        """Send an invoice-style template (with a document attachment) via TravelBot."""
        payload = {
            "type": "template",
            "to": phone_number,
            "template": {
                "name": template_name,
                "variables": [customer_first_name],
                "header": _build_header(pdf_url, filename),
            },
        }

        return self._post(payload, log_context=f"invoice template '{template_name}'")

    def send_reminder_template(
        self,
        phone_number: str,
        customer_first_name: str,
        template_name: str,
    ) -> Dict[str, Any]:
        """Send a reminder-style template (no attachment) via TravelBot."""
        payload = {
            "type": "template",
            "to": phone_number,
            "template": {
                "name": template_name,
                "variables": [customer_first_name],
            },
        }

        return self._post(payload, log_context=f"reminder template '{template_name}'")

    def send_bookiebuddy_invoice_template(
        self,
        phone_number: str,
        customer_first_name: str,
        shop_name: str,
        shop_phone_display: str,
        formatted_shop_phone: str,
        template_name: str,
        pdf_url: str,
        filename: str,
    ) -> Dict[str, Any]:
        """
        Send an invoice-style template via TravelBot using BookieBuddy's own
        Wayon account (tier 2). Unlike send_invoice_template (tier 1, shop's
        own Wayon number - customer name only), the message here originates
        from BookieBuddy's WhatsApp number, so the template must also carry
        shop identity (name, phone).

        Confirmed against a real Wayon payload (pickup_reminder_2 sample):
        body text and button parameters are two separate top-level keys under
        `template` - variables and buttonVariables - not a single
        `variables` list, and not a nested Wacto-shaped `button` object.
        variables' 3rd element is shop_phone_display (with "+"); the
        button's single value is formatted_shop_phone (digits + country code,
        no "+").
        # TODO: confirm the actual BookieBuddy Wayon template name with the
        # Wayon dashboard owner - currently reuses the Wacto template name.
        """
        payload = {
            "type": "template",
            "to": phone_number,
            "template": {
                "name": template_name,
                "variables": [customer_first_name, shop_name, shop_phone_display],
                "buttonVariables": [formatted_shop_phone],
                "header": _build_header(pdf_url, filename),
            },
        }

        return self._post(payload, log_context=f"BookieBuddy invoice template '{template_name}'")

    def send_bookiebuddy_reminder_template(
        self,
        phone_number: str,
        customer_first_name: str,
        shop_name: str,
        shop_phone_display: str,
        formatted_shop_phone: str,
        template_name: str,
    ) -> Dict[str, Any]:
        """
        Send a reminder-style template (no attachment) via TravelBot using
        BookieBuddy's own Wayon account (tier 2). See
        send_bookiebuddy_invoice_template for why shop identity/button are
        included here but not in the tier-1 shop-Wayon equivalent, and for the
        variables/buttonVariables payload shape confirmed against a real
        Wayon sample for this exact template (pickup_reminder_2).
        # TODO: confirm the actual BookieBuddy Wayon template name with the
        # Wayon dashboard owner - currently reuses the Wacto template name.
        """
        payload = {
            "type": "template",
            "to": phone_number,
            "template": {
                "name": template_name,
                "variables": [customer_first_name, shop_name, shop_phone_display],
                "buttonVariables": [formatted_shop_phone],
            },
        }

        return self._post(payload, log_context=f"BookieBuddy reminder template '{template_name}'")
