DROP TABLE IF EXISTS insights;

CREATE TABLE IF NOT EXISTS insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  insight_type TEXT,
  score REAL,
  summary TEXT,
  evidence TEXT,
  recommended_action TEXT,
  raw_response TEXT,
  subscription_id INTEGER,
  user_id TEXT,
  created_at INTEGER
);

CREATE INDEX IF NOT EXISTS insights_created_at_idx ON insights(created_at);
