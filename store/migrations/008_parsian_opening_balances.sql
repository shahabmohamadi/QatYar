CREATE TABLE opening_party_balances (
  party_id INTEGER PRIMARY KEY REFERENCES parties(id),
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  amount_rial INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE opening_account_balances (
  account_code INTEGER PRIMARY KEY CHECK (account_code IN (10,11)),
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  amount_rial INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE opening_settlements (
  id INTEGER PRIMARY KEY,
  party_id INTEGER NOT NULL REFERENCES opening_party_balances(party_id),
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  method TEXT NOT NULL CHECK (method IN ('cash','bank')),
  amount_rial INTEGER NOT NULL CHECK (amount_rial > 0),
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX opening_settlements_party ON opening_settlements(party_id,created_at);
