"""Main SMS Gateway Service orchestrator."""

import sys
import time
from enum import Enum
from typing import Optional
from config import Config
from ami_client import AMIClient
from message_queue import MessageQueue, SQLiteMessageQueue
from api_delivery_client import APIDeliveryClient
from telegram_delivery_client import TelegramDeliveryClient
from retry_logic import calculate_exponential_backoff
from logging_config import get_logger


logger = get_logger(__name__)


class ServiceState(Enum):
    """Service operational states."""
    INIT = "INIT"
    CONNECTING = "CONNECTING"
    LOGGING_IN = "LOGGING_IN"
    MONITORING = "MONITORING"
    RECONNECTING = "RECONNECTING"


class SMSGatewayService:
    """Main orchestrator for the TG100 SMS Gateway Service.
    
    This service coordinates all components to:
    1. Connect and authenticate with TG100 via AMI
    2. Monitor incoming SMS messages
    3. Queue and deliver messages to remote API with retry logic
    4. Handle connection failures with automatic reconnection
    
    Attributes:
        config: Service configuration
        ami_client: AMI protocol client for TG100 communication
        message_queue: FIFO queue for pending message deliveries
        api_client: Client for delivering messages to remote API
        state: Current operational state
    """
    
    def __init__(self, config: Config):
        """Initialize the SMS Gateway Service.
        
        Args:
            config: Configuration object with all required parameters
        """
        self.config = config
        self.state = ServiceState.INIT
        
        # Initialize components
        self.ami_client = AMIClient(
            host=config.tg100_host,
            port=config.tg100_port,
            username=config.tg100_username,
            password=config.tg100_password
        )
        
        self.message_queue = self._create_queue(config.queue_db_path)
        
        # Delivery client: straight to Telegram, or legacy relay server
        if config.delivery_mode == 'relay':
            self.api_client = APIDeliveryClient(
                api_url=config.api_url,
                bearer_token=config.api_bearer_token
            )
        else:
            self.api_client = TelegramDeliveryClient(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                timeout=config.telegram_request_timeout
            )
        
        # Retry tracking for API delivery
        self._delivery_retry_count = 0
        self._last_delivery_attempt = 0
        
        # Reconnection schedule (no sleeping in the main loop)
        self._reconnect_attempt = 0
        # (time.monotonic(): a change of the system clock does not break it)
        self._next_reconnect_time = 0.0
        self._last_connect_time = float('-inf')
        
        # Set by request_stop() (SIGTERM): the main loop ends after the current step
        self._stop_requested = False
        
        # Application-level keepalive tracking (optional, 0 to disable)
        self._last_ping_time = 0
        self._ping_interval = config.ping_interval
        
        logger.info(f"SMS Gateway Service initialized (delivery mode: {config.delivery_mode})")
    
    @staticmethod
    def _create_queue(path: Optional[str]):
        """Create the message queue: on disk if possible, otherwise in memory.
        
        Args:
            path: SQLite file for the queue; None or empty - in-memory queue
        """
        if not path:
            logger.warning("QUEUE_DB_PATH not set - queue is kept in memory, a restart loses it")
            return MessageQueue()
        queue = SQLiteMessageQueue(path)
        if not queue.is_open():
            # Already logged; the queue keeps messages in memory and retries opening
            return queue
        pending = queue.size()
        if pending:
            logger.info(f"Queue database opened: {pending} message(s) waiting for delivery")
        else:
            logger.info("Queue database opened")
        return queue
    
    def start(self) -> bool:
        """Start the service by connecting and authenticating with TG100.
        
        This method performs the initial connection and authentication sequence.
        If either fails, the service will log an error and exit - unless SMS
        are waiting for delivery (received during login, or kept in the queue
        database from before a restart): then it returns False and run()
        delivers them and reconnects.
        
        Returns:
            True if startup successful, False otherwise
        """
        logger.info("Starting SMS Gateway Service")
        
        # Attempt initial connection
        self.state = ServiceState.CONNECTING
        if not self.ami_client.connect():
            logger.error("Failed to establish initial connection to TG100")
            if not self.message_queue.is_empty():
                logger.error("SMS waiting in the queue - delivering them and reconnecting")
                return False
            sys.exit(1)
        
        # Attempt authentication
        self.state = ServiceState.LOGGING_IN
        if not self.ami_client.login():
            logger.error("Failed to authenticate with TG100")
            self.ami_client.close()
            if self.ami_client.has_early_events() or not self.message_queue.is_empty():
                # SMS waiting: do not exit, run() delivers them and reconnects
                logger.error("SMS received before the failure - delivering them and reconnecting")
                return False
            sys.exit(1)
        
        # Transition to monitoring state
        self.state = ServiceState.MONITORING
        self._last_connect_time = time.monotonic()
        logger.info("Service started successfully - entering monitoring state")
        return True

    def run(self) -> None:
        """Main event loop for processing SMS messages.
        
        This method continuously:
        1. Reads AMI events from TG100
        2. Processes incoming SMS messages
        3. Attempts to deliver queued messages to the remote API
        4. Handles connection failures with reconnection logic
        
        The loop runs until interrupted or request_stop() is called.
        """
        logger.info("Entering main event loop")
        
        while not self._stop_requested:
            try:
                # Check if we need to reconnect
                if not self.ami_client.is_connected():
                    # First deliver all that was already received
                    self.ami_client.keep_received_events()
                    self._process_early_events()
                    # Reconnect has priority: deliver more only while it is not due
                    while self._process_queue():
                        if self._reconnect_due():
                            break
                    if not self._reconnect():
                        # Not connected yet: short pause, then queue and reconnect again
                        time.sleep(0.1)
                        continue
                
                # Optional: Send keepalive ping (if enabled)
                if self._ping_interval > 0:
                    current_time = time.time()
                    if current_time - self._last_ping_time >= self._ping_interval:
                        if self.ami_client.send_ping():
                            self._last_ping_time = current_time
                            logger.debug(f"Sent keepalive ping to TG100")
                        else:
                            logger.warning("Failed to send keepalive ping")
                
                # Read next AMI event (with timeout)
                event = self.ami_client.read_event()
                
                if event is None:
                    # No event available (timeout or not an event message)
                    if not self.ami_client.is_connected():
                        logger.error("Connection lost during event reading")
                        continue
                    # No event data, continue to queue processing
                else:
                    # Process SMS events (both DongleNewSMSBase64 and ReceivedSMS)
                    if event.event_type in ['DongleNewSMSBase64', 'ReceivedSMS']:
                        self._process_sms_event(event)
                    # Log Pong responses
                    elif event.event_type == 'Pong' or (event.fields.get('Response') == 'Success' and event.fields.get('Ping') == 'Pong'):
                        logger.debug("Received Pong response from TG100")
                
                # Process message queue
                self._process_queue()
                
                # Small sleep to prevent tight loop
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                logger.info("Received shutdown signal")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                # Continue running despite errors
                time.sleep(1)
        else:
            logger.info("Stop requested")
            self.stop()
    
    def request_stop(self) -> None:
        """Ask the main loop to stop (safe to call from a signal handler).
        
        The loop finishes its current step, so an SMS being parsed or queued
        is not cut in the middle.
        """
        self._stop_requested = True
    
    def _process_early_events(self) -> None:
        """Queue SMS events that were received before the connection was lost."""
        while self.ami_client.has_early_events():
            event = self.ami_client.read_event()
            if event is not None and event.event_type in ['DongleNewSMSBase64', 'ReceivedSMS']:
                self._process_sms_event(event)
    
    def _process_sms_event(self, event) -> None:
        """Process an SMS event (DongleNewSMSBase64 or ReceivedSMS).
        
        Extracts SMS data, creates an SMSMessage object, computes signature,
        and adds it to the delivery queue. The signature is computed only in relay mode.
        
        Args:
            event: AMIEvent of type DongleNewSMSBase64 or ReceivedSMS
        """
        # Parse the event into an SMSMessage
        sms_message = self.ami_client.parse_sms_event(event)
        
        if sms_message is None:
            # Parsing failed (already logged by ami_client)
            return
        
        # Compute signature (only the relay server checks it)
        signature = None
        if self.config.delivery_mode == 'relay':
            signature = sms_message.compute_signature(self.config.hmac_secret_key)
        
        # Add to queue
        self.message_queue.enqueue(sms_message, signature)
        logger.info(f"Added message to queue (queue size: {self.message_queue.size()})")
    
    def _process_queue(self) -> bool:
        """Process the message delivery queue.
        
        Attempts to deliver the first message in the queue. On success or
        permanent failure, removes the message. On temporary failure, keeps
        the message and implements exponential backoff for retries.
        
        Returns:
            True if a message was removed from the queue (call again for the next one)
        """
        if self.message_queue.is_empty():
            # Reset retry counter when queue is empty
            self._delivery_retry_count = 0
            return False
        
        # Check if we need to wait before retry
        current_time = time.time()
        if self._last_delivery_attempt > 0:
            # Calculate required delay
            delay = calculate_exponential_backoff(
                self._delivery_retry_count,
                self.config.initial_retry_delay,
                self.config.max_retry_delay
            )
            
            time_since_last_attempt = current_time - self._last_delivery_attempt
            if time_since_last_attempt < delay:
                # Not enough time has passed, wait
                return False
        
        # Peek at first message without removing it
        item = self.message_queue.peek()
        if item is None:
            return False
        
        message, signature = item
        if self.config.delivery_mode == 'relay':
            # Sign with the current key: the message may be queued in telegram
            # mode (no signature) or before a key change
            signature = message.compute_signature(self.config.hmac_secret_key)
        
        # Attempt delivery
        self._last_delivery_attempt = current_time
        result = self.api_client.send_message(message, signature)
        
        if result is True:
            # Success - remove from queue
            self.message_queue.dequeue()
            logger.info(f"Message delivered successfully (queue size: {self.message_queue.size()})")
            self._delivery_retry_count = 0
            self._last_delivery_attempt = 0  # next message goes without a pause
            return True
            
        elif result is False:
            # Permanent failure - set aside (kept on disk for a manual retry)
            self.message_queue.mark_failed("permanent delivery failure")
            logger.error(f"Message delivery failed permanently - set aside as failed (queue size: {self.message_queue.size()})")
            self._delivery_retry_count = 0
            self._last_delivery_attempt = 0
            return True
            
        else:  # result is None
            # Temporary failure - keep in queue and increment retry counter
            self.message_queue.record_attempt("temporary delivery failure")
            self._delivery_retry_count += 1
            next_delay = calculate_exponential_backoff(
                self._delivery_retry_count,
                self.config.initial_retry_delay,
                self.config.max_retry_delay
            )
            logger.info(f"Message delivery failed temporarily - will retry in {next_delay} seconds")
            return False

    def _reconnect_due(self) -> bool:
        """Check if a reconnection attempt should be made now."""
        if self.state != ServiceState.RECONNECTING:
            return True
        return time.monotonic() >= self._next_reconnect_time
    
    def _reconnect(self) -> bool:
        """Handle reconnection to TG100 with exponential backoff.
        
        Does not sleep: if the next attempt is not due yet, returns False at
        once, so the main loop keeps delivering the queue between attempts.
        
        Returns:
            True if reconnection successful, False if should retry later
        """
        now = time.monotonic()
        if self.state != ServiceState.RECONNECTING:
            # First reconnection attempt: at once, but not more often than
            # initial_retry_delay after the last successful connect
            self.state = ServiceState.RECONNECTING
            self._reconnect_attempt = 0
            self._next_reconnect_time = max(now, self._last_connect_time + self.config.initial_retry_delay)
            logger.error("Connection lost - entering reconnection mode")
        
        if now < self._next_reconnect_time:
            return False
        
        logger.info(f"Attempting reconnection (attempt {self._reconnect_attempt + 1})")
        
        # Close any existing connection
        self.ami_client.close()
        
        connected = self.ami_client.connect()
        if not connected:
            logger.error("Reconnection attempt failed - will retry")
        elif not self.ami_client.login():
            logger.error("Re-authentication failed - will retry")
            self.ami_client.close()
            connected = False
        
        if not connected:
            delay = calculate_exponential_backoff(
                self._reconnect_attempt,
                self.config.initial_retry_delay,
                self.config.max_retry_delay
            )
            self._reconnect_attempt += 1
            self._next_reconnect_time = time.monotonic() + delay
            logger.info(f"Next reconnection attempt in {delay}s")
            return False
        
        # Success!
        logger.info("Reconnection successful - resuming monitoring")
        self.state = ServiceState.MONITORING
        self._reconnect_attempt = 0
        self._last_connect_time = time.monotonic()
        return True
    
    def stop(self) -> None:
        """Stop the service and clean up resources."""
        logger.info("Stopping SMS Gateway Service")
        try:
            # SMS the gateway has already sent may still be in the socket
            self.ami_client.drain()
        except Exception as e:
            logger.error(f"Could not read the rest from TG100 on stop: {e}")
        self.ami_client.close()
        try:
            # SMS already received from the gateway but not queued yet go to the
            # queue (on disk) - they are delivered after the restart
            self._process_early_events()
        except Exception as e:
            logger.error(f"Could not queue received SMS on stop: {e}")
        self.message_queue.close()
        logger.info("Service stopped")
