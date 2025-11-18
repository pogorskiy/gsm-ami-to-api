#!/usr/bin/env python3
"""Demonstration of the logging system for TG100 SMS Gateway Service."""

from logging_config import setup_logging, get_logger
import time


def main():
    """Demonstrate the logging system with various components and levels."""
    
    # Set up logging with a demo log file
    print("Setting up logging system...")
    setup_logging(
        log_file='demo_sms_gateway.log',
        max_bytes=10 * 1024 * 1024,  # 10 MB
        backup_count=5
    )
    
    print("Logging system initialized. Check 'demo_sms_gateway.log' for output.\n")
    
    # Simulate different components logging
    ami_logger = get_logger('ami_client')
    api_logger = get_logger('api_delivery_client')
    queue_logger = get_logger('message_queue')
    main_logger = get_logger('__main__')
    
    # Simulate a typical service lifecycle
    main_logger.info("SMS Gateway Service starting...")
    
    # Connection events
    ami_logger.info("Attempting to connect to TG100 at 192.168.1.100:5038")
    time.sleep(0.1)
    ami_logger.info("Connected to TG100 at 192.168.1.100:5038")
    
    # Authentication events
    ami_logger.info("Authenticating with TG100...")
    time.sleep(0.1)
    ami_logger.info("Authentication successful")
    
    main_logger.info("Service is now monitoring for incoming SMS messages")
    
    # SMS processing events
    ami_logger.info("Received SMS from +1234567890 via dongle0")
    queue_logger.info("Message added to delivery queue (queue size: 1)")
    
    # Successful delivery
    api_logger.info("Successfully delivered message to remote API (HTTP 200)")
    queue_logger.info("Message removed from queue (queue size: 0)")
    
    # Another SMS with temporary failure
    ami_logger.info("Received SMS from +9876543210 via dongle1")
    queue_logger.info("Message added to delivery queue (queue size: 1)")
    
    # Temporary failure
    api_logger.error("Temporary failure delivering message: HTTP 503 - Server Error")
    api_logger.info("Retrying delivery in 5 seconds...")
    
    # Warning for malformed event
    ami_logger.warning("Malformed SMS event: missing fields ['Content']")
    
    # Connection loss and reconnection
    ami_logger.error("Error reading event: [Errno 54] Connection reset by peer")
    main_logger.warning("Connection to TG100 lost, attempting to reconnect...")
    
    time.sleep(0.1)
    ami_logger.info("Reconnection attempt 1...")
    time.sleep(0.1)
    ami_logger.info("Connected to TG100 at 192.168.1.100:5038")
    ami_logger.info("Authentication successful")
    main_logger.info("Reconnection successful, resuming monitoring")
    
    # Successful retry
    api_logger.info("Successfully delivered message to remote API (HTTP 200)")
    queue_logger.info("Message removed from queue (queue size: 0)")
    
    main_logger.info("Service running normally")
    
    print("\nDemo complete! Check 'demo_sms_gateway.log' to see the formatted log output.")
    print("\nLog format: [TIMESTAMP] [LEVEL] [COMPONENT] Message")
    print("\nExample log entries:")
    print("  [2024-01-15T10:30:45Z] [INFO] [AMIClient] Connected to TG100 at 192.168.1.100:5038")
    print("  [2024-01-15T10:30:46Z] [INFO] [AMIClient] Authentication successful")
    print("  [2024-01-15T10:31:00Z] [INFO] [AMIClient] Received SMS from +1234567890 via dongle0")
    print("  [2024-01-15T10:31:01Z] [INFO] [APIDeliveryClient] Successfully delivered message to remote API (HTTP 200)")


if __name__ == '__main__':
    main()
