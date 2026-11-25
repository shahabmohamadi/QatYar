CREATE TABLE branches (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO branches(id, name) VALUES (1, 'شعبه اصلی');

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner','manager','clerk')),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  csrf_token TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE parties (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('customer','supplier','both')),
  phone TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  brand TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  min_stock INTEGER NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE price_types (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL UNIQUE,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);
INSERT INTO price_types(code,label) VALUES
  ('retail','تکی'),('wholesale','همکاری'),('repair','تعمیرکار');
CREATE TABLE product_prices (
  product_id INTEGER NOT NULL REFERENCES products(id),
  price_type_id INTEGER NOT NULL REFERENCES price_types(id),
  amount_toman INTEGER NOT NULL CHECK (amount_toman >= 0),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by INTEGER NOT NULL REFERENCES users(id),
  PRIMARY KEY(product_id,price_type_id)
);
CREATE TABLE price_changes (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  price_type_id INTEGER NOT NULL REFERENCES price_types(id),
  old_amount_toman INTEGER,
  new_amount_toman INTEGER,
  changed_by INTEGER NOT NULL REFERENCES users(id),
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE stock_balances (
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  avg_cost_toman INTEGER NOT NULL DEFAULT 0 CHECK (avg_cost_toman >= 0),
  PRIMARY KEY(branch_id,product_id)
);
CREATE TABLE stock_movements (
  id INTEGER PRIMARY KEY,
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  delta_qty INTEGER NOT NULL CHECK (delta_qty <> 0),
  reason TEXT NOT NULL CHECK (reason IN ('purchase','sale','adjustment')),
  invoice_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE invoices (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  kind TEXT NOT NULL CHECK (kind IN ('sale','purchase')),
  party_id INTEGER NOT NULL REFERENCES parties(id),
  total_toman INTEGER NOT NULL CHECK (total_toman > 0),
  status TEXT NOT NULL DEFAULT 'posted' CHECK (status = 'posted'),
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE invoice_lines (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_toman INTEGER NOT NULL CHECK (unit_price_toman >= 0),
  unit_cost_toman INTEGER NOT NULL CHECK (unit_cost_toman >= 0),
  line_total_toman INTEGER NOT NULL CHECK (line_total_toman >= 0),
  price_type_id INTEGER REFERENCES price_types(id)
);
CREATE INDEX invoice_party_idx ON invoices(party_id,created_at);
CREATE INDEX stock_movement_product_idx ON stock_movements(product_id,created_at);

CREATE TABLE money_movements (
  id INTEGER PRIMARY KEY,
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  invoice_id INTEGER REFERENCES invoices(id),
  party_id INTEGER REFERENCES parties(id),
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  method TEXT NOT NULL CHECK (method IN ('cash','bank')),
  category TEXT NOT NULL CHECK (category IN ('invoice','income','expense')),
  amount_toman INTEGER NOT NULL CHECK (amount_toman > 0),
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK ((category = 'invoice' AND invoice_id IS NOT NULL AND party_id IS NOT NULL)
      OR (category <> 'invoice' AND invoice_id IS NULL))
);
CREATE INDEX money_movement_party_idx ON money_movements(party_id,created_at);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  actor_user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id INTEGER,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
