CREATE TABLE purchase_returns (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  purchase_invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  party_id INTEGER NOT NULL REFERENCES parties(id),
  total_toman INTEGER NOT NULL CHECK (total_toman > 0),
  stock_cost_toman INTEGER NOT NULL CHECK (stock_cost_toman >= 0),
  reason TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE purchase_return_lines (
  id INTEGER PRIMARY KEY,
  return_id INTEGER NOT NULL REFERENCES purchase_returns(id),
  invoice_line_id INTEGER NOT NULL REFERENCES invoice_lines(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_purchase_toman INTEGER NOT NULL CHECK (unit_purchase_toman >= 0),
  unit_stock_cost_toman INTEGER NOT NULL CHECK (unit_stock_cost_toman >= 0),
  line_total_toman INTEGER NOT NULL CHECK (line_total_toman >= 0)
);
CREATE INDEX purchase_return_lines_origin ON purchase_return_lines(invoice_line_id);
CREATE TABLE purchase_return_refunds (
  id INTEGER PRIMARY KEY,
  return_id INTEGER NOT NULL REFERENCES purchase_returns(id),
  method TEXT NOT NULL CHECK (method IN ('cash','bank')),
  amount_toman INTEGER NOT NULL CHECK (amount_toman > 0),
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE stock_movements_next (
  id INTEGER PRIMARY KEY,
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  delta_qty INTEGER NOT NULL CHECK (delta_qty <> 0),
  reason TEXT NOT NULL CHECK (reason IN ('purchase','sale','adjustment','opening','sale_return','purchase_return')),
  invoice_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO stock_movements_next SELECT * FROM stock_movements;
DROP TABLE stock_movements;
ALTER TABLE stock_movements_next RENAME TO stock_movements;
CREATE INDEX stock_movement_product_idx ON stock_movements(product_id,created_at);
