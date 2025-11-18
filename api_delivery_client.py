"""API Delivery Client for sending SMS messages to remote HTTPS API."""

import requests
from typing import Optional
from sms_message import SMSMessage
from logging_config import get_logger


logger = get_logger(__name__)


class APIDeliveryClient:
    """Client for delivering SMS messages to remote API with error classification.
    
    This client handles HTTPS POST requests to the remote API, including proper
    authentication headers and error classification for retry logic.
    
    Attributes:
        api_url: The HTTPS endpoint URL for the remote API
        bearer_token: Bearer token for API authentication
    """
    
    def __init__(self, api_url: str, bearer_token: str):
        """Initialize the API delivery client.
        
        Args:
            api_url: Remote API endpoint URL
            bearer_token: Bearer token for Authorization header
        """
        self.api_url = api_url
        self.bearer_token = bearer_token
    
    def send_message(self, message: SMSMessage, signature: str) -> Optional[bool]:
        """Send a signed SMS message to the remote API.
        
        This method sends an HTTPS POST request with the message payload and
        classifies the response to determine if the failure is permanent or temporary.
        
        Args:
            message: The SMSMessage object to send
            signature: The HMAC-SHA256 signature for the message
        
        Returns:
            True: Message delivered successfully (HTTP 2xx)
            False: Permanent failure - do not retry (HTTP 4xx except 408/429)
            None: Temporary failure - should retry (HTTP 5xx, 408, 429, network errors)
        """
        # Prepare the JSON payload with signature
        payload = {
            'gateway_id': message.gateway_id,
            'sender': message.sender,
            'timestamp': message.timestamp,
            'message': message.message,
            'signature': signature
        }
        
        # Prepare headers with Authorization
        headers = {
            'Authorization': f'Bearer {self.bearer_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            # Send HTTPS POST request
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            # Classify response based on status code
            status_code = response.status_code
            
            # Success: 2xx responses
            if 200 <= status_code < 300:
                logger.info(f"Successfully delivered message to remote API (HTTP {status_code})")
                return True
            
            # Temporary failures: 408 (Request Timeout), 429 (Too Many Requests)
            if status_code in (408, 429):
                logger.error(f"Temporary failure delivering message: HTTP {status_code}")
                return None
            
            # Permanent failures: 4xx (except 408, 429)
            if 400 <= status_code < 500:
                logger.error(f"Permanent failure delivering message: HTTP {status_code} - {response.text[:200]}")
                return False
            
            # Temporary failures: 5xx (Server Errors)
            if 500 <= status_code < 600:
                logger.error(f"Temporary failure delivering message: HTTP {status_code} - Server Error")
                return None
            
            # Unknown status code - treat as temporary failure
            logger.error(f"Unknown status code {status_code} - treating as temporary failure")
            return None
            
        except requests.exceptions.Timeout:
            # Network timeout - temporary failure
            logger.error("Temporary failure delivering message: Request timeout")
            return None
        except requests.exceptions.ConnectionError as e:
            # Connection error - temporary failure
            logger.error(f"Temporary failure delivering message: Connection error - {e}")
            return None
        except requests.exceptions.RequestException as e:
            # Other network errors - temporary failure
            logger.error(f"Temporary failure delivering message: Network error - {e}")
            return None
