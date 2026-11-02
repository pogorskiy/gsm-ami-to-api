"""AMI (Asterisk Manager Interface) protocol client for TG100 gateway."""

import socket
import select
import base64
import time
from collections import deque
from typing import Deque, Dict, List, Optional
from datetime import datetime, timezone
from sms_message import SMSMessage
from logging_config import get_logger


logger = get_logger(__name__)

# Trace file for AMI protocol debugging
TRACE_FILE = 'trace.log'

# Time limits (seconds)
BANNER_TIMEOUT = 10.0  # waiting for the AMI banner after connect
LOGIN_TIMEOUT = 30.0   # waiting for the Login response

# Limits for one message: protect memory from a broken stream
MAX_MESSAGE_LINES = 1000
MAX_BUFFER_BYTES = 1024 * 1024  # buffer + lines of the unfinished message


class MessageTooLarge(Exception):
    """An AMI message exceeds MAX_MESSAGE_LINES or MAX_BUFFER_BYTES."""


def _write_trace(direction: str, data: str) -> None:
    """Write AMI protocol trace to trace.log file.
    
    Args:
        direction: Either 'SEND' or 'RECV' to indicate direction
        data: The data being sent or received
    """
    try:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        with open(TRACE_FILE, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] {direction}\n")
            f.write(f"{'-'*80}\n")
            f.write(data)
            if not data.endswith('\n'):
                f.write('\n')
            f.write(f"{'='*80}\n")
    except Exception as e:
        logger.warning(f"Failed to write trace: {e}")


class AMIEvent:
    """Represents a parsed AMI event.
    
    Attributes:
        event_type: Type of the AMI event (e.g., "DongleNewSMSBase64")
        fields: Dictionary of key-value pairs from the event
    """
    
    def __init__(self, event_type: str, fields: Dict[str, str]):
        """Initialize an AMI event.
        
        Args:
            event_type: The event type identifier
            fields: Dictionary containing all event fields
        """
        self.event_type = event_type
        self.fields = fields
    
    def __repr__(self) -> str:
        return f"AMIEvent(event_type={self.event_type!r}, fields={self.fields!r})"


