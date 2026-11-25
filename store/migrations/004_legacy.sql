CREATE TABLE legacy_imports (
  id INTEGER PRIMARY KEY,
  source_sha256 TEXT NOT NULL UNIQUE,
  source_created_at TEXT NOT NULL,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  money_unit TEXT NOT NULL CHECK (money_unit='rial'),
  record_count INTEGER NOT NULL,
  warning_count INTEGER NOT NULL DEFAULT 0,
  warning_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE legacy_records (
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  source_db TEXT NOT NULL,
  table_name TEXT NOT NULL,
  row_number INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(import_id,source_db,table_name,row_number)
);
CREATE INDEX legacy_records_lookup ON legacy_records(source_db,table_name);
CREATE TABLE legacy_invoices (
  id INTEGER PRIMARY KEY,
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  source_db TEXT NOT NULL,
  title_code INTEGER NOT NULL,
  factor_num INTEGER NOT NULL,
  date_jalali TEXT NOT NULL DEFAULT '',
  party_key TEXT NOT NULL DEFAULT '',
  party_name TEXT NOT NULL DEFAULT '',
  total_rial INTEGER NOT NULL DEFAULT 0,
  lines_count INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(import_id,source_db,title_code,factor_num)
);
CREATE INDEX legacy_invoices_filter ON legacy_invoices(source_db,title_code,date_jalali);
CREATE TABLE legacy_invoice_lines (
  id INTEGER PRIMARY KEY,
  legacy_invoice_id INTEGER NOT NULL REFERENCES legacy_invoices(id),
  source_row_id INTEGER NOT NULL,
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL,
  unit_price_rial INTEGER NOT NULL,
  line_total_rial INTEGER NOT NULL
);
CREATE INDEX legacy_lines_invoice ON legacy_invoice_lines(legacy_invoice_id);
CREATE TABLE legacy_journal (
  id INTEGER PRIMARY KEY,
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  source_db TEXT NOT NULL,
  source_row_id INTEGER NOT NULL,
  document_num INTEGER NOT NULL,
  date_jalali TEXT NOT NULL DEFAULT '',
  kol_code INTEGER NOT NULL,
  moeen_code INTEGER NOT NULL,
  tafsili_code INTEGER NOT NULL,
  account_name TEXT NOT NULL DEFAULT '',
  debit_rial INTEGER NOT NULL,
  credit_rial INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX legacy_journal_account ON legacy_journal(source_db,kol_code,moeen_code,tafsili_code);
CREATE INDEX legacy_journal_document ON legacy_journal(source_db,document_num);
CREATE TABLE legacy_checks (
  id INTEGER PRIMARY KEY,
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  source_db TEXT NOT NULL,
  source_row_id INTEGER NOT NULL,
  check_number TEXT NOT NULL DEFAULT '',
  bank_name TEXT NOT NULL DEFAULT '',
  amount_rial INTEGER NOT NULL,
  due_date_jalali TEXT NOT NULL DEFAULT '',
  received_date_jalali TEXT NOT NULL DEFAULT '',
  kind_code INTEGER NOT NULL,
  status_code INTEGER NOT NULL,
  is_active INTEGER NOT NULL,
  is_cancelled INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX legacy_checks_date ON legacy_checks(source_db,due_date_jalali);
CREATE TABLE legacy_stock (
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  source_db TEXT NOT NULL,
  product_code TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  PRIMARY KEY(import_id,source_db,product_code)
);
CREATE TABLE legacy_price_suggestions (
  import_id INTEGER NOT NULL REFERENCES legacy_imports(id),
  source_db TEXT NOT NULL,
  product_code TEXT NOT NULL,
  last_sale_rial INTEGER NOT NULL DEFAULT 0,
  last_sale_date TEXT NOT NULL DEFAULT '',
  last_purchase_rial INTEGER NOT NULL DEFAULT 0,
  last_purchase_date TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(import_id,source_db,product_code)
);
