"""Tests for SQLiteMessageQueue: the queue stored on disk."""

import os
import sqlite3
import stat
import time
import pytest
import message_queue
from message_queue import SQLiteMessageQueue
from sms_message import SMSMessage
import queue_tool


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "queue.db")


def _sms(text):
    return SMSMessage("1", "+374", text, "2026-11-02T10:00:00Z")


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
    finally:
        conn.close()


def test_fifo_order(db_path):
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("first"), "sig1")
    queue.enqueue(_sms("second"), None)

    message, signature = queue.peek()
    assert (message.message, signature) == ("first", "sig1")
    queue.dequeue()
    message, signature = queue.peek()
    assert (message.message, signature) == ("second", None)
    queue.dequeue()
    assert queue.is_empty()
    assert queue.peek() is None


def test_message_fields_are_kept(db_path):
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(SMSMessage("span2", ".INECOBANK", "Код: 1234 <b>", "2026-11-02T10:35:00Z"), None)
    queue.close()

    message, _ = SQLiteMessageQueue(db_path).peek()
    assert message.gateway_id == "span2"
    assert message.sender == ".INECOBANK"
    assert message.message == "Код: 1234 <b>"
    assert message.timestamp == "2026-11-02T10:35:00Z"


def test_pending_messages_survive_reopen(db_path):
    queue = SQLiteMessageQueue(db_path)
    for text in ("a", "b", "c"):
        queue.enqueue(_sms(text), None)
    queue.peek()
    queue.dequeue()  # "a" delivered
    queue.close()

    reopened = SQLiteMessageQueue(db_path)
    assert reopened.size() == 2
    assert reopened.peek()[0].message == "b"


def test_mark_failed_keeps_message_out_of_queue(db_path):
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("bad"), None)
    queue.enqueue(_sms("good"), None)

    queue.peek()
    queue.mark_failed("HTTP 400")

    assert queue.size() == 1
    assert queue.peek()[0].message == "good"
    rows = _rows(db_path)
    assert (rows[0]['status'], rows[0]['last_error']) == ("failed", "HTTP 400")


def test_dequeue_marks_the_peeked_message(db_path):
    """A message returned to the queue meanwhile (queue_tool retry) is not marked by mistake."""
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("old"), None)
    queue.peek()
    queue.mark_failed()
    queue.enqueue(_sms("new"), None)

    assert queue.peek()[0].message == "new"
    queue_tool.main(["--db", db_path, "retry", "--all-failed"])  # "old" is pending again, before "new"
    queue.dequeue()

    statuses = {row['message']: row['status'] for row in _rows(db_path)}
    assert statuses == {"old": "pending", "new": "sent"}
    assert queue.peek()[0].message == "old"


def test_record_attempt(db_path):
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("x"), None)
    queue.peek()
    queue.record_attempt("timeout")
    queue.record_attempt("timeout")

    row = _rows(db_path)[0]
    assert (row['attempts'], row['last_error'], row['status']) == (2, "timeout", "pending")


def test_file_is_owner_only(db_path):
    old_umask = os.umask(0o022)
    try:
        queue = SQLiteMessageQueue(db_path)
        queue.enqueue(_sms("x"), None)
    finally:
        os.umask(old_umask)
    for suffix in ('', '-wal', '-shm'):
        if os.path.exists(db_path + suffix):
            assert stat.S_IMODE(os.stat(db_path + suffix).st_mode) == 0o600


def test_old_sent_and_failed_messages_are_purged(db_path):
    queue = SQLiteMessageQueue(db_path)
    for text in ("old sent", "old failed", "new sent", "old pending"):
        queue.enqueue(_sms(text), None)
    conn = sqlite3.connect(db_path)
    day = 86400
    now = time.time()
    conn.execute("UPDATE messages SET status='sent', updated_at=? WHERE message='old sent'", (now - 8 * day,))
    conn.execute("UPDATE messages SET status='failed', updated_at=? WHERE message='old failed'", (now - 31 * day,))
    conn.execute("UPDATE messages SET status='sent', updated_at=? WHERE message='new sent'", (now - 1 * day,))
    conn.execute("UPDATE messages SET updated_at=? WHERE message='old pending'", (now - 100 * day,))
    conn.commit()
    conn.close()
    queue.close()

    SQLiteMessageQueue(db_path).close()  # purges on open

    assert sorted(row['message'] for row in _rows(db_path)) == ["new sent", "old pending"]


def test_write_error_keeps_message_in_memory(db_path, monkeypatch):
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("on disk"), None)

    class BrokenConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("database or disk is full")
            return self._conn.execute(sql, *args)

        def close(self):
            self._conn.close()

    queue._conn = BrokenConnection(queue._conn)
    queue.enqueue(_sms("in memory"), None)

    assert queue.size() == 2
    assert queue.peek()[0].message == "on disk"
    queue.dequeue()
    assert queue.peek()[0].message == "in memory"
    queue.dequeue()
    assert queue.is_empty()


