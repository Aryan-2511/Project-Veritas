PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pending_dispatch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  subscription_id INTEGER NOT NULL,
  insight_id INTEGER NOT NULL,
  score REAL,
  created_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pending_dispatch_user_sub
  ON pending_dispatch(user_id, subscription_id);

CREATE TABLE IF NOT EXISTS sent_digests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  subscription_id INTEGER NOT NULL,
  sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sent_digests_user_sub
  ON sent_digests(user_id, subscription_id);

CREATE TABLE IF NOT EXISTS user_contacts (
  user_id TEXT PRIMARY KEY,
  email TEXT NOT NULL
);
