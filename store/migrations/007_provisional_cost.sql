ALTER TABLE invoice_lines ADD COLUMN cost_provisional INTEGER NOT NULL DEFAULT 0
  CHECK (cost_provisional IN (0,1));
