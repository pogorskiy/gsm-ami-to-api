"""Unit tests for AMI client."""

import unittest
import base64
from ami_client import AMIEvent, AMIClient
from sms_message import SMSMessage


class TestAMIEvent(unittest.TestCase):
    """Test AMIEvent class."""
    
    def test_ami_event_creation(self):
        """Test creating an AMI event."""
        fields = {'Device': 'dongle0', 'From': '+1234567890'}
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        self.assertEqual(event.event_type, 'DongleNewSMSBase64')
        self.assertEqual(event.fields, fields)
    
    def test_ami_event_repr(self):
        """Test AMI event string representation."""
        fields = {'Device': 'dongle0'}
        event = AMIEvent('TestEvent', fields)
        
        repr_str = repr(event)
        self.assertIn('TestEvent', repr_str)
        self.assertIn('dongle0', repr_str)


class TestAMIClientParsing(unittest.TestCase):
    """Test AMI client SMS parsing functionality."""
    
    def test_parse_valid_sms_event(self):
        """Test parsing a valid DongleNewSMSBase64 event."""
        # Create a valid SMS event
        message_text = "Hello World"
        content_base64 = base64.b64encode(message_text.encode('utf-8')).decode('utf-8')
        
        fields = {
            'Device': 'dongle0',
            'From': '+1234567890',
            'Content': content_base64
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        # Create client (no connection needed for parsing)
        client = AMIClient('localhost', 5038, 'user', 'pass')
        
        # Parse the event
        sms = client.parse_sms_event(event)
        
        self.assertIsNotNone(sms)
        self.assertEqual(sms.gateway_id, 'dongle0')
        self.assertEqual(sms.sender, '+1234567890')
        self.assertEqual(sms.message, 'Hello World')
        self.assertIsNotNone(sms.timestamp)
    
    def test_parse_wrong_event_type(self):
        """Test parsing non-SMS event returns None."""
        fields = {'Device': 'dongle0', 'From': '+1234567890'}
        event = AMIEvent('SomeOtherEvent', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNone(sms)
    
    def test_parse_missing_device_field(self):
        """Test parsing event with missing Device field."""
        message_text = "Test"
        content_base64 = base64.b64encode(message_text.encode('utf-8')).decode('utf-8')
        
        fields = {
            'From': '+1234567890',
            'Content': content_base64
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNone(sms)
    
    def test_parse_missing_from_field(self):
        """Test parsing event with missing From field."""
        message_text = "Test"
        content_base64 = base64.b64encode(message_text.encode('utf-8')).decode('utf-8')
        
        fields = {
            'Device': 'dongle0',
            'Content': content_base64
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNone(sms)
    
    def test_parse_missing_content_field(self):
        """Test parsing event with missing Content field."""
        fields = {
            'Device': 'dongle0',
            'From': '+1234567890'
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNone(sms)
    
    def test_parse_invalid_base64(self):
        """Test parsing event with invalid Base64 content."""
        fields = {
            'Device': 'dongle0',
            'From': '+1234567890',
            'Content': 'not-valid-base64!!!'
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNone(sms)
    
    def test_parse_empty_message(self):
        """Test parsing event with empty message content."""
        message_text = ""
        content_base64 = base64.b64encode(message_text.encode('utf-8')).decode('utf-8')
        
        fields = {
            'Device': 'dongle0',
            'From': '+1234567890',
            'Content': content_base64
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNotNone(sms)
        self.assertEqual(sms.message, '')
    
    def test_parse_unicode_message(self):
        """Test parsing event with Unicode characters."""
        message_text = "Hello world 🌍"
        content_base64 = base64.b64encode(message_text.encode('utf-8')).decode('utf-8')
        
        fields = {
            'Device': 'dongle0',
            'From': '+1234567890',
            'Content': content_base64
        }
        event = AMIEvent('DongleNewSMSBase64', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNotNone(sms)
        self.assertEqual(sms.message, "Hello world 🌍")
    
    def test_parse_received_sms_event(self):
        """Test parsing ReceivedSMS event (TG100 format)."""
        fields = {
            'Event': 'ReceivedSMS',
            'Span': '2',
            'Sender': '+79047983571',
            'Content': 'Test message',
            'Recvtime': '2025-11-18 15:52:05'
        }
        event = AMIEvent('ReceivedSMS', fields)
        
        client = AMIClient('localhost', 5038, 'user', 'pass')
        sms = client.parse_sms_event(event)
        
        self.assertIsNotNone(sms)
        self.assertEqual(sms.gateway_id, '2')
        self.assertEqual(sms.sender, '+79047983571')
        self.assertEqual(sms.message, 'Test message')


class TestAMIClientConnection(unittest.TestCase):
    """Test AMI client connection management."""
    
    def test_initial_state_not_connected(self):
        """Test that client starts in disconnected state."""
        client = AMIClient('localhost', 5038, 'user', 'pass')
        self.assertFalse(client.is_connected())
    
    def test_close_when_not_connected(self):
        """Test that close() works even when not connected."""
        client = AMIClient('localhost', 5038, 'user', 'pass')
        client.close()  # Should not raise exception
        self.assertFalse(client.is_connected())


if __name__ == '__main__':
    unittest.main()
