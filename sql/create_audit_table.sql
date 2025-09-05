-- audit log (append-only)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL,
  user_id TEXT,           -- user who initiated (if any)
  actor TEXT NOT NULL,    -- which agent or service e.g. concierge, scout
  action TEXT NOT NULL,   -- e.g. "token_delegation", "subscribe", "analyze", "dispatch"
  audience TEXT,          -- target audience (inbound app id)
  scope TEXT,             -- scopes requested
  jti TEXT,               -- delegated token id (if present)
  outcome TEXT,           -- "success" | "failed"
  details TEXT            -- optional JSON / text
);

CREATE INDEX IF NOT EXISTS audit_timestamp_idx ON audit_log(timestamp);
