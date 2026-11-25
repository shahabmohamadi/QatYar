ALTER TABLE products ADD COLUMN cost_confirmed INTEGER NOT NULL DEFAULT 1 CHECK (cost_confirmed IN (0,1));
CREATE TABLE stock_balances_new (
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL DEFAULT 0,
  avg_cost_toman INTEGER NOT NULL DEFAULT 0 CHECK (avg_cost_toman >= 0),
  PRIMARY KEY(branch_id,product_id)
);
INSERT INTO stock_balances_new SELECT branch_id,product_id,quantity,avg_cost_toman FROM stock_balances;
DROP TABLE stock_balances;
ALTER TABLE stock_balances_new RENAME TO stock_balances;
CREATE TABLE stock_movements_new (
  id INTEGER PRIMARY KEY,
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  delta_qty INTEGER NOT NULL CHECK (delta_qty <> 0),
  reason TEXT NOT NULL CHECK (reason IN ('purchase','sale','adjustment','opening','sale_return')),
  invoice_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO stock_movements_new SELECT * FROM stock_movements;
DROP TABLE stock_movements;
ALTER TABLE stock_movements_new RENAME TO stock_movements;
CREATE INDEX stock_movement_product_idx ON stock_movements(product_id,created_at);
CREATE TABLE legacy_party_map (
  source_db TEXT NOT NULL,
  kol_code INTEGER NOT NULL,
  moeen_code INTEGER NOT NULL,
  tafsili_code INTEGER NOT NULL,
  party_id INTEGER NOT NULL REFERENCES parties(id),
  PRIMARY KEY(source_db,kol_code,moeen_code,tafsili_code)
);
CREATE TABLE sales_returns (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  sale_invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  party_id INTEGER NOT NULL REFERENCES parties(id),
  total_toman INTEGER NOT NULL CHECK (total_toman > 0),
  cost_total_toman INTEGER NOT NULL CHECK (cost_total_toman >= 0),
  reason TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sales_return_lines (
  id INTEGER PRIMARY KEY,
  return_id INTEGER NOT NULL REFERENCES sales_returns(id),
  invoice_line_id INTEGER NOT NULL REFERENCES invoice_lines(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_toman INTEGER NOT NULL CHECK (unit_price_toman >= 0),
  unit_cost_toman INTEGER NOT NULL CHECK (unit_cost_toman >= 0),
  line_total_toman INTEGER NOT NULL CHECK (line_total_toman >= 0)
);
CREATE INDEX sales_return_lines_origin ON sales_return_lines(invoice_line_id);
CREATE TABLE sales_return_refunds (
  id INTEGER PRIMARY KEY,
  return_id INTEGER NOT NULL REFERENCES sales_returns(id),
  method TEXT NOT NULL CHECK (method IN ('cash','bank')),
  amount_toman INTEGER NOT NULL CHECK (amount_toman > 0),
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE payment_checks (
  id INTEGER PRIMARY KEY,
  direction TEXT NOT NULL CHECK (direction IN ('received','issued')),
  party_id INTEGER REFERENCES parties(id),
  invoice_id INTEGER REFERENCES invoices(id),
  check_number TEXT NOT NULL,
  bank_name TEXT NOT NULL DEFAULT '',
  amount_toman INTEGER NOT NULL CHECK (amount_toman > 0),
  due_date_jalali TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','deposited','cleared','bounced','cancelled')),
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  settled_at TEXT
);
CREATE INDEX payment_checks_due ON payment_checks(status,due_date_jalali);
CREATE TABLE payment_check_events (
  id INTEGER PRIMARY KEY,
  check_id INTEGER NOT NULL REFERENCES payment_checks(id),
  old_status TEXT NOT NULL,
  new_status TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
