"""Tests for configuration loading."""

import os
import sys
import tempfile
from pathlib import Path
import pytest
from config import Config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clean environment variables before each test."""
    env_vars = ['TG100_HOST', 'TG100_PORT', 'TG100_USERNAME', 'TG100_PASSWORD', 
                'API_URL', 'API_BEARER_TOKEN', 'HMAC_SECRET_KEY', 
                'INITIAL_RETRY_DELAY', 'MAX_RETRY_DELAY']
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)


def test_load_valid_config(tmp_path, monkeypatch):
    """Test loading a valid configuration."""
    monkeypatch.chdir(tmp_path)
    
    # Create a valid .env file
    (tmp_path / ".env").write_text(
        "TG100_HOST=192.168.1.100\n"
        "TG100_PORT=5038\n"
        "TG100_USERNAME=admin\n"
        "TG100_PASSWORD=secret\n"
        "API_URL=https://api.example.com/sms/incoming\n"
        "API_BEARER_TOKEN=test-token\n"
        "HMAC_SECRET_KEY=test-secret-key\n"
    )
    
    # Load config
    config = Config.load_from_env()
    
    # Verify all fields
    assert config.tg100_host == "192.168.1.100"
    assert config.tg100_port == 5038
    assert config.tg100_username == "admin"
    assert config.tg100_password == "secret"
    assert config.api_url == "https://api.example.com/sms/incoming"
    assert config.api_bearer_token == "test-token"
    assert config.hmac_secret_key == "test-secret-key"
    assert config.initial_retry_delay == 5
    assert config.max_retry_delay == 60


def test_load_config_with_custom_retry_delays(tmp_path, monkeypatch):
    """Test loading configuration with custom retry delays."""
    monkeypatch.chdir(tmp_path)
    
    (tmp_path / ".env").write_text(
        "TG100_HOST=192.168.1.100\n"
        "TG100_PORT=5038\n"
        "TG100_USERNAME=admin\n"
        "TG100_PASSWORD=secret\n"
        "API_URL=https://api.example.com/sms/incoming\n"
        "API_BEARER_TOKEN=test-token\n"
        "HMAC_SECRET_KEY=test-secret-key\n"
        "INITIAL_RETRY_DELAY=10\n"
        "MAX_RETRY_DELAY=120\n"
    )
    
    config = Config.load_from_env()
    
    assert config.initial_retry_delay == 10
    assert config.max_retry_delay == 120


def test_missing_env_file(tmp_path, monkeypatch, capsys):
    """Test error handling when .env file is missing."""
    monkeypatch.chdir(tmp_path)
    
    with pytest.raises(SystemExit) as exc_info:
        Config.load_from_env()
    
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error: .env file not found" in captured.err


def test_missing_required_variable(tmp_path, monkeypatch, capsys):
    """Test error handling when required variable is missing."""
    monkeypatch.chdir(tmp_path)
    
    # Create .env with missing TG100_HOST
    (tmp_path / ".env").write_text(
        "TG100_PORT=5038\n"
        "TG100_USERNAME=admin\n"
        "TG100_PASSWORD=secret\n"
        "API_URL=https://api.example.com/sms/incoming\n"
        "API_BEARER_TOKEN=test-token\n"
        "HMAC_SECRET_KEY=test-secret-key\n"
    )
    
    with pytest.raises(SystemExit) as exc_info:
        Config.load_from_env()
    
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Missing required environment variables" in captured.err
    assert "TG100_HOST" in captured.err


def test_invalid_port_non_numeric(tmp_path, monkeypatch, capsys):
    """Test error handling for non-numeric port."""
    monkeypatch.chdir(tmp_path)
    
    (tmp_path / ".env").write_text(
        "TG100_HOST=192.168.1.100\n"
        "TG100_PORT=not-a-number\n"
        "TG100_USERNAME=admin\n"
        "TG100_PASSWORD=secret\n"
        "API_URL=https://api.example.com/sms/incoming\n"
        "API_BEARER_TOKEN=test-token\n"
        "HMAC_SECRET_KEY=test-secret-key\n"
    )
    
    with pytest.raises(SystemExit) as exc_info:
        Config.load_from_env()
    
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TG100_PORT must be a valid integer" in captured.err


def test_invalid_port_out_of_range(tmp_path, monkeypatch, capsys):
    """Test error handling for port out of valid range."""
    monkeypatch.chdir(tmp_path)
    
    (tmp_path / ".env").write_text(
        "TG100_HOST=192.168.1.100\n"
        "TG100_PORT=99999\n"
        "TG100_USERNAME=admin\n"
        "TG100_PASSWORD=secret\n"
        "API_URL=https://api.example.com/sms/incoming\n"
        "API_BEARER_TOKEN=test-token\n"
        "HMAC_SECRET_KEY=test-secret-key\n"
    )
    
    with pytest.raises(SystemExit) as exc_info:
        Config.load_from_env()
    
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TG100_PORT must be between 1 and 65535" in captured.err


def test_invalid_url_format(tmp_path, monkeypatch, capsys):
    """Test error handling for invalid URL format."""
    monkeypatch.chdir(tmp_path)
    
    (tmp_path / ".env").write_text(
        "TG100_HOST=192.168.1.100\n"
        "TG100_PORT=5038\n"
        "TG100_USERNAME=admin\n"
        "TG100_PASSWORD=secret\n"
        "API_URL=not-a-valid-url\n"
        "API_BEARER_TOKEN=test-token\n"
        "HMAC_SECRET_KEY=test-secret-key\n"
    )
    
    with pytest.raises(SystemExit) as exc_info:
        Config.load_from_env()
    
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "API_URL must start with http:// or https://" in captured.err
