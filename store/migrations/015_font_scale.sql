ALTER TABLE shop_settings ADD COLUMN font_scale_percent INTEGER NOT NULL DEFAULT 100
  CHECK (font_scale_percent BETWEEN 90 AND 125);