class AMIClient:
    """Client for communicating with TG100 via AMI protocol.
    
    The AMI protocol is a line-based text protocol where messages consist of
    key-value pairs separated by colons, with messages delimited by blank lines.
    """
    
    def __init__(self, host: str, port: int, username: str, password: str):
        """Initialize AMI client.
        
        Args:
            host: TG100 hostname or IP address
            port: AMI port (typically 5038)
            username: AMI username
            password: AMI password
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._socket: Optional[socket.socket] = None
        self._connected = False
        self._buffer = b''  # Persistent buffer for incomplete messages
        self._pending_lines: List[str] = []  # Lines of a message not finished yet
        self._pending_bytes = 0  # Size of _pending_lines
        self._early_events: Deque[Dict[str, str]] = deque()  # Events received while waiting for Login response
        
        # Initialize trace file
        try:
            with open(TRACE_FILE, 'w', encoding='utf-8') as f:
                timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                f.write(f"AMI Protocol Trace Log\n")
                f.write(f"Started: {timestamp}\n")
                f.write(f"TG100: {host}:{port}\n")
                f.write(f"{'='*80}\n")
        except Exception as e:
            logger.warning(f"Failed to initialize trace file: {e}")
    
    def connect(self) -> bool:
        """Establish TCP connection to TG100.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Set TCP keepalive to detect dead connections
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            # Connection timeout for initial connect
            self._socket.settimeout(10.0)
            self._socket.connect((self.host, self.port))
            
            # Set socket to non-blocking mode for use with select()
            # This allows timeout-based reads without blocking indefinitely
            self._socket.setblocking(False)
            
            self._connected = True
            self._reset_parser()
            
            # Read and log the AMI banner (with timeout for initial read)
            banner_bytes = b''
            deadline = time.monotonic() + BANNER_TIMEOUT
            while b'\n' not in banner_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.error("Timeout while reading banner")
                ready = select.select([self._socket], [], [], min(remaining, 1.0))
                if not ready[0]:
                    continue
                
                try:
                    chunk = self._socket.recv(1024)
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    # EOF goes to the error handler below - no retry on a closed socket
                    raise socket.error("Connection closed while reading banner")
                banner_bytes += chunk
            
            banner_line, rest = banner_bytes.split(b'\n', 1)
            banner = banner_line.decode('utf-8', errors='replace') + '\n'
            _write_trace('RECV', banner)
            # Data after the banner belongs to the next messages - keep it
            self._buffer = rest
            
            logger.info(f"Connected to TG100 at {self.host}:{self.port}")
            logger.info(f"TG100 Banner: {banner.strip()}")
            
            return True
        except (socket.error, socket.timeout) as e:
            logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self._connected = False
            if self._socket:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
            return False
    
    def login(self) -> bool:
        """Authenticate with TG100 using AMI Login command.

        Returns:
            True if authentication successful, False otherwise
        """
        if not self._connected or not self._socket:
            logger.error("Cannot login: not connected")
            return False

        try:
            # Send Action: Login (as in working example)
            login_cmd = (
                "Action: Login\r\n"
                f"Username: {self.username}\r\n"
                f"Secret: {self.password}\r\n"
                # "Events: on\r\n"  # if desired
                "\r\n"
            )
            logger.info("Sending login command with username: %s", self.username)
            
            # Trace outgoing command (without the password)
            _write_trace('SEND', login_cmd.replace(f"Secret: {self.password}", "Secret: ****"))
            
            self._socket.sendall(login_cmd.encode("utf-8"))

            # Wait for Login Response, keeping Events; give up after LOGIN_TIMEOUT
            deadline = time.monotonic() + LOGIN_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.error(f"No Login response from TG100 in {LOGIN_TIMEOUT:.0f} seconds")
                    self._connected = False
                    return False
                
                response = self._read_message(timeout=min(remaining, 10.0))
                
                if response is None:
                    # Timeout - no data yet, keep waiting
                    continue
                
                if response == {}:
                    # EOF - connection closed
                    logger.error("Connection closed while waiting for Login response")
                    return False

                logger.debug("AMI message during login: %s", response)

                resp_type = response.get("Response")
                if resp_type == "Success":
                    logger.info("Authentication successful")
                    return True
                if resp_type == "Error":
                    error_msg = response.get("Message", "Unknown error")
                    logger.error("Authentication failed: %s", error_msg)
                    logger.error("Full response details: %s", response)
                    return False

                # An event (maybe a new SMS) came before the Login response - keep it for read_event
                if 'Event' in response:
                    self._early_events.append(response)

        except (socket.error, socket.timeout) as e:
            logger.error(f"Login failed: {e}")
            self._connected = False
            return False
    
    def read_event(self) -> Optional[AMIEvent]:
        """Read and parse the next AMI event from the connection.
        
        Returns:
            AMIEvent object if an event was read, None if no data available (timeout) or not an event
        """
        # Events already received are returned even if the connection is gone
        if self._early_events:
            message = self._early_events.popleft()
            event_type = message['Event']
            logger.info(f"Received event type: {event_type}")
            return AMIEvent(event_type, message)
        
        if not self._connected or not self._socket:
            return None
        
        try:
            message = self._read_message()
            
            if message is None:
                # Timeout - no data available, connection is alive
                return None
            
            if message == {}:
                # EOF - connection closed
                self._connected = False
                return None
            
            # Log all messages from TG100 for debugging
            logger.debug(f"TG100 Message: {message}")
            
            # Check if this is an event (has Event field)
            if 'Event' in message:
                event_type = message['Event']
                logger.info(f"Received event type: {event_type}")
                return AMIEvent(event_type, message)
            
            # Not an event, just a response - return None to continue reading
            return None
            
        except socket.error as e:
            logger.error(f"Error reading event: {e}")
            self._connected = False
            return None
    
    def _read_message(self, timeout: float = 1.0) -> Optional[Dict[str, str]]:
        """Read a single AMI message (until blank line).
        
        Lines of an unfinished message are kept between calls, so a message
        that arrives in parts with long pauses is not lost. Data already in
        the buffer is parsed before waiting for the socket.
        
        Args:
            timeout: Timeout in seconds for select (default 1.0)
        
        Returns:
            Dictionary of key-value pairs if message complete
            None if no complete message yet (timeout) - connection still alive
            Empty dict {} if connection closed (EOF) or socket error
        """
        deadline = time.monotonic() + timeout
        try:
            while True:
                # A complete message may already be in the buffer
                message = self._parse_buffered_message()
                if message is not None:
                    return message
                
                # Not longer than timeout in total: a socket that is always
                # ready (a stream of blank lines) must not keep us here
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                
                ready = select.select([self._socket], [], [], remaining)
                if not ready[0]:
                    # Timeout - unfinished lines stay in _pending_lines
                    return None
                
                try:
                    chunk = self._socket.recv(4096)
                except (BlockingIOError, InterruptedError):
                    # No data available yet (shouldn't happen after select)
                    continue
                
                if not chunk:
                    # EOF - connection closed
                    if self._pending_lines:
                        _write_trace('RECV', ''.join(self._pending_lines))
                    self._connected = False
                    return {}  # Empty dict signals EOF
                self._buffer += chunk
                
        except MessageTooLarge as e:
            logger.error(f"AMI message too large ({e}) - dropping connection")
            if self._pending_lines:
                _write_trace('RECV', ''.join(self._pending_lines[:50]))
            self._pending_lines = []
            self._pending_bytes = 0
            self._buffer = b''
            self._connected = False
            return {}
        except socket.error as e:
            logger.error(f"Socket error in _read_message: {e}")
            if self._pending_lines:
                _write_trace('RECV', ''.join(self._pending_lines))
            self._connected = False
            return {}  # Empty dict signals error
    
    def _parse_buffered_message(self) -> Optional[Dict[str, str]]:
        """Take one complete message from the buffer.
        
        Returns:
            Dictionary of key-value pairs if a complete message is in the buffer,
            None if more data is needed
        
        Raises:
            MessageTooLarge: the message (or a line without end) is over the limits
        """
        while b'\n' in self._buffer:
            line_bytes, self._buffer = self._buffer.split(b'\n', 1)
            # Do not drop a line with broken UTF-8: it may be the SMS text
            line = line_bytes.decode('utf-8', errors='replace') + '\n'
            
            if line.strip():
                self._pending_lines.append(line)
                self._pending_bytes += len(line_bytes) + 1
                self._check_limits(include_buffer=False)
                continue
            
            # Blank line - end of message
            if not self._pending_lines:
                # Extra blank line between messages - not a message, not EOF
                continue
            
            raw_lines, self._pending_lines = self._pending_lines, []
            self._pending_bytes = 0
            _write_trace('RECV', ''.join(raw_lines))
            message = {}
            for raw_line in raw_lines:
                stripped = raw_line.strip()
                if ':' in stripped:
                    key, value = stripped.split(':', 1)
                    message[key.strip()] = value.strip()
            if message:
                return message
            # Only lines without ':' - nothing to return, read on
        
        # Now the buffer holds only an unfinished line of this message - it counts too
        self._check_limits(include_buffer=True)
        return None
    
    def _check_limits(self, include_buffer: bool) -> None:
        """Raise MessageTooLarge if the unfinished message is over the limits.
        
        Args:
            include_buffer: count the buffer too (only when it holds just the
                unfinished line of this message, not next messages)
        """
        size = self._pending_bytes + (len(self._buffer) if include_buffer else 0)
        if len(self._pending_lines) > MAX_MESSAGE_LINES or size > MAX_BUFFER_BYTES:
            raise MessageTooLarge(f"{len(self._pending_lines)} lines, {size} bytes")
    
    def drain(self, timeout: float = 0.3, limit: float = 5.0) -> None:
        """Read what the gateway has already sent, before closing.
        
        Complete events are kept and returned by read_event later, even
        without a connection. Stops when no data comes within timeout and no
        message is half-received, or after limit seconds in total.
        """
        deadline = time.monotonic() + limit
        while self._connected and self._socket and time.monotonic() < deadline:
            try:
                message = self._read_message(timeout=timeout)
            except socket.error:
                break
            if message is None and (self._pending_lines or self._buffer.strip()):
                # A message is half-received: wait for the rest
                continue
            if not message:
                # None - no more data; {} - connection closed
                break
            if 'Event' in message:
                self._early_events.append(message)
    
    def keep_received_events(self) -> None:
        """Keep complete events that are still in the buffer.
        
        Called when the connection is lost or closed: an SMS may already be
        fully received but not parsed yet. read_event returns them later,
        even without a connection.
        """
        try:
            while True:
                message = self._parse_buffered_message()
                if message is None:
                    break
                if 'Event' in message:
                    self._early_events.append(message)
        except MessageTooLarge:
            pass
    
    def has_early_events(self) -> bool:
        """Check if there are received events that read_event has not returned yet."""
        return bool(self._early_events)
    
    def _reset_parser(self) -> None:
        """Clear the parser state (for a new connection).
        
        Early events are kept: they are complete messages (maybe SMS)
        that read_event has not returned yet.
        """
        self.keep_received_events()
        self._buffer = b''
        self._pending_lines = []
        self._pending_bytes = 0
    
    def is_connected(self) -> bool:
        """Check if the client is currently connected.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connected
    
    def send_ping(self) -> bool:
        """Send a Ping command to check if connection is alive.
        
        Returns:
            True if ping successful, False otherwise
        """
        if not self._connected or not self._socket:
            return False
        
        try:
            ping_cmd = "Action: Ping\r\n\r\n"
            logger.debug("Sending Ping to TG100")
            _write_trace('SEND', ping_cmd)
            self._socket.sendall(ping_cmd.encode('utf-8'))
            return True
        except (socket.error, socket.timeout) as e:
            logger.error(f"Failed to send Ping: {e}")
            self._connected = False
            return False
    
    def close(self) -> None:
        """Close the AMI connection."""
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
            self._socket = None
        
        self._connected = False
        self._reset_parser()
        logger.info("Connection closed")
    
    def parse_sms_event(self, event: AMIEvent) -> Optional[SMSMessage]:
        """Parse SMS events (DongleNewSMSBase64 or ReceivedSMS) into an SMSMessage.
        
        Args:
            event: AMIEvent to parse
        
        Returns:
            SMSMessage object if parsing successful, None if event is malformed
        """
        # Support both DongleNewSMSBase64 and ReceivedSMS event types
        if event.event_type not in ['DongleNewSMSBase64', 'ReceivedSMS']:
            return None
        
        try:
            # Log the full event for debugging (DEBUG: SMS text stays out of the journal)
            logger.debug(f"Parsing SMS event: {event.fields}")
            
            # Handle different event formats
            if event.event_type == 'ReceivedSMS':
                # ReceivedSMS format (TG100)
                # Fields: Span, Sender, Content (plain text), Recvtime
                gateway_id = event.fields.get('Span', event.fields.get('commandID', 'unknown'))
                sender = event.fields.get('Sender')
                message = event.fields.get('Content')
                
                # Validate required fields
                missing = []
                if sender is None:
                    missing.append('Sender')
                if message is None:
                    missing.append('Content')
                
                if missing:
                    logger.warning(f"Malformed ReceivedSMS event: missing fields {missing}")
                    logger.warning(f"Available fields: {list(event.fields.keys())}")
                    return None
                
                # Content is already plain text, no decoding needed
                logger.debug(f"Message content: {message}")
                
            else:
                # DongleNewSMSBase64 format (original)
                # Fields: Device, From, Content (Base64 encoded)
                gateway_id = event.fields.get('Device')
                sender = event.fields.get('From')
                content_base64 = event.fields.get('Content')
                
                # Validate all required fields are present
                missing = []
                if gateway_id is None:
                    missing.append('Device')
                if sender is None:
                    missing.append('From')
                if content_base64 is None:
                    missing.append('Content')
                
                if missing:
                    logger.warning(f"Malformed DongleNewSMSBase64 event: missing fields {missing}")
                    logger.warning(f"Available fields: {list(event.fields.keys())}")
                    return None
                
                # Decode Base64 message content
                try:
                    message_bytes = base64.b64decode(content_base64)
                    message = message_bytes.decode('utf-8')
                    logger.debug(f"Decoded message content: {message}")
                except Exception as e:
                    logger.warning(f"Failed to decode Base64 content: {e}")
                    return None
            
            # Create SMSMessage with current timestamp
            sms = SMSMessage(
                gateway_id=str(gateway_id),
                sender=sender,
                message=message
            )
            
            logger.info(f"Received SMS from {sender} via {gateway_id}")
            return sms
            
        except Exception as e:
            logger.warning(f"Error parsing SMS event: {e}")
            return None
