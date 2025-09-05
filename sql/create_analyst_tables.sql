-- insights produced by Analyst
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  insight_type TEXT NOT NULL,
  score REAL,
  summary TEXT,
  evidence TEXT,      -- JSON string with evidence refs
  user_id TEXT,       -- optional: who requested / the owner
  created_at INTEGER
);

CREATE INDEX IF NOT EXISTS insights_created_at_idx ON insights(created_at);
