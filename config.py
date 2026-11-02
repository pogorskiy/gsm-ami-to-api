"""Configuration management for TG100 SMS Gateway Service."""

import os
import sys
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv


# Delivery modes:
#   telegram - send SMS straight to Telegram Bot API (default)
#   relay    - legacy: POST signed SMS to the relay server (gsm-to-telegram-bot)
DELIVERY_MODES = ('telegram', 'relay')
DEFAULT_DELIVERY_MODE = 'telegram'


@dataclass
class Config:
    """Configuration for SMS Gateway Service."""
    
    # TG100 connection parameters
    tg100_host: str
    tg100_port: int
    tg100_username: str
    tg100_password: str
    
    # Delivery mode: 'telegram' or 'relay'
    delivery_mode: str = DEFAULT_DELIVERY_MODE
    
    # Remote API parameters (relay mode)
    api_url: Optional[str] = None
    api_bearer_token: Optional[str] = None
    
    # Security (relay mode)
    hmac_secret_key: Optional[str] = None
    
    # Telegram parameters (telegram mode)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_request_timeout: int = 15
    
    # Operational parameters
    initial_retry_delay: int = 5
    max_retry_delay: int = 60
    ping_interval: int = 300  # Keepalive ping interval in seconds (0 to disable)
    
    @staticmethod
    def load_from_env(delivery_mode: Optional[str] = None) -> 'Config':
        """
        Load configuration from .env file.
        
        Args:
            delivery_mode: Overrides DELIVERY_MODE from .env ('telegram' or 'relay')
        
        Returns:
            Config: Loaded and validated configuration
            
        Raises:
            SystemExit: If configuration is invalid or .env file is missing
        """
        # Try to load .env file
        if not os.path.exists('.env'):
            sys.stderr.write("Error: .env file not found. Please create one based on .env.example\n")
            sys.exit(1)
        
        load_dotenv('.env', override=True)
        
        # Resolve delivery mode: CLI argument > DELIVERY_MODE > default
        mode = (delivery_mode or os.getenv('DELIVERY_MODE') or DEFAULT_DELIVERY_MODE).strip().lower()
        if mode not in DELIVERY_MODES:
            sys.stderr.write(f"Error: DELIVERY_MODE must be one of {', '.join(DELIVERY_MODES)}, got '{mode}'\n")
            sys.exit(1)
        
        # Required environment variables
        required_vars = {
            'TG100_HOST': 'TG100 host address',
            'TG100_PORT': 'TG100 port number',
            'TG100_USERNAME': 'TG100 username',
            'TG100_PASSWORD': 'TG100 password',
        }
        if mode == 'relay':
            required_vars.update({
                'API_URL': 'Remote API URL',
                'API_BEARER_TOKEN': 'API Bearer token',
                'HMAC_SECRET_KEY': 'HMAC secret key',
            })
        else:
            required_vars.update({
                'TELEGRAM_BOT_TOKEN': 'Telegram bot token',
                'TELEGRAM_CHAT_ID': 'Telegram chat ID',
            })
        
        # Check for missing required variables
        missing_vars = []
        for var_name, description in required_vars.items():
            value = os.getenv(var_name)
            if value is None or value.strip() == '':
                missing_vars.append(f"{var_name} ({description})")
        
        if missing_vars:
            sys.stderr.write(f"Error: Missing required environment variables:\n")
            for var in missing_vars:
                sys.stderr.write(f"  - {var}\n")
            sys.exit(1)
        
        # Validate and parse TG100_PORT
        try:
            tg100_port = int(os.getenv('TG100_PORT'))
            if tg100_port <= 0 or tg100_port > 65535:
                sys.stderr.write(f"Error: TG100_PORT must be between 1 and 65535, got {tg100_port}\n")
                sys.exit(1)
        except ValueError:
            sys.stderr.write(f"Error: TG100_PORT must be a valid integer, got '{os.getenv('TG100_PORT')}'\n")
            sys.exit(1)
        
        # Validate API_URL format
        api_url = os.getenv('API_URL') or None
        if mode == 'relay' and not api_url.startswith('https://') and not api_url.startswith('http://'):
            sys.stderr.write(f"Error: API_URL must start with http:// or https://, got '{api_url}'\n")
            sys.exit(1)
        
        # Parse optional parameters with defaults
        initial_retry_delay = 5
        max_retry_delay = 60
        ping_interval = 300
        telegram_request_timeout = 15
        
        if os.getenv('INITIAL_RETRY_DELAY'):
            try:
                initial_retry_delay = int(os.getenv('INITIAL_RETRY_DELAY'))
                if initial_retry_delay <= 0:
                    sys.stderr.write(f"Error: INITIAL_RETRY_DELAY must be positive, got {initial_retry_delay}\n")
                    sys.exit(1)
            except ValueError:
                sys.stderr.write(f"Error: INITIAL_RETRY_DELAY must be a valid integer, got '{os.getenv('INITIAL_RETRY_DELAY')}'\n")
                sys.exit(1)
        
        if os.getenv('MAX_RETRY_DELAY'):
            try:
                max_retry_delay = int(os.getenv('MAX_RETRY_DELAY'))
                if max_retry_delay <= 0:
                    sys.stderr.write(f"Error: MAX_RETRY_DELAY must be positive, got {max_retry_delay}\n")
                    sys.exit(1)
            except ValueError:
                sys.stderr.write(f"Error: MAX_RETRY_DELAY must be a valid integer, got '{os.getenv('MAX_RETRY_DELAY')}'\n")
                sys.exit(1)
        
        if os.getenv('PING_INTERVAL'):
            try:
                ping_interval = int(os.getenv('PING_INTERVAL'))
                if ping_interval < 0:
                    sys.stderr.write(f"Error: PING_INTERVAL must be non-negative, got {ping_interval}\n")
                    sys.exit(1)
            except ValueError:
                sys.stderr.write(f"Error: PING_INTERVAL must be a valid integer, got '{os.getenv('PING_INTERVAL')}'\n")
                sys.exit(1)
        
        if os.getenv('TELEGRAM_REQUEST_TIMEOUT'):
            try:
                telegram_request_timeout = int(os.getenv('TELEGRAM_REQUEST_TIMEOUT'))
                if telegram_request_timeout <= 0:
                    sys.stderr.write(f"Error: TELEGRAM_REQUEST_TIMEOUT must be positive, got {telegram_request_timeout}\n")
                    sys.exit(1)
            except ValueError:
                sys.stderr.write(f"Error: TELEGRAM_REQUEST_TIMEOUT must be a valid integer, got '{os.getenv('TELEGRAM_REQUEST_TIMEOUT')}'\n")
                sys.exit(1)
        
        return Config(
            tg100_host=os.getenv('TG100_HOST'),
            tg100_port=tg100_port,
            tg100_username=os.getenv('TG100_USERNAME'),
            tg100_password=os.getenv('TG100_PASSWORD'),
            delivery_mode=mode,
            api_url=api_url,
            api_bearer_token=os.getenv('API_BEARER_TOKEN') or None,
            hmac_secret_key=os.getenv('HMAC_SECRET_KEY') or None,
            telegram_bot_token=os.getenv('TELEGRAM_BOT_TOKEN') or None,
            telegram_chat_id=os.getenv('TELEGRAM_CHAT_ID') or None,
            telegram_request_timeout=telegram_request_timeout,
            initial_retry_delay=initial_retry_delay,
            max_retry_delay=max_retry_delay,
            ping_interval=ping_interval,
        )
