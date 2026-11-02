"""Tests for AMI stream parsing on real sockets: no SMS may be lost."""

import socket
import threading
import time
import pytest
import ami_client
from ami_client import AMIClient


SMS_EVENT = (
    b"Event: ReceivedSMS\r\n"
    b"Privilege: all,smscommand\r\n"
    b"Sender: .INECOBANK\r\n"
    b"Recvtime: 2026-11-02 13:41:30\r\n"
    b"Content: Your+code+is+1234\r\n"
    b"\r\n"
)


@pytest.fixture(autouse=True)
def trace_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(ami_client, 'TRACE_FILE', str(tmp_path / 'trace.log'))


@pytest.fixture
def pair():
    """AMIClient connected to one end of a socket pair; the test writes to the other end."""
    gateway, service = socket.socketpair()
    service.setblocking(False)
    client = AMIClient('127.0.0.1', 5038, 'admin', 'secret')
    client._socket = service
    client._connected = True
    yield client, gateway
    gateway.close()
    service.close()


def test_event_in_parts_with_long_pause_is_not_lost(pair):
    client, gateway = pair
    gateway.sendall(SMS_EVENT[:40])

    # Pause longer than the read timeout: the first part must be kept
    assert client._read_message(timeout=0.05) is None

    gateway.sendall(SMS_EVENT[40:])
    message = client._read_message(timeout=0.5)

    assert message['Event'] == 'ReceivedSMS'
    assert message['Sender'] == '.INECOBANK'
    assert message['Content'] == 'Your+code+is+1234'


def test_two_events_in_one_packet_need_no_more_data(pair):
    client, gateway = pair
    gateway.sendall(SMS_EVENT + SMS_EVENT.replace(b'.INECOBANK', b'.Ardshin'))

    first = client._read_message(timeout=0.5)
    start = time.monotonic()
    second = client._read_message(timeout=2.0)

    assert first['Sender'] == '.INECOBANK'
    assert second['Sender'] == '.Ardshin'
    assert time.monotonic() - start < 0.5  # taken from the buffer, not waited for


def test_broken_utf8_line_is_kept(pair):
    client, gateway = pair
    gateway.sendall(SMS_EVENT.replace(b'Your+code+is+1234', b'code \xff 1234'))

    message = client._read_message(timeout=0.5)

    assert message['Sender'] == '.INECOBANK'
    assert message['Content'] == 'code � 1234'


def test_extra_blank_lines_are_not_eof(pair):
    client, gateway = pair
    gateway.sendall(b"\r\n\r\n" + SMS_EVENT)

    message = client._read_message(timeout=0.5)

    assert message['Event'] == 'ReceivedSMS'
    assert client.is_connected()


def test_eof_returns_empty_dict(pair):
    client, gateway = pair
    gateway.sendall(SMS_EVENT[:20])
    gateway.close()

    assert client._read_message(timeout=0.5) == {}
    assert not client.is_connected()


