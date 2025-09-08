# common/audit.py
import os
import sqlite3
import time
import json
from pathlib import Path


AUDIT_DB = os.getenv("AUDIT_DB", "./data/audit.db")


# ensure folder exists
Path(AUDIT_DB).parent.mkdir(parents=True, exist_ok=True)


def _get_conn():
    conn = sqlite3.connect(AUDIT_DB, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def audit_insert(actor: str, action: str, user_id: str | None = None, audience: str | None = None,
    scope: str | None = None, jti: str | None = None, outcome: str = "success",
    details: dict | None = None):
    """Insert an audit row (synchronous). details will be JSON-serialized.
    actor: service name (concierge|scout|analyst|dispatcher)
    action: textual action (token_delegation|subscribe|insight_created|dispatch)
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO audit_log (timestamp, user_id, actor, action, audience, scope, jti, outcome, details)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (int(time.time()), user_id, actor, action, audience, scope, jti, outcome, json.dumps(details) if details else None))
    conn.commit()
    conn.close()