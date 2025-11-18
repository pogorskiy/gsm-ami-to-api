"""Unit tests for SMS Message model."""

import json
import re
from datetime import datetime, timezone
from sms_message import SMSMessage


def test_sms_message_creation():
    """Test creating an SMS message with explicit timestamp."""
    msg = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Hello World",
        timestamp="2024-01-15T10:30:45Z"
    )
    
    assert msg.gateway_id == "dongle0"
    assert msg.sender == "+1234567890"
    assert msg.message == "Hello World"
    assert msg.timestamp == "2024-01-15T10:30:45Z"


def test_sms_message_auto_timestamp():
    """Test that timestamp is auto-generated if not provided."""
    msg = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test"
    )
    
    # Verify timestamp is in ISO 8601 format
    iso_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
    assert re.match(iso_pattern, msg.timestamp)


def test_to_json_sorted_keys():
    """Test JSON serialization with sorted keys."""
    msg = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Hello",
        timestamp="2024-01-15T10:30:45Z"
    )
    
    json_str = msg.to_json(sort_keys=True)
    data = json.loads(json_str)
    
    # Verify all fields are present
    assert data['gateway_id'] == "dongle0"
    assert data['sender'] == "+1234567890"
    assert data['message'] == "Hello"
    assert data['timestamp'] == "2024-01-15T10:30:45Z"
    
    # Verify keys are sorted in the JSON string
    expected_order = '{"gateway_id": "dongle0", "message": "Hello", "sender": "+1234567890", "timestamp": "2024-01-15T10:30:45Z"}'
    assert json_str == expected_order


def test_compute_signature_determinism():
    """Test that signature computation is deterministic."""
    msg = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Hello",
        timestamp="2024-01-15T10:30:45Z"
    )
    
    secret = "test-secret-key"
    
    # Compute signature twice
    sig1 = msg.compute_signature(secret)
    sig2 = msg.compute_signature(secret)
    
    # Should be identical
    assert sig1 == sig2
    
    # Should be hexadecimal
    assert re.match(r'^[0-9a-f]+$', sig1)
    
    # HMAC-SHA256 produces 64 hex characters (32 bytes)
    assert len(sig1) == 64


def test_compute_signature_different_keys():
    """Test that different keys produce different signatures."""
    msg = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Hello",
        timestamp="2024-01-15T10:30:45Z"
    )
    
    sig1 = msg.compute_signature("key1")
    sig2 = msg.compute_signature("key2")
    
    assert sig1 != sig2


def test_to_signed_json():
    """Test creating signed JSON payload."""
    msg = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Hello",
        timestamp="2024-01-15T10:30:45Z"
    )
    
    signed_json = msg.to_signed_json("test-secret")
    data = json.loads(signed_json)
    
    # Verify all fields including signature
    assert 'gateway_id' in data
    assert 'sender' in data
    assert 'message' in data
    assert 'timestamp' in data
    assert 'signature' in data
    
    # Verify signature is hexadecimal
    assert re.match(r'^[0-9a-f]+$', data['signature'])
    assert len(data['signature']) == 64
