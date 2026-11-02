#!/usr/bin/env python3
"""
TG100 SMS Gateway Service - Main Entry Point

This service connects to a Yeastar TG100 GSM gateway via AMI protocol,
monitors incoming SMS messages, and forwards them to Telegram.

Delivery modes:
    telegram (default) - send SMS straight to Telegram Bot API
    relay              - legacy: send signed SMS to the relay server,
                         which forwards it to Telegram

Usage:
    python main.py                # mode from DELIVERY_MODE in .env (default: telegram)
    python main.py --mode relay   # legacy mode via relay server
"""

import argparse
import os
import sys
from config import Config, DELIVERY_MODES
from sms_gateway_service import SMSGatewayService
from logging_config import setup_logging, get_logger, restrict_permissions


def parse_args(argv=None):
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="TG100 SMS Gateway Service")
    parser.add_argument(
        '--mode',
        choices=DELIVERY_MODES,
        help="Delivery mode (overrides DELIVERY_MODE in .env): "
             "telegram - straight to Telegram, relay - via relay server",
    )
    return parser.parse_args(argv)


def main():
    """Main entry point for the SMS Gateway Service."""
    args = parse_args()
    
    # Logs, trace and the queue database contain SMS texts: new files are readable only by the owner,
    # existing ones are fixed too
    os.umask(0o077)
    
    # Initialize logging system first
    setup_logging()
    restrict_permissions('sms_gateway.log*', 'trace.log', 'queue.db*')
    
    logger = get_logger(__name__)
    
    try:
        # Load configuration from .env file
        logger.info("Loading configuration from .env file")
        config = Config.load_from_env(delivery_mode=args.mode)
        logger.info(f"Configuration loaded successfully (delivery mode: {config.delivery_mode})")
        
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
