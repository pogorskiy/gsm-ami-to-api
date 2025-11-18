"""AMI (Asterisk Manager Interface) protocol client for TG100 gateway."""

import socket
import select
import base64
from typing import Dict, Optional
from datetime import datetime, timezone
from sms_message import SMSMessage
from logging_config import get_logger


logger = get_logger(__name__)

# Trace file for AMI protocol debugging
TRACE_FILE = 'trace.log'


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
            self._buffer = b''  # Clear buffer for new connection
            
            # Read and log the AMI banner (with timeout for initial read)
            banner_bytes = b''
            start_time = datetime.now(timezone.utc)
            while b'\n' not in banner_bytes:
                ready = select.select([self._socket], [], [], 1.0)
                if not ready[0]:
                    # Check if we've been waiting too long
                    if (datetime.now(timezone.utc) - start_time).total_seconds() > 10:
                        raise socket.error("Timeout while reading banner")
                    continue
                
                try:
                    chunk = self._socket.recv(1024)
                    if not chunk:
                        raise socket.error("Connection closed while reading banner")
                    banner_bytes += chunk
                except (BlockingIOError, socket.error):
                    continue
            
            banner = banner_bytes.decode('utf-8').split('\n')[0] + '\n'
            _write_trace('RECV', banner)
            
            logger.info(f"Connected to TG100 at {self.host}:{self.port}")
            logger.info(f"TG100 Banner: {banner.strip()}")
            
            return True
        except (socket.error, socket.timeout) as e:
            logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self._connected = False
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
            
            # Trace outgoing command
            _write_trace('SEND', login_cmd)
            
            self._socket.sendall(login_cmd.encode("utf-8"))

            # Wait for Login Response, ignoring Events and other noise
            while True:
                response = self._read_message(timeout=10.0)  # Longer timeout for login
                
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

                # If this is not a Response, but an Event or something else - just ignore and continue reading

        except (socket.error, socket.timeout) as e:
            logger.error(f"Login failed: {e}")
            self._connected = False
            return False
    
    def read_event(self) -> Optional[AMIEvent]:
        """Read and parse the next AMI event from the connection.
        
        Returns:
            AMIEvent object if an event was read, None if no data available (timeout) or not an event
        """
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
        
        Args:
            timeout: Timeout in seconds for select (default 1.0)
        
        Returns:
            Dictionary of key-value pairs if message complete
            None if no data available (timeout) - connection still alive
            Empty dict {} if connection closed (EOF)
        """
        raw_lines = []
        
        try:
            while True:
                # Wait for data with timeout
                ready = select.select([self._socket], [], [], timeout)
                if not ready[0]:
                    # Timeout - no data available, but connection is alive
                    return None
                
                # Read data from socket
                try:
                    chunk = self._socket.recv(4096)
                    if not chunk:
                        # EOF - connection closed
                        if raw_lines:
                            _write_trace('RECV', ''.join(raw_lines))
                        self._connected = False
                        return {}  # Empty dict signals EOF
                    self._buffer += chunk
                except (BlockingIOError, socket.error):
                    # No data available yet (shouldn't happen after select)
                    continue
                
                # Process complete lines
                while b'\n' in self._buffer:
                    line_bytes, self._buffer = self._buffer.split(b'\n', 1)
                    try:
                        line = line_bytes.decode('utf-8') + '\n'
                    except UnicodeDecodeError:
                        continue
                    
                    raw_lines.append(line)
                    line_stripped = line.strip()
                    
                    if not line_stripped:
                        # Blank line - end of message
                        _write_trace('RECV', ''.join(raw_lines))
                        # Parse all collected lines
                        message = {}
                        for raw_line in raw_lines:
                            stripped = raw_line.strip()
                            if stripped and ':' in stripped:
                                key, value = stripped.split(':', 1)
                                message[key.strip()] = value.strip()
                        return message
                    
        except socket.error as e:
            logger.error(f"Socket error in _read_message: {e}")
            if raw_lines:
                _write_trace('RECV', ''.join(raw_lines))
            self._connected = False
            return {}  # Empty dict signals error
    
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
        self._buffer = b''  # Clear buffer on close
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
            # Log the full event for debugging
            logger.info(f"Parsing SMS event: {event.fields}")
            
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
                logger.info(f"Message content: {message}")
                
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
                    logger.info(f"Decoded message content: {message}")
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
