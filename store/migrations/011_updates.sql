CREATE TABLE update_settings (
  id INTEGER PRIMARY KEY CHECK(id=1),
  github_repo TEXT NOT NULL DEFAULT '',
  release_tag TEXT NOT NULL DEFAULT '',
  release_notes TEXT NOT NULL DEFAULT '',
  asset_url TEXT NOT NULL DEFAULT '',
  asset_digest TEXT NOT NULL DEFAULT '',
  checked_at TEXT
);
INSERT INTO update_settings(id) VALUES (1);
