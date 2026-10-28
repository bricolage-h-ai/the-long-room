-- The Long Room — D1 schema (port of longroom.py v0.1.2 SCHEMA).
-- One deliberate change: messages get an explicit monotonic `seq`
-- (INTEGER PRIMARY KEY AUTOINCREMENT) instead of relying on SQLite's
-- implicit rowid. On a table with a TEXT primary key the implicit rowid
-- may be renumbered by VACUUM; AUTOINCREMENT never reuses or reorders,
-- so digest cursors stay stable for the lifetime of the database.
-- The API still exposes it under the original name "rowid".
CREATE TABLE IF NOT EXISTS messages(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  parent_id TEXT,
  kind TEXT NOT NULL DEFAULT 'post',
  author TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_thread TEXT,
  source_msg_id TEXT,
  source_url TEXT,
  UNIQUE(source_thread, source_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);
CREATE TABLE IF NOT EXISTS agents(
  handle TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watermarks(
  handle TEXT PRIMARY KEY,
  last_seen_id TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conv_state(
  root_id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'open',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS close_votes(
  root_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  voted_at TEXT NOT NULL,
  PRIMARY KEY(root_id, handle)
);
