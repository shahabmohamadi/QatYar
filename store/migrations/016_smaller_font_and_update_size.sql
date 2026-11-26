CREATE TABLE shop_settings_next (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  app_name TEXT NOT NULL DEFAULT 'قطعه‌یار' CHECK (length(app_name) BETWEEN 2 AND 80),
  app_tagline TEXT NOT NULL DEFAULT 'مدیریت فروشگاه قطعات موبایل' CHECK (length(app_tagline) BETWEEN 2 AND 160),
  shop_name TEXT NOT NULL DEFAULT 'فروشگاه قطعات موبایل' CHECK (length(shop_name) BETWEEN 2 AND 120),
  shop_phone TEXT NOT NULL DEFAULT '',
  shop_address TEXT NOT NULL DEFAULT '',
  default_min_stock INTEGER NOT NULL DEFAULT 0 CHECK (default_min_stock BETWEEN 0 AND 100000000),
  default_price_type_id INTEGER NOT NULL DEFAULT 1 REFERENCES price_types(id),
  sale_prefix TEXT NOT NULL DEFAULT 'F-' CHECK (length(sale_prefix) BETWEEN 1 AND 8),
  purchase_prefix TEXT NOT NULL DEFAULT 'K-' CHECK (length(purchase_prefix) BETWEEN 1 AND 8),
  invoice_digits INTEGER NOT NULL DEFAULT 6 CHECK (invoice_digits BETWEEN 4 AND 12),
  default_payment_method TEXT NOT NULL DEFAULT 'cash' CHECK (default_payment_method IN ('cash','bank')),
  cash_label TEXT NOT NULL DEFAULT 'نقد' CHECK (length(cash_label) BETWEEN 2 AND 40),
  bank_label TEXT NOT NULL DEFAULT 'بانک / کارت‌خوان' CHECK (length(bank_label) BETWEEN 2 AND 40),
  session_hours INTEGER NOT NULL DEFAULT 12 CHECK (session_hours BETWEEN 1 AND 72),
  login_max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (login_max_attempts BETWEEN 3 AND 10),
  login_lock_minutes INTEGER NOT NULL DEFAULT 10 CHECK (login_lock_minutes BETWEEN 1 AND 60),
  backup_enabled INTEGER NOT NULL DEFAULT 1 CHECK (backup_enabled IN (0,1)),
  backup_retention_days INTEGER NOT NULL DEFAULT 30 CHECK (backup_retention_days BETWEEN 1 AND 365),
  backup_directory TEXT NOT NULL DEFAULT '',
  logo_content BLOB,
  logo_mime TEXT CHECK (logo_mime IS NULL OR logo_mime IN ('image/png','image/jpeg','image/webp')),
  logo_revision INTEGER NOT NULL DEFAULT 1,
  updated_by INTEGER REFERENCES users(id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  calendar TEXT NOT NULL DEFAULT 'jalali' CHECK (calendar IN ('jalali','gregorian')),
  font_scale_percent INTEGER NOT NULL DEFAULT 100 CHECK (font_scale_percent BETWEEN 75 AND 125),
  CHECK ((logo_content IS NULL) = (logo_mime IS NULL))
);
INSERT INTO shop_settings_next SELECT * FROM shop_settings;
DROP TABLE shop_settings;
ALTER TABLE shop_settings_next RENAME TO shop_settings;

ALTER TABLE update_settings ADD COLUMN asset_size INTEGER NOT NULL DEFAULT 0 CHECK (asset_size BETWEEN 0 AND 200000000);
