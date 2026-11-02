"""Message queue with FIFO ordering for SMS delivery."""

import os
import sqlite3
import time
from typing import Optional, Tuple
from collections import deque
from sms_message import SMSMessage
from logging_config import get_logger


logger = get_logger(__name__)


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
    
    def mark_failed(self, error: Optional[str] = None) -> None:
        """Remove the first message after a permanent failure.
        
        The in-memory queue has nowhere to keep it, so the message is discarded.
        """
        self.dequeue()
    
    def record_attempt(self, error: Optional[str] = None) -> None:
        """Count a failed delivery attempt (nothing to record in memory)."""
    
    def close(self) -> None:
        """Nothing to close for the in-memory queue."""
    
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


# Statuses of a message in the SQLite queue
STATUS_PENDING = 'pending'  # waits for delivery
STATUS_SENT = 'sent'        # delivered
STATUS_FAILED = 'failed'    # permanent failure: kept for manual retry (queue_tool.py)

# How long delivered and failed messages are kept (they contain SMS texts)
SENT_RETENTION_DAYS = 7
FAILED_RETENTION_DAYS = 30
PURGE_INTERVAL = 3600  # seconds between purges of old messages
REOPEN_INTERVAL = 60  # seconds between attempts to open the database after a failure

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    signature TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS messages_status_id ON messages (status, id);
"""


def open_queue_db(path: str) -> sqlite3.Connection:
    """Open the queue database and create the table if needed.

    The file contains SMS texts: it is made readable only by the owner (0600).

    Args:
        path: Path to the SQLite file

    Returns:
        Open connection (autocommit mode, every change is written to disk at once)
    """
    # Fix permissions first: a file without write permission is opened read-only
    _restrict(path)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # FULL: a committed message survives a power loss, not only a crash
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_SCHEMA)
    _restrict(path)
    return conn


def _restrict(path: str) -> None:
    """Make the database files readable and writable only by the owner (0600)."""
    for suffix in ('', '-wal', '-shm'):
        try:
            os.chmod(path + suffix, 0o600)
        except OSError:
            pass


class SQLiteMessageQueue:
    """FIFO queue stored in an SQLite file: survives a service restart.

    Same interface as MessageQueue. A message is written to disk in enqueue(),
    before the first delivery attempt. Delivered messages are marked 'sent',
    permanently failed ones 'failed' (not deleted). Both are purged after
    SENT_RETENTION_DAYS / FAILED_RETENTION_DAYS.

    If the database cannot be opened or written (disk full, I/O error), messages
    are kept in memory, so delivery does not stop. The queue keeps trying: it
    reopens the file every REOPEN_INTERVAL seconds and moves the messages from
    memory to disk as soon as writing works again.
    """

    def __init__(self, path: str):
        """Open (or create) the queue file.

        Does not raise: if the file cannot be opened, the queue starts in memory
        and tries again later (see is_open()).

        Args:
            path: Path to the SQLite file
        """
        self.path = path
        self._conn = None
        self._next_open_time = 0.0
        # Messages not written to the database yet (oldest first)
        self._memory = MessageQueue()
        # Failed messages not written to the database yet: (message, signature, error)
        self._memory_failed = []
        # What peek() returned: ('db', row id) or ('memory', None)
        self._peeked = None
        # Status changes not yet written because of a database error: id -> (status, error)
        self._unsaved_status = {}
        # A database error was logged and the database has not worked since
        self._error_logged = False
        self._last_purge = float('-inf')
        if self._connection() is not None:
            self._purge_old()

    def is_open(self) -> bool:
        """Check if the database file is open."""
        return self._conn is not None

    def enqueue(self, message: SMSMessage, signature: Optional[str]) -> None:
        """Write a message to the end of the queue (to disk).

        Args:
            message: SMSMessage object to enqueue
            signature: HMAC-SHA256 signature (relay mode) or None
        """
        self._memory.enqueue(message, signature)
        self._flush_memory()

    def peek(self) -> Optional[Tuple[SMSMessage, Optional[str]]]:
        """View the first pending message without removing it.

        Also retries what earlier database errors left unsaved.

        Returns:
            Tuple of (SMSMessage, signature) if queue is not empty, None otherwise
        """
        self._peeked = None
        self._retry_unsaved()
        row = self._first_pending_row()
        if row is not None:
            self._peeked = ('db', row['id'])
            message = SMSMessage(row['gateway_id'], row['sender'], row['message'], row['timestamp'])
            return message, row['signature']
        item = self._memory.peek()
        if item is not None:
            self._peeked = ('memory', None)
        return item

    def dequeue(self) -> None:
        """Mark the message returned by peek() as delivered."""
        self._finish(STATUS_SENT, None)

    def mark_failed(self, error: Optional[str] = None) -> None:
        """Mark the message returned by peek() as permanently failed.

        It is kept in the database (status 'failed') for a manual retry.

        Args:
            error: Short description of the failure
        """
        self._finish(STATUS_FAILED, error)

    def record_attempt(self, error: Optional[str] = None) -> None:
        """Count a failed delivery attempt of the message returned by peek().

        Args:
            error: Short description of the failure
        """
        if self._peeked is None or self._peeked[0] != 'db':
            return
        self._execute(
            "UPDATE messages SET attempts = attempts + 1, last_error = ?, updated_at = ?"
            " WHERE id = ?",
            (error, time.time(), self._peeked[1]),
        )

    def is_empty(self) -> bool:
        """Check if no message waits for delivery."""
        return self.size() == 0

    def size(self) -> int:
        """Get the number of messages waiting for delivery.

        Messages whose final status is not saved yet are not counted.
        Also retries what earlier database errors left unsaved.
        """
        self._retry_unsaved()
        count = self._memory.size()
        cursor = self._execute("SELECT COUNT(*) FROM messages WHERE status = ?", (STATUS_PENDING,))
        if cursor is not None:
            pending_unsaved = sum(
                1 for row_id in self._unsaved_status if self._is_pending(row_id)
            ) if self._unsaved_status else 0
            count += cursor.fetchone()[0] - pending_unsaved
        return max(count, 0)

    def close(self) -> None:
        """Close the database."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _connection(self) -> Optional[sqlite3.Connection]:
        """Get the open connection; (re)open the file if it is time to try."""
        if self._conn is None and time.monotonic() >= self._next_open_time:
            try:
                self._conn = open_queue_db(self.path)
            except (sqlite3.Error, OSError) as e:
                self._next_open_time = time.monotonic() + REOPEN_INTERVAL
                logger.error(
                    f"Cannot open queue database {self.path} ({e}) - messages are kept "
                    f"in memory, will try again in {REOPEN_INTERVAL}s"
                )
            else:
                if self._error_logged:
                    logger.info(f"Queue database {self.path} opened")
        return self._conn

    def _execute(self, sql: str, params=()) -> Optional[sqlite3.Cursor]:
        """Run one statement; on a database error log it (once) and return None."""
        conn = self._connection()
        if conn is None:
            self._error_logged = True
            return None
        try:
            cursor = conn.execute(sql, params)
        except sqlite3.Error as e:
            if not self._error_logged:
                logger.error(f"Queue database error ({e}) - messages are kept in memory until it works")
                self._error_logged = True
            if 'readonly' in str(e):
                # Opened read-only: this connection never writes, open the file again later
                self.close()
                self._next_open_time = time.monotonic() + REOPEN_INTERVAL
            return None
        if self._error_logged:
            logger.info("Queue database works again")
            self._error_logged = False
        return cursor

    def _retry_unsaved(self) -> None:
        """Write status changes and messages that earlier database errors left unsaved."""
        if self._unsaved_status:
            self._save_status()
        if not self._memory.is_empty():
            self._flush_memory()
        while self._memory_failed:
            message, signature, error = self._memory_failed[0]
            if self._insert(message, signature, STATUS_FAILED, error) is None:
                return
            self._memory_failed.pop(0)

    def _insert(self, message: SMSMessage, signature: Optional[str], status: str,
                error: Optional[str] = None) -> Optional[sqlite3.Cursor]:
        """Insert one message row; None on a database error."""
        now = time.time()
        return self._execute(
            "INSERT INTO messages (gateway_id, sender, message, timestamp, signature,"
            " status, created_at, updated_at, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message.gateway_id, message.sender, message.message, message.timestamp,
             signature, status, now, now, error),
        )

    def _flush_memory(self) -> None:
        """Write messages kept in memory to the database, oldest first.

        Done before reading the database, so they keep their place in the queue.
        """
        while not self._memory.is_empty():
            message, signature = self._memory.peek()
            if self._insert(message, signature, STATUS_PENDING) is None:
                return
            self._memory.dequeue()

    def _is_pending(self, row_id: int) -> bool:
        """Check if a row still has status 'pending' in the database."""
        cursor = self._execute("SELECT status FROM messages WHERE id = ?", (row_id,))
        row = cursor.fetchone() if cursor is not None else None
        return row is not None and row['status'] == STATUS_PENDING

    def _first_pending_row(self) -> Optional[sqlite3.Row]:
        """Get the oldest pending row, skipping rows whose status is not saved yet."""
        cursor = self._execute(
            "SELECT * FROM messages WHERE status = ? ORDER BY id LIMIT ?",
            (STATUS_PENDING, len(self._unsaved_status) + 1),
        )
        if cursor is None:
            return None
        for row in cursor.fetchall():
            if row['id'] not in self._unsaved_status:
                return row
        return None

    def _finish(self, status: str, error: Optional[str]) -> None:
        """Set the final status of the message returned by peek()."""
        peeked, self._peeked = self._peeked, None
        if peeked is None:
            return
        if peeked[0] == 'memory':
            if status == STATUS_FAILED:
                # Keep it until the database works: then it is saved as 'failed'
                message, signature = self._memory.peek()
                self._memory_failed.append((message, signature, error))
            self._memory.dequeue()
            self._retry_unsaved()
            return
        self._unsaved_status[peeked[1]] = (status, error)
        self._save_status()
        self._maybe_purge()

    def _save_status(self) -> None:
        """Write status changes; on error keep them and try again next time.

        Until written, the message is skipped by peek(): it is not delivered twice.
        """
        for row_id, (status, error) in list(self._unsaved_status.items()):
            cursor = self._execute(
                "UPDATE messages SET status = ?, last_error = COALESCE(?, last_error),"
                " updated_at = ? WHERE id = ?",
                (status, error, time.time(), row_id),
            )
            if cursor is None:
                return
            del self._unsaved_status[row_id]

    def _maybe_purge(self) -> None:
        """Purge old messages not more often than PURGE_INTERVAL."""
        if time.monotonic() - self._last_purge >= PURGE_INTERVAL:
            self._purge_old()

    def _purge_old(self) -> None:
        """Delete delivered and failed messages older than their retention time."""
        self._last_purge = time.monotonic()
        now = time.time()
        self._execute(
            "DELETE FROM messages WHERE (status = ? AND updated_at < ?)"
            " OR (status = ? AND updated_at < ?)",
            (STATUS_SENT, now - SENT_RETENTION_DAYS * 86400,
             STATUS_FAILED, now - FAILED_RETENTION_DAYS * 86400),
        )
