CREATE TABLE financial_accounts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('cash','bank')),
  name TEXT NOT NULL UNIQUE,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO financial_accounts(id,kind,name) SELECT 1,'cash',cash_label FROM shop_settings WHERE id=1;
INSERT INTO financial_accounts(id,kind,name) SELECT 2,'bank',bank_label FROM shop_settings WHERE id=1;
ALTER TABLE money_movements ADD COLUMN account_id INTEGER REFERENCES financial_accounts(id);
UPDATE money_movements SET account_id=CASE method WHEN 'cash' THEN 1 ELSE 2 END;
CREATE INDEX money_movement_account ON money_movements(account_id,created_at);
