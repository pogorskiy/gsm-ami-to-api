"""Simple test script to send a test SMS message to the remote API."""

from config import Config
from sms_message import SMSMessage
from api_delivery_client import APIDeliveryClient

config = Config.load_from_env()

# Create test message
message = SMSMessage(
    gateway_id="dongle0",
    sender="+79991234567",
    message="Test message from ami-to-api"
)

# Sign and send
signature = message.compute_signature(config.hmac_secret_key)
client = APIDeliveryClient(config.api_url, config.api_bearer_token)
result = client.send_message(message, signature)

if result is True:
    print("OK — message delivered")
elif result is False:
    print("FAIL — permanent error (4xx)")
else:
    print("RETRY — temporary error (5xx / network)")
