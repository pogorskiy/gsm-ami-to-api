"""Simple test script to send a test SMS message.

Uses the delivery mode from .env (DELIVERY_MODE) or from the command line:
    python test_send.py                # telegram (default)
    python test_send.py --mode relay   # via relay server
"""

from config import Config
from main import parse_args
from sms_message import SMSMessage
from sms_gateway_service import SMSGatewayService

args = parse_args()
config = Config.load_from_env(delivery_mode=args.mode)

# Create test message
message = SMSMessage(
    gateway_id="dongle0",
    sender="+79991234567",
    message="Test message from ami-to-api"
)

# Sign (relay mode only) and send with the same client the service uses
signature = None
if config.delivery_mode == 'relay':
    signature = message.compute_signature(config.hmac_secret_key)
client = SMSGatewayService(config).api_client
result = client.send_message(message, signature)

print(f"Mode: {config.delivery_mode}")
if result is True:
    print("OK — message delivered")
elif result is False:
    print("FAIL — permanent error")
else:
    print("RETRY — temporary error (see log)")
