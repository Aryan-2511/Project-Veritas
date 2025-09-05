-- subscriptions and items for Scout
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  source TEXT NOT NULL,       -- 'arxiv' | 'twitter'
  url TEXT NOT NULL,          -- feed/url
  created_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_user_source_url ON subscriptions(user_id, source, url);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id INTEGER,
  source TEXT NOT NULL,
  source_id TEXT,
  title TEXT,
  content TEXT,
  url TEXT,
  fetch_time INTEGER,
  fingerprint TEXT,
  FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS items_subscription_idx ON items(subscription_id);
CREATE INDEX IF NOT EXISTS items_fetch_time_idx ON items(fetch_time);
