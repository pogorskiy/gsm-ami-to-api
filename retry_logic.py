"""Retry logic with exponential backoff for SMS Gateway Service."""


def calculate_exponential_backoff(attempt: int, initial_delay: int, max_delay: int) -> int:
    """Calculate exponential backoff delay for retry attempts.
    
    The delay increases exponentially with each attempt: initial_delay * 2^attempt,
    but is capped at max_delay to prevent excessively long waits.
    
    Args:
        attempt: The retry attempt number (0-indexed, where 0 is the first retry)
        initial_delay: The initial delay in seconds (minimum delay)
        max_delay: The maximum delay in seconds (cap for exponential growth)
    
    Returns:
        The calculated delay in seconds, guaranteed to be:
        - At least initial_delay
        - At most max_delay
        - Exponentially increasing with attempt number
    
    Examples:
        >>> calculate_exponential_backoff(0, 5, 60)
        5
        >>> calculate_exponential_backoff(1, 5, 60)
        10
        >>> calculate_exponential_backoff(2, 5, 60)
        20
        >>> calculate_exponential_backoff(10, 5, 60)
        60
    """
    # Calculate exponential delay: initial_delay * 2^attempt
    delay = initial_delay * (2 ** attempt)
    
    # Ensure delay is at least initial_delay and at most max_delay
    return min(delay, max_delay)
