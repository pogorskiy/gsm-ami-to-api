"""Main SMS Gateway Service orchestrator."""

import sys
import time
from enum import Enum
from typing import Optional
from config import Config
from ami_client import AMIClient
from message_queue import MessageQueue
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
        
        self.message_queue = MessageQueue()
        
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
        
        # Application-level keepalive tracking (optional, 0 to disable)
        self._last_ping_time = 0
        self._ping_interval = config.ping_interval
        
        logger.info(f"SMS Gateway Service initialized (delivery mode: {config.delivery_mode})")
    
    def start(self) -> bool:
        """Start the service by connecting and authenticating with TG100.
        
        This method performs the initial connection and authentication sequence.
        If either fails, the service will log an error and exit.
        
        Returns:
            True if startup successful, False otherwise
        """
        logger.info("Starting SMS Gateway Service")
        
        # Attempt initial connection
        self.state = ServiceState.CONNECTING
        if not self.ami_client.connect():
            logger.error("Failed to establish initial connection to TG100")
            sys.exit(1)
        
        # Attempt authentication
        self.state = ServiceState.LOGGING_IN
        if not self.ami_client.login():
            logger.error("Failed to authenticate with TG100")
            self.ami_client.close()
            sys.exit(1)
        
        # Transition to monitoring state
        self.state = ServiceState.MONITORING
        logger.info("Service started successfully - entering monitoring state")
        return True

    def run(self) -> None:
        """Main event loop for processing SMS messages.
        
        This method continuously:
        1. Reads AMI events from TG100
        2. Processes incoming SMS messages
        3. Attempts to deliver queued messages to the remote API
        4. Handles connection failures with reconnection logic
        
        The loop runs indefinitely until interrupted or a fatal error occurs.
        """
        logger.info("Entering main event loop")
        
        while True:
            try:
                # Check if we need to reconnect
                if not self.ami_client.is_connected():
                    if not self._reconnect():
                        # Reconnection failed, continue trying
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
    
    def _process_queue(self) -> None:
        """Process the message delivery queue.
        
        Attempts to deliver the first message in the queue. On success or
        permanent failure, removes the message. On temporary failure, keeps
        the message and implements exponential backoff for retries.
        """
        if self.message_queue.is_empty():
            # Reset retry counter when queue is empty
            self._delivery_retry_count = 0
            return
        
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
                return
        
        # Peek at first message without removing it
        item = self.message_queue.peek()
        if item is None:
            return
        
        message, signature = item
        
        # Attempt delivery
        self._last_delivery_attempt = current_time
        result = self.api_client.send_message(message, signature)
        
        if result is True:
            # Success - remove from queue
            self.message_queue.dequeue()
            logger.info(f"Message delivered successfully (queue size: {self.message_queue.size()})")
            self._delivery_retry_count = 0
            
        elif result is False:
            # Permanent failure - remove from queue
            self.message_queue.dequeue()
            logger.error(f"Message delivery failed permanently - discarding (queue size: {self.message_queue.size()})")
            self._delivery_retry_count = 0
            
        else:  # result is None
            # Temporary failure - keep in queue and increment retry counter
            self._delivery_retry_count += 1
            next_delay = calculate_exponential_backoff(
                self._delivery_retry_count,
                self.config.initial_retry_delay,
                self.config.max_retry_delay
            )
            logger.info(f"Message delivery failed temporarily - will retry in {next_delay} seconds")

    def _reconnect(self) -> bool:
        """Handle reconnection to TG100 with exponential backoff.
        
        Attempts to reconnect and re-authenticate with TG100 using exponential
        backoff. Continues indefinitely until successful.
        
        Returns:
            True if reconnection successful, False if should retry later
        """
        if self.state != ServiceState.RECONNECTING:
            # First reconnection attempt
            self.state = ServiceState.RECONNECTING
            self._reconnect_attempt = 0
            logger.error("Connection lost - entering reconnection mode")
        
        # Calculate backoff delay
        delay = calculate_exponential_backoff(
            self._reconnect_attempt,
            self.config.initial_retry_delay,
            self.config.max_retry_delay
        )
        
        logger.info(f"Attempting reconnection (attempt {self._reconnect_attempt + 1}, delay: {delay}s)")
        
        # Wait before attempting
        time.sleep(delay)
        
        # Close any existing connection
        self.ami_client.close()
        
        # Attempt to connect
        if not self.ami_client.connect():
            logger.error("Reconnection attempt failed - will retry")
            self._reconnect_attempt += 1
            return False
        
        # Attempt to authenticate
        if not self.ami_client.login():
            logger.error("Re-authentication failed - will retry")
            self.ami_client.close()
            self._reconnect_attempt += 1
            return False
        
        # Success!
        logger.info("Reconnection successful - resuming monitoring")
        self.state = ServiceState.MONITORING
        self._reconnect_attempt = 0
        return True
    
    def stop(self) -> None:
        """Stop the service and clean up resources."""
        logger.info("Stopping SMS Gateway Service")
        self.ami_client.close()
        logger.info("Service stopped")
