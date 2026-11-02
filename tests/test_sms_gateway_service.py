"""Tests for SMS Gateway Service orchestrator."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from sms_gateway_service import SMSGatewayService, ServiceState
from config import Config
from ami_client import AMIEvent
from sms_message import SMSMessage


@pytest.fixture
def mock_config():
    """Create a mock configuration for testing."""
    config = Mock(spec=Config)
    config.tg100_host = "192.168.1.100"
    config.tg100_port = 5038
    config.tg100_username = "admin"
    config.tg100_password = "password"
    config.delivery_mode = "relay"
    config.api_url = "https://api.example.com/sms"
    config.api_bearer_token = "test-token"
    config.hmac_secret_key = "test-secret"
    config.initial_retry_delay = 5
    config.max_retry_delay = 60
    return config


def test_service_initialization(mock_config):
    """Test that service initializes with correct components."""
    service = SMSGatewayService(mock_config)
    
    assert service.config == mock_config
    assert service.state == ServiceState.INIT
    assert service.ami_client is not None
    assert service.message_queue is not None
    assert service.api_client is not None


@patch('sms_gateway_service.AMIClient')
def test_start_success(mock_ami_class, mock_config):
    """Test successful service startup."""
    # Mock AMI client
    mock_ami = Mock()
    mock_ami.connect.return_value = True
    mock_ami.login.return_value = True
    mock_ami_class.return_value = mock_ami
    
    service = SMSGatewayService(mock_config)
    result = service.start()
    
    assert result is True
    assert service.state == ServiceState.MONITORING
    mock_ami.connect.assert_called_once()
    mock_ami.login.assert_called_once()


@patch('sms_gateway_service.AMIClient')
@patch('sys.exit')
def test_start_connection_failure(mock_exit, mock_ami_class, mock_config):
    """Test that connection failure causes exit."""
    # Mock AMI client with connection failure
    mock_ami = Mock()
    mock_ami.connect.return_value = False
    mock_ami_class.return_value = mock_ami
    
    service = SMSGatewayService(mock_config)
    service.start()
    
    mock_exit.assert_called_once_with(1)


@patch('sms_gateway_service.AMIClient')
@patch('sys.exit')
def test_start_authentication_failure(mock_exit, mock_ami_class, mock_config):
    """Test that authentication failure causes exit."""
    # Mock AMI client with auth failure
    mock_ami = Mock()
    mock_ami.connect.return_value = True
    mock_ami.login.return_value = False
    mock_ami_class.return_value = mock_ami
    
    service = SMSGatewayService(mock_config)
    service.start()
    
    mock_ami.close.assert_called_once()
    mock_exit.assert_called_once_with(1)


def test_process_sms_event(mock_config):
    """Test processing of SMS event."""
    service = SMSGatewayService(mock_config)
    
    # Create a mock event
    event = AMIEvent('DongleNewSMSBase64', {
        'Device': 'dongle0',
        'From': '+1234567890',
        'Content': 'SGVsbG8='  # "Hello" in base64
    })
    
    # Mock the parse method to return a valid message
    mock_sms = SMSMessage('dongle0', '+1234567890', 'Hello')
    service.ami_client.parse_sms_event = Mock(return_value=mock_sms)
    
    # Process the event
    service._process_sms_event(event)
    
    # Verify message was added to queue
    assert service.message_queue.size() == 1


def test_process_queue_empty(mock_config):
    """Test queue processing when queue is empty."""
    service = SMSGatewayService(mock_config)
    
    # Should not raise any errors
    service._process_queue()
    
    assert service._delivery_retry_count == 0


def test_process_queue_success(mock_config):
    """Test successful message delivery from queue."""
    service = SMSGatewayService(mock_config)
    
    # Add a message to queue
    message = SMSMessage('dongle0', '+1234567890', 'Test')
    signature = message.compute_signature(mock_config.hmac_secret_key)
    service.message_queue.enqueue(message, signature)
    
    # Mock successful delivery
    service.api_client.send_message = Mock(return_value=True)
    
    # Process queue
    service._process_queue()
    
    # Verify message was removed
    assert service.message_queue.is_empty()
    assert service._delivery_retry_count == 0


def test_process_queue_permanent_failure(mock_config):
    """Test permanent failure removes message from queue."""
    service = SMSGatewayService(mock_config)
    
    # Add a message to queue
    message = SMSMessage('dongle0', '+1234567890', 'Test')
    signature = message.compute_signature(mock_config.hmac_secret_key)
    service.message_queue.enqueue(message, signature)
    
    # Mock permanent failure
    service.api_client.send_message = Mock(return_value=False)
    
    # Process queue
    service._process_queue()
    
    # Verify message was removed
    assert service.message_queue.is_empty()
    assert service._delivery_retry_count == 0


def test_process_queue_temporary_failure(mock_config):
    """Test temporary failure keeps message in queue."""
    service = SMSGatewayService(mock_config)
    
    # Add a message to queue
    message = SMSMessage('dongle0', '+1234567890', 'Test')
    signature = message.compute_signature(mock_config.hmac_secret_key)
    service.message_queue.enqueue(message, signature)
    
    # Mock temporary failure
    service.api_client.send_message = Mock(return_value=None)
    
    # Process queue
    service._process_queue()
    
    # Verify message is still in queue
    assert not service.message_queue.is_empty()
    assert service._delivery_retry_count == 1


@patch('time.sleep')
def test_reconnect_success(mock_sleep, mock_config):
    """Test successful reconnection."""
    service = SMSGatewayService(mock_config)
    service.state = ServiceState.MONITORING
    
    # Mock successful reconnection
    service.ami_client.is_connected = Mock(return_value=False)
    service.ami_client.close = Mock()
    service.ami_client.connect = Mock(return_value=True)
    service.ami_client.login = Mock(return_value=True)
    
    result = service._reconnect()
    
    assert result is True
    assert service.state == ServiceState.MONITORING


@patch('time.sleep')
def test_reconnect_connection_failure(mock_sleep, mock_config):
    """Test reconnection with connection failure."""
    service = SMSGatewayService(mock_config)
    
    # Mock connection failure
    service.ami_client.close = Mock()
    service.ami_client.connect = Mock(return_value=False)
    
    result = service._reconnect()
    
    assert result is False
    assert service.state == ServiceState.RECONNECTING


@patch('time.sleep')
def test_reconnect_auth_failure(mock_sleep, mock_config):
    """Test reconnection with authentication failure."""
    service = SMSGatewayService(mock_config)
    
    # Mock auth failure
    service.ami_client.close = Mock()
    service.ami_client.connect = Mock(return_value=True)
    service.ami_client.login = Mock(return_value=False)
    
    result = service._reconnect()
    
    assert result is False
    assert service.state == ServiceState.RECONNECTING


def test_stop(mock_config):
    """Test service stop."""
    service = SMSGatewayService(mock_config)
    service.ami_client.close = Mock()
    
    service.stop()
    
    service.ami_client.close.assert_called_once()


def test_telegram_mode_uses_telegram_client(mock_config):
    """In telegram mode the service sends straight to Telegram without signature."""
    from telegram_delivery_client import TelegramDeliveryClient

    mock_config.delivery_mode = "telegram"
    mock_config.telegram_bot_token = "123:ABC"
    mock_config.telegram_chat_id = "-100500"
    mock_config.telegram_request_timeout = 15
    service = SMSGatewayService(mock_config)

    assert isinstance(service.api_client, TelegramDeliveryClient)

    event = AMIEvent('ReceivedSMS', {'Span': '2', 'Sender': '+374', 'Content': 'hi'})
    service._process_sms_event(event)

    message, signature = service.message_queue.peek()
    assert message.message == 'hi'
    assert signature is None


def test_relay_mode_uses_api_client(mock_config):
    from api_delivery_client import APIDeliveryClient

    service = SMSGatewayService(mock_config)

    assert isinstance(service.api_client, APIDeliveryClient)
