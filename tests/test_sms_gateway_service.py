"""Tests for SMS Gateway Service orchestrator."""

import time
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
    config.queue_db_path = None
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
    mock_ami.has_early_events.return_value = False
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


def test_early_events_are_queued_before_reconnect(mock_config):
    """SMS received before the connection dropped are queued and delivered without a new connection."""
    service = SMSGatewayService(mock_config)
    event = AMIEvent('ReceivedSMS', {'Event': 'ReceivedSMS', 'Sender': '+374', 'Content': 'code'})
    service.ami_client._early_events.append(event.fields)
    service.ami_client._connected = False
    service.api_client.send_message = Mock(return_value=True)

    service._process_early_events()
    service._process_queue()

    assert not service.ami_client.has_early_events()
    assert service.message_queue.is_empty()
    sent_message = service.api_client.send_message.call_args.args[0]
    assert sent_message.message == 'code'


@patch('sms_gateway_service.AMIClient')
@patch('sys.exit')
def test_start_login_failure_with_received_sms_does_not_exit(mock_exit, mock_ami_class, mock_config):
    """SMS received during the first Login must not be lost by exiting."""
    mock_ami = Mock()
    mock_ami.connect.return_value = True
    mock_ami.login.return_value = False
    mock_ami.has_early_events.return_value = True
    mock_ami_class.return_value = mock_ami

    service = SMSGatewayService(mock_config)

    assert service.start() is False
    mock_exit.assert_not_called()


def test_queue_is_drained_without_pauses(mock_config):
    """After a successful delivery the next message goes at once."""
    service = SMSGatewayService(mock_config)
    for i in range(3):
        service.message_queue.enqueue(SMSMessage("1", "+374", f"sms {i}"), None)
    service.api_client.send_message = Mock(return_value=True)

    while service._process_queue():
        pass

    assert service.message_queue.is_empty()
    assert service.api_client.send_message.call_count == 3


def test_queue_drain_stops_on_temporary_failure(mock_config):
    service = SMSGatewayService(mock_config)
    for i in range(3):
        service.message_queue.enqueue(SMSMessage("1", "+374", f"sms {i}"), None)
    service.api_client.send_message = Mock(return_value=None)

    while service._process_queue():
        pass

    assert service.message_queue.size() == 3
    assert service.api_client.send_message.call_count == 1


def test_reconnect_does_not_block_while_waiting(mock_config):
    """Between attempts _reconnect returns at once, so the queue keeps being delivered."""
    service = SMSGatewayService(mock_config)
    service.ami_client.connect = Mock(return_value=False)
    service.ami_client.close = Mock()

    assert service._reconnect() is False        # first attempt happens at once
    assert service.ami_client.connect.call_count == 1

    start = time.monotonic()
    assert service._reconnect() is False        # next attempt is not due yet
    assert time.monotonic() - start < 0.1
    assert service.ami_client.connect.call_count == 1

    service._next_reconnect_time = 0            # time has come
    assert service._reconnect() is False
    assert service.ami_client.connect.call_count == 2


def test_reconnect_has_priority_over_draining_queue(mock_config):
    """With a due reconnect only one message is sent before reconnecting."""
    service = SMSGatewayService(mock_config)
    for i in range(5):
        service.message_queue.enqueue(SMSMessage("1", "+374", f"sms {i}"), None)
    service.api_client.send_message = Mock(return_value=True)

    assert service._reconnect_due()  # just lost the connection: reconnect at once
    while service._process_queue():
        if service._reconnect_due():
            break

    assert service.api_client.send_message.call_count == 1
    assert service.message_queue.size() == 4


def test_reconnect_schedule_uses_monotonic_clock(mock_config):
    service = SMSGatewayService(mock_config)
    service.ami_client.connect = Mock(return_value=False)
    service.ami_client.close = Mock()

    with patch('sms_gateway_service.time.time', return_value=0):  # wall clock jumps back
        service._reconnect()
        service._next_reconnect_time = 0
        service._reconnect()

    assert service.ami_client.connect.call_count == 2


@pytest.fixture
def disk_config(mock_config, tmp_path):
    """Configuration with the queue in an SQLite file."""
    mock_config.queue_db_path = str(tmp_path / "queue.db")
    return mock_config


def _statuses(path):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT status FROM messages ORDER BY id")]
    finally:
        conn.close()


def test_disk_queue_is_used_when_path_is_set(disk_config):
    from message_queue import SQLiteMessageQueue
    service = SMSGatewayService(disk_config)
    assert isinstance(service.message_queue, SQLiteMessageQueue)
    service.stop()


