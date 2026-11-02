#!/usr/bin/env python3
"""
Look at the message queue database and retry failed messages.

The running service delivers messages returned to 'pending' by itself,
this tool does not send anything.

Usage (in the service directory, as the owner of queue.db - the user the
service runs as; other users are refused):
    python queue_tool.py list                  # pending and failed messages
    python queue_tool.py list --status sent    # delivered (kept for some days)
    python queue_tool.py show 42               # one message with its text
    python queue_tool.py retry 42 43           # failed -> pending
    python queue_tool.py retry --all-failed
"""

import argparse
import os
import sys
import time
from datetime import datetime
from message_queue import (
    open_queue_db, STATUS_PENDING, STATUS_SENT, STATUS_FAILED,
)
from config import DEFAULT_QUEUE_DB_PATH


def _time(value: float) -> str:
    """Format a Unix time as local time."""
    return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')


def cmd_list(conn, args) -> int:
    """Print messages without their texts."""
    statuses = [args.status] if args.status else [STATUS_PENDING, STATUS_FAILED]
    rows = conn.execute(
        f"SELECT id, status, sender, created_at, attempts, last_error FROM messages"
        f" WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id",
        statuses,
    ).fetchall()
    if not rows:
        print(f"No messages with status: {', '.join(statuses)}")
        return 0
    print(f"{'id':>6}  {'status':<8} {'received':<19}  {'tries':>5}  sender / last error")
    for row in rows:
        error = f"  ({row['last_error']})" if row['last_error'] else ''
        print(f"{row['id']:>6}  {row['status']:<8} {_time(row['created_at'])}  "
              f"{row['attempts']:>5}  {row['sender']}{error}")
    return 0


def cmd_show(conn, args) -> int:
    """Print one message with its text."""
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        print(f"No message with id {args.id}", file=sys.stderr)
        return 1
    for key in ('id', 'status', 'gateway_id', 'sender', 'timestamp', 'attempts', 'last_error'):
        print(f"{key}: {row[key]}")
    print(f"received: {_time(row['created_at'])}")
    print(f"updated: {_time(row['updated_at'])}")
    print(f"message: {row['message']}")
    return 0


def cmd_retry(conn, args) -> int:
    """Return failed messages to the queue."""
    if args.all_failed:
        where, params = "status = ?", [STATUS_FAILED]
    elif args.ids:
        where = f"status = ? AND id IN ({','.join('?' * len(args.ids))})"
        params = [STATUS_FAILED] + args.ids
    else:
        print("Give message ids or --all-failed", file=sys.stderr)
        return 1
    cursor = conn.execute(
        f"UPDATE messages SET status = ?, attempts = 0, updated_at = ? WHERE {where}",
        [STATUS_PENDING, time.time()] + params,
    )
    print(f"Returned to the queue: {cursor.rowcount} message(s). "
          f"The running service delivers them.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SMS queue database tool")
    parser.add_argument('--db', default=os.getenv('QUEUE_DB_PATH') or DEFAULT_QUEUE_DB_PATH,
                        help=f"Queue database file (default: QUEUE_DB_PATH or {DEFAULT_QUEUE_DB_PATH})")
    sub = parser.add_subparsers(dest='command', required=True)

    p_list = sub.add_parser('list', help="list messages (without texts)")
    p_list.add_argument('--status', choices=(STATUS_PENDING, STATUS_SENT, STATUS_FAILED))
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser('show', help="show one message with its text")
    p_show.add_argument('id', type=int)
    p_show.set_defaults(func=cmd_show)

    p_retry = sub.add_parser('retry', help="return failed messages to the queue")
    p_retry.add_argument('ids', type=int, nargs='*')
    p_retry.add_argument('--all-failed', action='store_true')
    p_retry.set_defaults(func=cmd_retry)

    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        print(f"Queue database not found: {args.db}", file=sys.stderr)
        return 1
    owner = os.stat(args.db).st_uid
    if os.geteuid() != owner:
        # SQLite creates -wal/-shm files owned by whoever opens the database:
        # files owned by another user would break the service
        print(f"Run as the owner of {args.db} (uid {owner}), e.g.: "
              f"sudo -u '#{owner}' venv/bin/python3 queue_tool.py ...", file=sys.stderr)
        return 1
    # The database contains SMS texts: files created here are owner-only too
    os.umask(0o077)
    conn = open_queue_db(args.db)
    try:
        return args.func(conn, args)
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
