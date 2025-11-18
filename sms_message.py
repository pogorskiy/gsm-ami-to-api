"""SMS Message Model with cryptographic signing support."""

import json
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Dict


class SMSMessage:
    """Represents an incoming SMS message with metadata and signing capabilities.
    
    Attributes:
        gateway_id: Identifier of the GSM gateway that received the message
        sender: Phone number of the message sender
        message: Content of the SMS message
        timestamp: ISO 8601 UTC timestamp when the message was received
    """
    
    def __init__(self, gateway_id: str, sender: str, message: str, timestamp: str = None):
        """Initialize an SMS message.
        
        Args:
            gateway_id: Gateway identifier (e.g., "dongle0")
            sender: Sender phone number (e.g., "+1234567890")
            message: SMS message content
            timestamp: ISO 8601 UTC timestamp. If None, current time is used.
        """
        self.gateway_id = gateway_id
        self.sender = sender
        self.message = message
        self.timestamp = timestamp if timestamp is not None else self._generate_timestamp()
    
    @staticmethod
    def _generate_timestamp() -> str:
        """Generate current timestamp in ISO 8601 UTC format.
        
        Returns:
            Timestamp string in format: YYYY-MM-DDTHH:MM:SSZ
        """
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    
    def to_json(self, sort_keys: bool = True) -> str:
        """Serialize message to JSON string.
        
        Args:
            sort_keys: If True, sort dictionary keys for deterministic output
        
        Returns:
            JSON string representation of the message
        """
        data = {
            'gateway_id': self.gateway_id,
            'sender': self.sender,
            'timestamp': self.timestamp,
            'message': self.message
        }
        return json.dumps(data, sort_keys=sort_keys)
    
    def compute_signature(self, secret_key: str) -> str:
        """Compute HMAC-SHA256 signature for the message.
        
        The signature is computed over the JSON representation of the message
        (without the signature field itself) using sorted keys for deterministic
        serialization.
        
        Args:
            secret_key: Secret key for HMAC computation
        
        Returns:
            Hexadecimal string representation of the HMAC-SHA256 signature
        """
        # Get JSON representation with sorted keys for deterministic output
        json_data = self.to_json(sort_keys=True)
        
        # Compute HMAC-SHA256
        signature = hmac.new(
            secret_key.encode('utf-8'),
            json_data.encode('utf-8'),
            hashlib.sha256
        )
        
        return signature.hexdigest()
    
    def to_signed_json(self, secret_key: str) -> str:
        """Create signed JSON payload with signature field.
        
        Args:
            secret_key: Secret key for HMAC computation
        
        Returns:
            JSON string with all message fields plus signature
        """
        signature = self.compute_signature(secret_key)
        
        data = {
            'gateway_id': self.gateway_id,
            'sender': self.sender,
            'timestamp': self.timestamp,
            'message': self.message,
            'signature': signature
        }
        
        return json.dumps(data, sort_keys=True)