def test_undelivered_sms_survive_restart(disk_config):
    """SMS queued before a restart are delivered by the next process."""
    service = SMSGatewayService(disk_config)
    service.message_queue.enqueue(SMSMessage("1", "+374", "code 1234"), None)
    service.api_client.send_message = Mock(return_value=None)  # Telegram is down
    service._process_queue()
    service.stop()

    restarted = SMSGatewayService(disk_config)
    restarted.api_client.send_message = Mock(return_value=True)
    assert restarted.message_queue.size() == 1
    assert restarted._process_queue() is True

    sent = restarted.api_client.send_message.call_args[0][0]
    assert sent.message == "code 1234"
    assert restarted.message_queue.is_empty()
    restarted.stop()
    assert _statuses(disk_config.queue_db_path) == ["sent"]


def test_permanent_failure_keeps_sms_as_failed(disk_config):
    """A permanently failed SMS is kept on disk and does not block the next one."""
    service = SMSGatewayService(disk_config)
    service.message_queue.enqueue(SMSMessage("1", "+374", "bad"), None)
    service.message_queue.enqueue(SMSMessage("1", "+374", "good"), None)
    service.api_client.send_message = Mock(side_effect=[False, True])

    while service._process_queue():
        pass

    assert service.message_queue.is_empty()
    service.stop()
    assert _statuses(disk_config.queue_db_path) == ["failed", "sent"]


def test_relay_mode_signs_message_queued_without_signature(disk_config):
    """A message queued in telegram mode (no signature) gets one in relay mode."""
    service = SMSGatewayService(disk_config)
    message = SMSMessage("1", "+374", "text", "2026-11-02T10:00:00Z")
    service.message_queue.enqueue(message, None)
    service.api_client.send_message = Mock(return_value=True)

    service._process_queue()

    signature = service.api_client.send_message.call_args[0][1]
    assert signature == message.compute_signature(disk_config.hmac_secret_key)
    service.stop()


def test_queue_database_is_opened_later_if_it_fails_at_start(mock_config, tmp_path, monkeypatch):
    """A database error at start does not lose SMS; the file is opened when it works."""
    import message_queue
    db_dir = tmp_path / "missing-dir"
    mock_config.queue_db_path = str(db_dir / "queue.db")
    service = SMSGatewayService(mock_config)
    assert not service.message_queue.is_open()

    service.message_queue.enqueue(SMSMessage("1", "+374", "while broken"), None)
    assert service.message_queue.size() == 1

    db_dir.mkdir()
    monkeypatch.setattr(message_queue, "REOPEN_INTERVAL", 0)
    service.message_queue._next_open_time = 0.0
    assert service.message_queue.size() == 1
    assert service.message_queue.is_open()
    service.stop()
    assert _statuses(mock_config.queue_db_path) == ["pending"]


def test_relay_mode_replaces_stale_signature(disk_config):
    """A signature stored before an HMAC key change is computed again."""
    service = SMSGatewayService(disk_config)
    message = SMSMessage("1", "+374", "text", "2026-11-02T10:00:00Z")
    service.message_queue.enqueue(message, "signature-with-old-key")
    service.api_client.send_message = Mock(return_value=True)

    service._process_queue()

    signature = service.api_client.send_message.call_args[0][1]
    assert signature == message.compute_signature(disk_config.hmac_secret_key)
    service.stop()


@patch('sms_gateway_service.AMIClient')
@patch('sys.exit')
def test_start_without_gateway_does_not_exit_if_queue_has_sms(mock_exit, mock_ami_class, disk_config):
    """After a restart with SMS on disk, a gateway outage does not block their delivery."""
    queue = SMSGatewayService(disk_config).message_queue
    queue.enqueue(SMSMessage("1", "+374", "saved"), None)
    queue.close()

    mock_ami = Mock()
    mock_ami.connect.return_value = False
    mock_ami_class.return_value = mock_ami
    service = SMSGatewayService(disk_config)

    assert service.start() is False
    mock_exit.assert_not_called()
    service.stop()


@patch('sms_gateway_service.AMIClient')
@patch('sys.exit')
def test_start_without_gateway_exits_if_queue_is_empty(mock_exit, mock_ami_class, disk_config):
    mock_ami = Mock()
    mock_ami.connect.return_value = False
    mock_ami_class.return_value = mock_ami
    service = SMSGatewayService(disk_config)

    service.start()

    mock_exit.assert_called_once_with(1)
    service.stop()
