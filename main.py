#!/usr/bin/env python3
"""
TG100 SMS Gateway Service - Main Entry Point

This service connects to a Yeastar TG100 GSM gateway via AMI protocol,
monitors incoming SMS messages, and forwards them to a remote HTTPS API
with cryptographic signatures for authenticity verification.
"""

import sys
from config import Config
from sms_gateway_service import SMSGatewayService
from logging_config import setup_logging, get_logger


def main():
    """Main entry point for the SMS Gateway Service."""
    # Initialize logging system first
    setup_logging()
    
    logger = get_logger(__name__)
    
    try:
        # Load configuration from .env file
        logger.info("Loading configuration from .env file")
        config = Config.load_from_env()
        logger.info("Configuration loaded successfully")
        
        # Initialize the service
        service = SMSGatewayService(config)
        
        # Start the service (connect and authenticate)
        service.start()
        
        # Run the main event loop
        service.run()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt - shutting down gracefully")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
