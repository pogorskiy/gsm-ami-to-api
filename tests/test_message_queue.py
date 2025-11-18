"""Tests for MessageQueue class."""

import pytest
from message_queue import MessageQueue
from sms_message import SMSMessage


def test_queue_starts_empty():
    """Test that a new queue is empty."""
    queue = MessageQueue()
    assert queue.is_empty()
    assert queue.size() == 0


def test_enqueue_adds_message():
    """Test that enqueue adds a message to the queue."""
    queue = MessageQueue()
    message = SMSMessage("dongle0", "+1234567890", "Test message")
    signature = "abc123"
    
    queue.enqueue(message, signature)
    
    assert not queue.is_empty()
    assert queue.size() == 1


def test_peek_returns_first_message_without_removal():
    """Test that peek returns the first message without removing it."""
    queue = MessageQueue()
    message1 = SMSMessage("dongle0", "+1111111111", "First")
    message2 = SMSMessage("dongle0", "+2222222222", "Second")
    
    queue.enqueue(message1, "sig1")
    queue.enqueue(message2, "sig2")
    
    # Peek should return first message
    peeked = queue.peek()
    assert peeked is not None
    assert peeked[0].sender == "+1111111111"
    assert peeked[1] == "sig1"
    
    # Queue should still have both messages
    assert queue.size() == 2


def test_peek_returns_none_for_empty_queue():
    """Test that peek returns None when queue is empty."""
    queue = MessageQueue()
    assert queue.peek() is None


def test_dequeue_removes_first_message():
    """Test that dequeue removes the first message."""
    queue = MessageQueue()
    message1 = SMSMessage("dongle0", "+1111111111", "First")
    message2 = SMSMessage("dongle0", "+2222222222", "Second")
    
    queue.enqueue(message1, "sig1")
    queue.enqueue(message2, "sig2")
    
    # Remove first message
    queue.dequeue()
    
    # Queue should now have one message
    assert queue.size() == 1
    
    # Peek should return second message
    peeked = queue.peek()
    assert peeked[0].sender == "+2222222222"


def test_dequeue_on_empty_queue_does_nothing():
    """Test that dequeue on empty queue doesn't raise an error."""
    queue = MessageQueue()
    queue.dequeue()  # Should not raise an error
    assert queue.is_empty()


def test_fifo_ordering():
    """Test that messages are retrieved in FIFO order."""
    queue = MessageQueue()
    
    # Add three messages
    messages = [
        (SMSMessage("dongle0", "+1111111111", "First"), "sig1"),
        (SMSMessage("dongle0", "+2222222222", "Second"), "sig2"),
        (SMSMessage("dongle0", "+3333333333", "Third"), "sig3"),
    ]
    
    for msg, sig in messages:
        queue.enqueue(msg, sig)
    
    # Verify FIFO order
    for expected_msg, expected_sig in messages:
        peeked = queue.peek()
        assert peeked is not None
        assert peeked[0].sender == expected_msg.sender
        assert peeked[1] == expected_sig
        queue.dequeue()
    
    # Queue should be empty now
    assert queue.is_empty()


def test_multiple_enqueue_dequeue_cycles():
    """Test multiple cycles of enqueue and dequeue operations."""
    queue = MessageQueue()
    
    # First cycle
    queue.enqueue(SMSMessage("dongle0", "+1111111111", "Msg1"), "sig1")
    assert queue.size() == 1
    queue.dequeue()
    assert queue.is_empty()
    
    # Second cycle
    queue.enqueue(SMSMessage("dongle0", "+2222222222", "Msg2"), "sig2")
    queue.enqueue(SMSMessage("dongle0", "+3333333333", "Msg3"), "sig3")
    assert queue.size() == 2
    queue.dequeue()
    assert queue.size() == 1
    queue.dequeue()
    assert queue.is_empty()
