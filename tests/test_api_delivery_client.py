"""Unit tests for API Delivery Client."""

from unittest.mock import Mock, patch
import requests
from api_delivery_client import APIDeliveryClient
from sms_message import SMSMessage


def test_api_delivery_client_initialization():
    """Test creating an API delivery client."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token-123"
    )
    
    assert client.api_url == "https://api.example.com/sms"
    assert client.bearer_token == "test-token-123"


def test_send_message_success_2xx():
    """Test successful message delivery with 2xx response."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test message",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123def456"
    
    # Mock successful response
    with patch('requests.post') as mock_post:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        result = client.send_message(message, signature)
        
        # Should return True for success
        assert result is True
        
        # Verify request was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        
        # Check URL
        assert call_args[0][0] == "https://api.example.com/sms"
        
        # Check headers include Authorization
        assert 'Authorization' in call_args[1]['headers']
        assert call_args[1]['headers']['Authorization'] == 'Bearer test-token'
        
        # Check JSON payload
        payload = call_args[1]['json']
        assert payload['gateway_id'] == "dongle0"
        assert payload['sender'] == "+1234567890"
        assert payload['message'] == "Test message"
        assert payload['timestamp'] == "2024-01-15T10:30:45Z"
        assert payload['signature'] == "abc123def456"


def test_send_message_permanent_failure_4xx():
    """Test permanent failure with 4xx response (except 408, 429)."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    # Test various 4xx codes (permanent failures)
    permanent_codes = [400, 401, 403, 404, 422]
    
    for status_code in permanent_codes:
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = status_code
            mock_response.text = "Error response body"
            mock_post.return_value = mock_response
            
            result = client.send_message(message, signature)
            
            # Should return False for permanent failure
            assert result is False, f"Status {status_code} should be permanent failure"


def test_send_message_temporary_failure_408_429():
    """Test temporary failure with 408 and 429 responses."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    # Test 408 (Request Timeout) and 429 (Too Many Requests)
    temporary_codes = [408, 429]
    
    for status_code in temporary_codes:
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = status_code
            mock_post.return_value = mock_response
            
            result = client.send_message(message, signature)
            
            # Should return None for temporary failure
            assert result is None, f"Status {status_code} should be temporary failure"


def test_send_message_temporary_failure_5xx():
    """Test temporary failure with 5xx server errors."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    # Test various 5xx codes (temporary failures)
    temporary_codes = [500, 502, 503, 504]
    
    for status_code in temporary_codes:
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = status_code
            mock_post.return_value = mock_response
            
            result = client.send_message(message, signature)
            
            # Should return None for temporary failure
            assert result is None, f"Status {status_code} should be temporary failure"


def test_send_message_network_timeout():
    """Test handling of network timeout errors."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.Timeout()
        
        result = client.send_message(message, signature)
        
        # Should return None for temporary failure
        assert result is None


def test_send_message_connection_error():
    """Test handling of connection errors."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError()
        
        result = client.send_message(message, signature)
        
        # Should return None for temporary failure
        assert result is None


def test_send_message_request_exception():
    """Test handling of generic request exceptions."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="test-token"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.RequestException()
        
        result = client.send_message(message, signature)
        
        # Should return None for temporary failure
        assert result is None


def test_authorization_header_format():
    """Test that Authorization header is properly formatted."""
    client = APIDeliveryClient(
        api_url="https://api.example.com/sms",
        bearer_token="my-secret-token-xyz"
    )
    
    message = SMSMessage(
        gateway_id="dongle0",
        sender="+1234567890",
        message="Test",
        timestamp="2024-01-15T10:30:45Z"
    )
    signature = "abc123"
    
    with patch('requests.post') as mock_post:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        client.send_message(message, signature)
        
        # Verify Authorization header format
        call_args = mock_post.call_args
        auth_header = call_args[1]['headers']['Authorization']
        assert auth_header == 'Bearer my-secret-token-xyz'
        assert auth_header.startswith('Bearer ')
