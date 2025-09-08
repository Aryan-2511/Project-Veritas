# veritas/init_db.py
import os
import sqlite3
from glob import glob
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
SQL_DIR = ROOT / "sql"
DATA_DIR = ROOT / "data"

DB_FILES = {
    "scout": DATA_DIR / "scout.db",
    "analyst": DATA_DIR / "analyst.db",
    "audit": DATA_DIR / "audit.db",
    "dispatcher":DATA_DIR/"dispatcher.db",
}

# mapping which SQL files apply to which DB
MIGRATIONS = {
    "scout": ["create_scout_tables.sql"],
    "analyst": ["create_analyst_tables.sql"],
    "audit": ["create_audit_table.sql"],
    "dispatcher":["create_dispatcher_tables.sql"]
}

def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o750)

def apply_sql(db_path: Path, sql_files: list[str]):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # Performance PRAGMAs
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA foreign_keys=ON;")
    conn.commit()
    for fname in sql_files:
        p = SQL_DIR / fname
        if not p.exists():
            raise FileNotFoundError(f"Missing migration {p}")
        sql = p.read_text()
        cur.executescript(sql)
        conn.commit()
        print(f"Applied {fname} -> {db_path}")
    conn.close()

def main():
    ensure_data_dir()
    for key, db_path in DB_FILES.items():
        files = MIGRATIONS.get(key, [])
        apply_sql(db_path, files)
    print("All migrations applied at", time.ctime())

if __name__ == "__main__":
    main()
