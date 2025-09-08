PRAGMA foreign_keys = ON;

-- 1) Persisted moderation log (every request -> one row)
CREATE TABLE IF NOT EXISTS moderation_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id INTEGER,           -- optional: scout subscription id
  user_id TEXT,                      -- optional: user id (if provided)
  item_title TEXT,
  item_url TEXT,
  content_hash TEXT,                 -- sha256(content|url) for quick de-dup
  content_snippet TEXT,              -- short excerpt (e.g. first 2000 chars)
  requested_at INTEGER NOT NULL,     -- unix epoch
  decision_allowed INTEGER NOT NULL, -- 1 = allowed, 0 = blocked
  categories TEXT,                   -- JSON array of categories (e.g. '["porn","violence"]')
  reason TEXT,                       -- short reason/note from moderator/LLM
  model_response TEXT,               -- raw LLM response / debug
  model_confidence REAL,             -- optional confidence score from LLM
  moderator_jti TEXT,                -- delegated token jti (for audit)
  remote_addr TEXT,                  -- client IP (optional)
  created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_moderation_logs_subscription ON moderation_logs(subscription_id);
CREATE INDEX IF NOT EXISTS idx_moderation_logs_user ON moderation_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_moderation_logs_content_hash ON moderation_logs(content_hash);

-- 2) Blocked items cache (fast lookup if you want to check whether a URL was previously blocked)
CREATE TABLE IF NOT EXISTS blocked_items_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_hash TEXT UNIQUE,
  first_blocked_at INTEGER,
  categories TEXT,
  reason TEXT,
  blocked_count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_blocked_items_content_hash ON blocked_items_cache(content_hash);

-- 3) Rules table for deterministic/pattern-based moderation (admin-manageable)
CREATE TABLE IF NOT EXISTS moderation_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT,
  pattern TEXT NOT NULL,        -- e.g. regex or substring
  pattern_type TEXT NOT NULL,   -- 'regex' | 'substring' | 'domain' | 'hash'
  action TEXT NOT NULL,         -- 'block' | 'allow' | 'review'
  priority INTEGER DEFAULT 100, -- lower runs first
  enabled INTEGER DEFAULT 1,
  created_at INTEGER DEFAULT (strftime('%s','now')),
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_moderation_rules_priority ON moderation_rules(priority);

-- 4) Queue for manual-review / pending decisions
CREATE TABLE IF NOT EXISTS pending_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  moderation_log_id INTEGER NOT NULL, -- link back to moderation_logs.id
  reason TEXT,
  assigned_to TEXT,                   -- moderator/admin id
  status TEXT DEFAULT 'pending',      -- pending | in_progress | resolved
  created_at INTEGER DEFAULT (strftime('%s','now')),
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pending_review_status ON pending_review(status);