"""Centralized logging configuration for TG100 SMS Gateway Service."""

import logging
import logging.handlers
import sys
import os
import re
from pathlib import Path


# Telegram bot token inside a Bot API URL: /bot123456:ABC-def...
_BOT_TOKEN_RE = re.compile(r'bot\d+:[A-Za-z0-9_-]+')

# Third-party loggers that write request URLs (with the bot token) at DEBUG
NOISY_LOGGERS = ('urllib3', 'requests')


def redact_secrets(text: str) -> str:
    """Replace Telegram bot tokens in text with a placeholder."""
    return _BOT_TOKEN_RE.sub('bot<redacted>', text)


def restrict_permissions(*patterns: str) -> None:
    """Make existing files readable only by the owner (0600).

    Args:
        patterns: File names or glob patterns, e.g. 'sms_gateway.log*'
    """
    for pattern in patterns:
        for path in Path().glob(pattern):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


class ComponentFormatter(logging.Formatter):
    """Custom formatter that adds component name to log messages.
    
    Format: [TIMESTAMP] [LEVEL] [COMPONENT] Message
    """
    
    def format(self, record):
        """Format the log record with timestamp, level, and component.
        
        Args:
            record: LogRecord to format
        
        Returns:
            Formatted log string
        """
        # Extract component name from logger name
        # For example: "ami_client" -> "AMIClient"
        component = self._get_component_name(record.name)
        
        # Format timestamp in ISO 8601 format
        timestamp = self.formatTime(record, datefmt='%Y-%m-%dT%H:%M:%SZ')
        
        # Format: [TIMESTAMP] [LEVEL] [COMPONENT] Message
        formatted = f"[{timestamp}] [{record.levelname}] [{component}] {record.getMessage()}"
        
        # Add exception info if present
        if record.exc_info:
            formatted += '\n' + self.formatException(record.exc_info)
        
        # Never write the bot token, even if a library puts it in a message or traceback
        return redact_secrets(formatted)
    
    def _get_component_name(self, logger_name: str) -> str:
        """Convert logger name to component name.
        
        Args:
            logger_name: Name of the logger (e.g., "ami_client", "__main__")
        
        Returns:
            Component name for display (e.g., "AMIClient", "Main")
        """
        # Handle special cases
        if logger_name == '__main__':
            return 'Main'
        
        # Special handling for known acronyms
        acronyms = {'ami': 'AMI', 'api': 'API', 'sms': 'SMS'}
        
        # Convert snake_case to PascalCase
        # e.g., "ami_client" -> "AMIClient"
        parts = logger_name.split('_')
        converted_parts = []
        for word in parts:
            # Check if word is a known acronym
            if word.lower() in acronyms:
                converted_parts.append(acronyms[word.lower()])
            else:
                converted_parts.append(word.capitalize())
        
        return ''.join(converted_parts)


def setup_logging(log_file: str = 'sms_gateway.log', 
                  max_bytes: int = 10 * 1024 * 1024,  # 10 MB
                  backup_count: int = 5,
                  console_level: int = logging.INFO,
                  file_level: int = logging.DEBUG) -> None:
    """Configure logging for the SMS Gateway Service.
    
    Sets up both console and file logging with rotation. The log format is:
    [TIMESTAMP] [LEVEL] [COMPONENT] Message
    
    The logging level can be controlled via LOG_LEVEL environment variable:
    - DEBUG: Most verbose, shows all messages including protocol details
    - INFO: Normal operations (default for console)
    - WARNING: Only warnings and errors
    - ERROR: Only errors
    
    Args:
        log_file: Path to the log file
        max_bytes: Maximum size of log file before rotation (default: 10 MB)
        backup_count: Number of backup log files to keep (default: 5)
        console_level: Logging level for console output (default: INFO)
        file_level: Logging level for file output (default: DEBUG)
    """
    # Check for LOG_LEVEL environment variable to override defaults
    log_level_str = os.getenv('LOG_LEVEL', '').upper()
    if log_level_str:
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR
        }
        if log_level_str in level_map:
            console_level = level_map[log_level_str]
            file_level = level_map[log_level_str]
    
    # Create logs directory if it doesn't exist
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create custom formatter
    formatter = ComponentFormatter()
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels, handlers will filter
    
    # Remove any existing handlers
    root_logger.handlers.clear()
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Do not log HTTP request URLs: they contain the Telegram bot token
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    
    # Log startup message
    logging.info("Logging system initialized")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a specific component.
    
    Args:
        name: Name of the component (typically __name__)
    
    Returns:
        Logger instance configured with the service's formatting
    """
    return logging.getLogger(name)
