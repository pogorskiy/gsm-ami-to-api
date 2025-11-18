"""Unit tests for logging configuration."""

import logging
import tempfile
import os
from pathlib import Path
from logging_config import setup_logging, get_logger, ComponentFormatter


def test_component_formatter_basic():
    """Test ComponentFormatter formats messages correctly."""
    formatter = ComponentFormatter()
    
    # Create a log record
    record = logging.LogRecord(
        name='ami_client',
        level=logging.INFO,
        pathname='',
        lineno=0,
        msg='Test message',
        args=(),
        exc_info=None
    )
    
    formatted = formatter.format(record)
    
    # Check format: [TIMESTAMP] [LEVEL] [COMPONENT] Message
    assert '[INFO]' in formatted
    assert '[AMIClient]' in formatted
    assert 'Test message' in formatted
    assert formatted.count('[') == 3  # Three bracketed sections


def test_component_formatter_main_module():
    """Test ComponentFormatter handles __main__ module."""
    formatter = ComponentFormatter()
    
    record = logging.LogRecord(
        name='__main__',
        level=logging.ERROR,
        pathname='',
        lineno=0,
        msg='Error occurred',
        args=(),
        exc_info=None
    )
    
    formatted = formatter.format(record)
    
    assert '[ERROR]' in formatted
    assert '[Main]' in formatted
    assert 'Error occurred' in formatted


def test_component_formatter_snake_case_conversion():
    """Test ComponentFormatter converts snake_case to PascalCase."""
    formatter = ComponentFormatter()
    
    test_cases = [
        ('api_delivery_client', 'APIDeliveryClient'),
        ('message_queue', 'MessageQueue'),
        ('retry_logic', 'RetryLogic'),
        ('sms_message', 'SMSMessage'),
    ]
    
    for logger_name, expected_component in test_cases:
        record = logging.LogRecord(
            name=logger_name,
            level=logging.INFO,
            pathname='',
            lineno=0,
            msg='Test',
            args=(),
            exc_info=None
        )
        
        formatted = formatter.format(record)
        assert f'[{expected_component}]' in formatted


def test_setup_logging_creates_log_file():
    """Test setup_logging creates log file and directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'logs', 'test.log')
        
        setup_logging(log_file=log_file)
        
        # Check log file was created
        assert os.path.exists(log_file)
        
        # Write a test log message
        logger = get_logger('test_module')
        logger.info('Test log message')
        
        # Check log file contains the message
        with open(log_file, 'r') as f:
            content = f.read()
            assert 'Test log message' in content
            assert '[INFO]' in content
            assert '[TestModule]' in content


def test_get_logger_returns_configured_logger():
    """Test get_logger returns a properly configured logger."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        setup_logging(log_file=log_file)
        
        logger = get_logger('my_component')
        
        # Logger should be a logging.Logger instance
        assert isinstance(logger, logging.Logger)
        assert logger.name == 'my_component'


def test_logging_levels():
    """Test different logging levels work correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        setup_logging(log_file=log_file, file_level=logging.DEBUG)
        
        logger = get_logger('test_levels')
        
        logger.debug('Debug message')
        logger.info('Info message')
        logger.warning('Warning message')
        logger.error('Error message')
        
        # Check all messages are in the log file
        with open(log_file, 'r') as f:
            content = f.read()
            assert 'Debug message' in content
            assert 'Info message' in content
            assert 'Warning message' in content
            assert 'Error message' in content
            assert '[DEBUG]' in content
            assert '[INFO]' in content
            assert '[WARNING]' in content
            assert '[ERROR]' in content


def test_log_rotation_configuration():
    """Test that log rotation is configured correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        max_bytes = 1024  # 1 KB for testing
        backup_count = 3
        
        setup_logging(
            log_file=log_file,
            max_bytes=max_bytes,
            backup_count=backup_count
        )
        
        # Get the root logger and check its handlers
        root_logger = logging.getLogger()
        
        # Find the RotatingFileHandler
        file_handler = None
        for handler in root_logger.handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                file_handler = handler
                break
        
        assert file_handler is not None
        assert file_handler.maxBytes == max_bytes
        assert file_handler.backupCount == backup_count


def test_timestamp_format():
    """Test that timestamps are in ISO 8601 format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        setup_logging(log_file=log_file)
        
        logger = get_logger('timestamp_test')
        logger.info('Timestamp test message')
        
        # Read log file and check timestamp format
        with open(log_file, 'r') as f:
            content = f.read()
            # Should contain ISO 8601 format: [YYYY-MM-DDTHH:MM:SSZ]
            import re
            # Pattern for ISO 8601 timestamp in brackets
            pattern = r'\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]'
            assert re.search(pattern, content) is not None
