ALTER TABLE products ADD COLUMN search_name TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN search_sku TEXT NOT NULL DEFAULT '';
CREATE INDEX products_search_sku_idx ON products(search_sku);
