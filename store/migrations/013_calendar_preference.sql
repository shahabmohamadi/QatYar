ALTER TABLE shop_settings ADD COLUMN calendar TEXT NOT NULL DEFAULT 'jalali'
  CHECK (calendar IN ('jalali','gregorian'));
