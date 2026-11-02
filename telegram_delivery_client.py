"""Telegram Delivery Client for sending SMS messages directly to Telegram Bot API.

Replaces the relay server (gsm-to-telegram-bot): the message text and format
are the same as the relay server used to send.
"""

import html
import requests
from typing import Optional
from urllib.parse import unquote_plus
from sms_message import SMSMessage
from logging_config import get_logger


logger = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram limit for one message text
TELEGRAM_MAX_TEXT_LENGTH = 4096


def decode_sms_text(text: str) -> str:
    """Decode SMS text from the gateway.

    TG100 sends SMS content URL-encoded (form style: "+" for space,
    "%3A" for ":"). The relay server decoded it the same way.
    A leading BOM (U+FEFF) is removed.

    Args:
        text: SMS content as received from the gateway

    Returns:
        Decoded text
    """
    if '%' in text or '+' in text:
        try:
            text = unquote_plus(text)
        except Exception:
            pass
    return text.lstrip('\ufeff')


def format_telegram_message(message: SMSMessage) -> str:
    """Format an SMS for Telegram (HTML parse mode).

    SMS text is URL-decoded first. All SMS fields are HTML-escaped, so symbols like < > & in SMS text
    do not break Telegram HTML parsing.

    Args:
        message: The SMSMessage to format

    Returns:
        Message text ready for Telegram sendMessage
    """
    header = f"""📱 New SMS
━━━━━━━━━━━━━━━━
🔹 Gateway: {html.escape(message.gateway_id, quote=False)}
🔹 From: {html.escape(message.sender, quote=False)}
🔹 Time: {html.escape(message.timestamp, quote=False)}

💬 Message:
"""
    body = decode_sms_text(message.message)
    text = header + html.escape(body, quote=False)
    if len(text) <= TELEGRAM_MAX_TEXT_LENGTH:
        return text

    # Too long: add escaped chars one by one, so an HTML entity is never cut in half
    limit = TELEGRAM_MAX_TEXT_LENGTH - len(header) - 1  # 1 char for "…"
    parts = []
    length = 0
    for char in body:
        escaped = html.escape(char, quote=False)
        if length + len(escaped) > limit:
            break
        parts.append(escaped)
        length += len(escaped)
    return header + "".join(parts) + "…"


class TelegramDeliveryClient:
    """Client for delivering SMS messages to a Telegram chat via Bot API.

    Has the same interface as APIDeliveryClient, so the service can use
    either of them.

    Attributes:
        bot_token: Telegram bot token from @BotFather
        chat_id: Target chat ID (groups start with -100)
        timeout: Request timeout in seconds
    """

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 15):
        """Initialize the Telegram delivery client.

        Args:
            bot_token: Telegram bot token
            chat_id: Target chat ID
            timeout: Request timeout in seconds
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send_message(self, message: SMSMessage, signature: Optional[str] = None) -> Optional[bool]:
        """Send an SMS message to Telegram.

        Args:
            message: The SMSMessage object to send
            signature: Not used (kept for interface compatibility with APIDeliveryClient)

        Returns:
            True: Message delivered successfully
            False: Permanent failure - do not retry (HTTP 400, e.g. bad message)
            None: Temporary failure - should retry (5xx, 429, network errors,
                  and 401/403/404 - wrong token or chat: fix config, message stays in queue)
        """
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': format_telegram_message(message),
            'parse_mode': 'HTML',
        }

        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.Timeout:
            logger.error("Temporary failure delivering message to Telegram: Request timeout")
            return None
        except requests.exceptions.ConnectionError:
            # Do not log exception text: it contains the URL with the bot token
            logger.error("Temporary failure delivering message to Telegram: Connection error")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Temporary failure delivering message to Telegram: Network error ({type(e).__name__})")
            return None

        status_code = response.status_code
        description = self._get_description(response)

        if status_code == 200:
            logger.info(f"Successfully delivered message to Telegram (HTTP {status_code})")
            return True

        if status_code == 429:
            logger.error(f"Temporary failure delivering message to Telegram: HTTP 429 - {description}")
            return None

        if status_code in (401, 403, 404):
            logger.error(
                f"Telegram rejected bot token or chat (HTTP {status_code} - {description}). "
                f"Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID; message kept in queue"
            )
            return None

        if 400 <= status_code < 500:
            logger.error(f"Permanent failure delivering message to Telegram: HTTP {status_code} - {description}")
            return False

        logger.error(f"Temporary failure delivering message to Telegram: HTTP {status_code} - {description}")
        return None

    @staticmethod
    def _get_description(response) -> str:
        """Get the error description from a Telegram API response."""
        try:
            return str(response.json().get('description', ''))[:200]
        except Exception:
            return response.text[:200]
