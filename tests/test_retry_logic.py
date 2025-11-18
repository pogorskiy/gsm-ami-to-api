"""Tests for retry logic with exponential backoff."""

import pytest
from retry_logic import calculate_exponential_backoff


def test_first_attempt_returns_initial_delay():
    """Test that the first retry attempt (attempt 0) returns initial delay."""
    delay = calculate_exponential_backoff(0, 5, 60)
    assert delay == 5


def test_exponential_growth():
    """Test that delay grows exponentially with attempt number."""
    initial = 5
    max_delay = 60
    
    # attempt 0: 5 * 2^0 = 5
    assert calculate_exponential_backoff(0, initial, max_delay) == 5
    
    # attempt 1: 5 * 2^1 = 10
    assert calculate_exponential_backoff(1, initial, max_delay) == 10
    
    # attempt 2: 5 * 2^2 = 20
    assert calculate_exponential_backoff(2, initial, max_delay) == 20
    
    # attempt 3: 5 * 2^3 = 40
    assert calculate_exponential_backoff(3, initial, max_delay) == 40


def test_max_delay_cap():
    """Test that delay never exceeds maximum."""
    initial = 5
    max_delay = 60
    
    # attempt 4: 5 * 2^4 = 80, but capped at 60
    assert calculate_exponential_backoff(4, initial, max_delay) == 60
    
    # attempt 10: 5 * 2^10 = 5120, but capped at 60
    assert calculate_exponential_backoff(10, initial, max_delay) == 60


def test_different_initial_delays():
    """Test with different initial delay values."""
    # Initial delay of 10 seconds
    assert calculate_exponential_backoff(0, 10, 120) == 10
    assert calculate_exponential_backoff(1, 10, 120) == 20
    assert calculate_exponential_backoff(2, 10, 120) == 40
    
    # Initial delay of 1 second
    assert calculate_exponential_backoff(0, 1, 60) == 1
    assert calculate_exponential_backoff(1, 1, 60) == 2
    assert calculate_exponential_backoff(2, 1, 60) == 4


def test_different_max_delays():
    """Test with different maximum delay values."""
    # Max delay of 30 seconds
    assert calculate_exponential_backoff(3, 5, 30) == 30  # 40 capped to 30
    
    # Max delay of 120 seconds
    assert calculate_exponential_backoff(4, 5, 120) == 80  # Not capped yet
    assert calculate_exponential_backoff(5, 5, 120) == 120  # 160 capped to 120


def test_delay_at_least_initial():
    """Test that delay is always at least the initial delay."""
    # Even with attempt 0, should return initial delay
    assert calculate_exponential_backoff(0, 5, 60) >= 5
    assert calculate_exponential_backoff(0, 10, 120) >= 10


def test_delay_at_most_max():
    """Test that delay never exceeds max delay."""
    # Test with large attempt numbers
    for attempt in range(20):
        delay = calculate_exponential_backoff(attempt, 5, 60)
        assert delay <= 60


def test_realistic_scenario():
    """Test a realistic retry scenario with 5s initial and 60s max."""
    initial = 5
    max_delay = 60
    
    expected_delays = [
        (0, 5),    # 5 * 2^0 = 5
        (1, 10),   # 5 * 2^1 = 10
        (2, 20),   # 5 * 2^2 = 20
        (3, 40),   # 5 * 2^3 = 40
        (4, 60),   # 5 * 2^4 = 80, capped at 60
        (5, 60),   # 5 * 2^5 = 160, capped at 60
        (10, 60),  # Very large, capped at 60
    ]
    
    for attempt, expected in expected_delays:
        actual = calculate_exponential_backoff(attempt, initial, max_delay)
        assert actual == expected, f"Attempt {attempt}: expected {expected}, got {actual}"
