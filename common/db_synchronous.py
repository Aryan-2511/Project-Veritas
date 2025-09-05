# common/db_synchronous.py
import sqlite3
from typing import Any, Dict

def get_conn(path: str = "./data/scout.db"):
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    # set pragmas per connection
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def query_all(path: str, sql: str, params: tuple = ()):
    conn = get_conn(path)
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def execute(path: str, sql: str, params: tuple = ()):
    conn = get_conn(path)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    lastrowid = cur.lastrowid
    conn.close()
    return lastrowid
