"""Configuration management for TG100 SMS Gateway Service."""

import os
import sys
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration for SMS Gateway Service."""
    
    # TG100 connection parameters
    tg100_host: str
    tg100_port: int
    tg100_username: str
    tg100_password: str
    
    # Remote API parameters
    api_url: str
    api_bearer_token: str
    
    # Security
    hmac_secret_key: str
    
    # Operational parameters
    initial_retry_delay: int = 5
    max_retry_delay: int = 60
    ping_interval: int = 300  # Keepalive ping interval in seconds (0 to disable)
    
    @staticmethod
    def load_from_env() -> 'Config':
        """
        Load configuration from .env file.
        
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
        
        # Required environment variables
        required_vars = {
            'TG100_HOST': 'TG100 host address',
            'TG100_PORT': 'TG100 port number',
            'TG100_USERNAME': 'TG100 username',
            'TG100_PASSWORD': 'TG100 password',
            'API_URL': 'Remote API URL',
            'API_BEARER_TOKEN': 'API Bearer token',
            'HMAC_SECRET_KEY': 'HMAC secret key',
        }
        
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
        api_url = os.getenv('API_URL')
        if not api_url.startswith('https://') and not api_url.startswith('http://'):
            sys.stderr.write(f"Error: API_URL must start with http:// or https://, got '{api_url}'\n")
            sys.exit(1)
        
        # Parse optional parameters with defaults
        initial_retry_delay = 5
        max_retry_delay = 60
        ping_interval = 300
        
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
        
        return Config(
            tg100_host=os.getenv('TG100_HOST'),
            tg100_port=tg100_port,
            tg100_username=os.getenv('TG100_USERNAME'),
            tg100_password=os.getenv('TG100_PASSWORD'),
            api_url=api_url,
            api_bearer_token=os.getenv('API_BEARER_TOKEN'),
            hmac_secret_key=os.getenv('HMAC_SECRET_KEY'),
            initial_retry_delay=initial_retry_delay,
            max_retry_delay=max_retry_delay,
            ping_interval=ping_interval,
        )