def test_memory_messages_go_to_disk_when_database_works_again(db_path):
    """SMS kept in memory are written first, before newer ones: order and durability."""
    queue = SQLiteMessageQueue(db_path)
    real_conn = queue._conn

    class FullDisk:
        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("database or disk is full")
            return real_conn.execute(sql, *args)

    queue._conn = FullDisk()
    queue.enqueue(_sms("A"), None)
    queue.enqueue(_sms("B"), None)
    assert _rows(db_path) == []

    queue._conn = real_conn
    queue.enqueue(_sms("C"), None)

    assert [row['message'] for row in _rows(db_path)] == ["A", "B", "C"]
    assert queue.peek()[0].message == "A"


def test_failed_memory_message_is_saved_when_database_works_again(db_path):
    """A message never written to disk and then failed permanently is not lost."""
    queue = SQLiteMessageQueue(db_path)
    real_conn = queue._conn

    class FullDisk:
        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("database or disk is full")
            return real_conn.execute(sql, *args)

    queue._conn = FullDisk()
    queue.enqueue(_sms("rejected"), None)
    assert queue.peek()[0].message == "rejected"
    queue.mark_failed("HTTP 400")
    assert queue.is_empty()

    queue._conn = real_conn
    assert queue.is_empty()
    row = _rows(db_path)[0]
    assert (row['message'], row['status'], row['last_error']) == ("rejected", "failed", "HTTP 400")


def test_unsaved_failed_status_is_saved_when_queue_looks_empty(db_path):
    """A 'failed' mark lost to a write error is saved later even with nothing else queued."""
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("only"), None)
    real_conn = queue._conn

    class NoUpdates:
        def execute(self, sql, *args):
            if sql.startswith("UPDATE"):
                raise sqlite3.OperationalError("disk I/O error")
            return real_conn.execute(sql, *args)

    queue._conn = NoUpdates()
    queue.peek()
    queue.mark_failed("HTTP 400")
    assert queue.is_empty()
    assert _rows(db_path)[0]['status'] == "pending"

    queue._conn = real_conn
    assert queue.is_empty()  # the service calls this every loop
    assert _rows(db_path)[0]['status'] == "failed"


def test_status_write_error_does_not_resend(db_path):
    """If 'sent' cannot be saved, the message is not delivered again; saved later."""
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("a"), None)
    queue.enqueue(_sms("b"), None)
    real_conn = queue._conn

    class ReadOnlyConnection:
        def execute(self, sql, *args):
            if sql.startswith("UPDATE"):
                raise sqlite3.OperationalError("disk I/O error")
            return real_conn.execute(sql, *args)

    queue._conn = ReadOnlyConnection()
    queue.peek()
    queue.dequeue()  # "a" delivered, status not saved

    assert queue.size() == 1
    assert queue.peek()[0].message == "b"

    queue._conn = real_conn
    queue.dequeue()  # saves both
    assert [row['status'] for row in _rows(db_path)] == ["sent", "sent"]
    assert queue.is_empty()


def test_queue_tool_list_and_retry(db_path, capsys):
    queue = SQLiteMessageQueue(db_path)
    queue.enqueue(_sms("secret code 1234"), None)
    queue.peek()
    queue.mark_failed("permanent delivery failure")
    queue.close()

    assert queue_tool.main(["--db", db_path, "list"]) == 0
    out = capsys.readouterr().out
    assert "failed" in out and "+374" in out
    assert "secret code" not in out  # list does not show texts

    assert queue_tool.main(["--db", db_path, "show", "1"]) == 0
    assert "secret code 1234" in capsys.readouterr().out

    assert queue_tool.main(["--db", db_path, "retry", "1"]) == 0
    assert SQLiteMessageQueue(db_path).peek()[0].message == "secret code 1234"


def test_queue_tool_missing_db(tmp_path):
    assert queue_tool.main(["--db", str(tmp_path / "nope.db"), "list"]) == 1


def test_read_only_connection_is_reopened(db_path, monkeypatch):
    """A connection that cannot write is closed and the file opened again."""
    queue = SQLiteMessageQueue(db_path)
    queue.close()
    queue._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)
    queue._conn.row_factory = sqlite3.Row

    queue.enqueue(_sms("x"), None)  # write fails: kept in memory
    assert not queue.is_open()

    monkeypatch.setattr(message_queue, "REOPEN_INTERVAL", 0)
    queue._next_open_time = 0.0
    assert queue.size() == 1
    assert [row['message'] for row in _rows(db_path)] == ["x"]


def test_queue_tool_refuses_other_user(db_path, monkeypatch, capsys):
    """Another user (e.g. root) would create -wal/-shm files the service cannot open."""
    SQLiteMessageQueue(db_path).close()
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(db_path).st_uid + 1)
    assert queue_tool.main(["--db", db_path, "list"]) == 1
    assert "Run as the owner" in capsys.readouterr().err
