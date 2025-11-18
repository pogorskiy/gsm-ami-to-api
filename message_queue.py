"""Message queue with FIFO ordering for SMS delivery."""

from typing import Optional, Tuple
from collections import deque
from sms_message import SMSMessage


class MessageQueue:
    """FIFO queue for managing SMS messages pending delivery.
    
    The queue stores messages along with their computed signatures and
    ensures first-in-first-out ordering for delivery attempts.
    """
    
    def __init__(self):
        """Initialize an empty message queue."""
        self._queue = deque()
    
    def enqueue(self, message: SMSMessage, signature: str) -> None:
        """Add a message to the end of the queue.
        
        Args:
            message: SMSMessage object to enqueue
            signature: Computed HMAC-SHA256 signature for the message
        """
        self._queue.append((message, signature))
    
    def peek(self) -> Optional[Tuple[SMSMessage, str]]:
        """View the first message in the queue without removing it.
        
        Returns:
            Tuple of (SMSMessage, signature) if queue is not empty, None otherwise
        """
        if self._queue:
            return self._queue[0]
        return None
    
    def dequeue(self) -> None:
        """Remove the first message from the queue.
        
        Does nothing if the queue is empty.
        """
        if self._queue:
            self._queue.popleft()
    
    def is_empty(self) -> bool:
        """Check if the queue is empty.
        
        Returns:
            True if queue has no messages, False otherwise
        """
        return len(self._queue) == 0
    
    def size(self) -> int:
        """Get the number of messages in the queue.
        
        Returns:
            Number of messages currently in the queue
        """
        return len(self._queue)