def test_event_during_login_is_returned_by_read_event(pair):
    client, gateway = pair
    gateway.sendall(SMS_EVENT + b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n")

    assert client.login() is True
    event = client.read_event()

    assert event.event_type == 'ReceivedSMS'
    assert event.fields['Sender'] == '.INECOBANK'


def _fake_gateway(payload, close_after=1.0):
    """Start a one-connection TCP server that sends payload. Returns (port, stop)."""
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    server.settimeout(5)
    port = server.getsockname()[1]

    def run():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            if payload:
                conn.sendall(payload)
            time.sleep(close_after)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def stop():
        server.close()
        thread.join(timeout=5)

    return port, stop


def test_data_sent_with_banner_is_not_lost():
    """The gateway may send the banner and the first message in one packet."""
    port, stop = _fake_gateway(b"Asterisk Call Manager/1.1\r\n" + SMS_EVENT)
    client = AMIClient('127.0.0.1', port, 'admin', 'secret')
    try:
        assert client.connect() is True
        event = client.read_event()
        assert event is not None
        assert event.fields['Sender'] == '.INECOBANK'
    finally:
        client.close()
        stop()


def test_eof_before_banner_fails_fast():
    """Gateway closes the connection before the banner: no busy loop, no hang."""
    port, stop = _fake_gateway(b"Asterisk Call", close_after=0)
    client = AMIClient('127.0.0.1', port, 'admin', 'secret')
    try:
        start = time.monotonic()
        assert client.connect() is False
        assert time.monotonic() - start < 3
        assert not client.is_connected()
    finally:
        stop()


def test_login_gives_up_without_response(pair, monkeypatch):
    client, gateway = pair
    monkeypatch.setattr(ami_client, 'LOGIN_TIMEOUT', 0.3)

    start = time.monotonic()
    assert client.login() is False
    assert time.monotonic() - start < 2
    assert not client.is_connected()


def test_blank_line_stream_does_not_block_read(pair):
    """Blank lines every 0.1 s must not keep _read_message longer than its timeout."""
    client, gateway = pair
    stop = threading.Event()

    def send_blank_lines():
        while not stop.is_set():
            try:
                gateway.sendall(b"\r\n")
            except OSError:
                return
            time.sleep(0.1)

    thread = threading.Thread(target=send_blank_lines, daemon=True)
    thread.start()
    try:
        start = time.monotonic()
        assert client._read_message(timeout=0.5) is None
        assert time.monotonic() - start < 1.5
        assert client.is_connected()
    finally:
        stop.set()
        thread.join(timeout=2)


def test_endless_message_drops_connection(pair, monkeypatch):
    client, gateway = pair
    monkeypatch.setattr(ami_client, 'MAX_MESSAGE_LINES', 5)
    gateway.sendall(b"".join(b"Key%d: value\r\n" % i for i in range(10)))

    assert client._read_message(timeout=0.5) == {}
    assert not client.is_connected()


def test_early_events_returned_after_disconnect(pair):
    """SMS received during Login must be returned even if the connection then dropped."""
    client, gateway = pair
    gateway.sendall(SMS_EVENT)
    gateway.shutdown(socket.SHUT_WR)  # gateway stops sending: EOF after the SMS

    assert client.login() is False  # EOF instead of Login response
    assert client.has_early_events()

    event = client.read_event()
    assert event.fields['Sender'] == '.INECOBANK'
    assert not client.has_early_events()


def test_continuous_blank_lines_do_not_block_read(pair):
    """A socket that is always ready must not keep _read_message past its timeout."""
    client, gateway = pair
    stop = threading.Event()

    def flood():
        while not stop.is_set():
            try:
                gateway.sendall(b"\r\n" * 100)
            except (BlockingIOError, OSError):
                time.sleep(0.001)

    gateway.setblocking(False)
    thread = threading.Thread(target=flood, daemon=True)
    thread.start()
    try:
        start = time.monotonic()
        assert client._read_message(timeout=0.3) is None
        assert time.monotonic() - start < 1.5
    finally:
        stop.set()
        thread.join(timeout=2)


def test_long_lines_count_towards_memory_limit(pair, monkeypatch):
    """Bytes of finished lines of an unfinished message are counted, not only the raw buffer.

    Lines are sent one by one with a read between them, so each line moves
    from the buffer to the unfinished message before the next one comes.
    """
    client, gateway = pair
    monkeypatch.setattr(ami_client, 'MAX_BUFFER_BYTES', 1000)
    line = b"Key: " + b"x" * 400 + b"\r\n"  # one line is under the limit

    gateway.sendall(line)
    assert client._read_message(timeout=0.2) is None
    assert client._buffer == b''  # the line is in the unfinished message now
    gateway.sendall(line)
    assert client._read_message(timeout=0.2) is None
    gateway.sendall(line)  # 3 x 407 bytes > 1000

    assert client._read_message(timeout=0.5) == {}
    assert not client.is_connected()


def test_next_message_does_not_count_towards_current_size(pair, monkeypatch):
    """A big but allowed message followed by an SMS in the same packet: both are returned."""
    client, gateway = pair
    monkeypatch.setattr(ami_client, 'MAX_BUFFER_BYTES', 300)
    big = b"Event: Big\r\nData: " + b"x" * 200 + b"\r\n\r\n"  # ~230 bytes, under the limit
    gateway.sendall(big + SMS_EVENT)  # together over the limit

    assert client._read_message(timeout=0.5)['Event'] == 'Big'
    assert client._read_message(timeout=0.5)['Sender'] == '.INECOBANK'


def test_complete_message_over_line_limit_is_rejected(pair, monkeypatch):
    client, gateway = pair
    monkeypatch.setattr(ami_client, 'MAX_MESSAGE_LINES', 5)
    gateway.sendall(b"".join(b"Key%d: value\r\n" % i for i in range(10)) + b"\r\n")

    assert client._read_message(timeout=0.5) == {}


def test_close_keeps_complete_sms_from_buffer(pair):
    """An SMS fully received but not parsed yet must survive close()."""
    client, gateway = pair
    client._buffer = SMS_EVENT + SMS_EVENT[:30]  # one complete SMS + a part of the next

    client.close()

    assert client.has_early_events()
    event = client.read_event()
    assert event.fields['Sender'] == '.INECOBANK'
    assert not client.has_early_events()


def test_keep_received_events_after_lost_connection(pair):
    """Connection lost (e.g. Ping failed): a complete SMS in the buffer is available at once."""
    client, gateway = pair
    client._buffer = SMS_EVENT
    client._connected = False

    client.keep_received_events()

    event = client.read_event()
    assert event.fields['Sender'] == '.INECOBANK'


def test_drain_reads_sms_still_in_socket(pair):
    """On stop, an SMS already sent by the gateway (unread socket data) is kept."""
    client, gateway = pair
    gateway.sendall(b"Response: Success\r\nPing: Pong\r\n\r\n" + SMS_EVENT)

    client.drain()
    client.close()

    event = client.read_event()
    assert event.fields['Sender'] == '.INECOBANK'
    assert not client.has_early_events()


def test_drain_returns_quickly_without_data(pair):
    client, gateway = pair
    start = time.monotonic()
    client.drain(timeout=0.1)
    assert time.monotonic() - start < 1.0
