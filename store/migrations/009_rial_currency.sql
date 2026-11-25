-- Switch active accounting to exact integer Rials. Historical Parsian tables already store Rials.
ALTER TABLE product_prices RENAME COLUMN amount_toman TO amount_rial;
UPDATE product_prices SET amount_rial=amount_rial*10;
ALTER TABLE price_changes RENAME COLUMN old_amount_toman TO old_amount_rial;
ALTER TABLE price_changes RENAME COLUMN new_amount_toman TO new_amount_rial;
UPDATE price_changes SET old_amount_rial=old_amount_rial*10, new_amount_rial=new_amount_rial*10;
ALTER TABLE stock_balances RENAME COLUMN avg_cost_toman TO avg_cost_rial;
UPDATE stock_balances SET avg_cost_rial=avg_cost_rial*10;
ALTER TABLE invoices RENAME COLUMN total_toman TO total_rial;
UPDATE invoices SET total_rial=total_rial*10;
ALTER TABLE invoice_lines RENAME COLUMN unit_price_toman TO unit_price_rial;
ALTER TABLE invoice_lines RENAME COLUMN unit_cost_toman TO unit_cost_rial;
ALTER TABLE invoice_lines RENAME COLUMN line_total_toman TO line_total_rial;
UPDATE invoice_lines SET unit_price_rial=unit_price_rial*10, unit_cost_rial=unit_cost_rial*10, line_total_rial=line_total_rial*10;
ALTER TABLE money_movements RENAME COLUMN amount_toman TO amount_rial;
UPDATE money_movements SET amount_rial=amount_rial*10;
ALTER TABLE sales_returns RENAME COLUMN total_toman TO total_rial;
ALTER TABLE sales_returns RENAME COLUMN cost_total_toman TO cost_total_rial;
UPDATE sales_returns SET total_rial=total_rial*10, cost_total_rial=cost_total_rial*10;
ALTER TABLE sales_return_lines RENAME COLUMN unit_price_toman TO unit_price_rial;
ALTER TABLE sales_return_lines RENAME COLUMN unit_cost_toman TO unit_cost_rial;
ALTER TABLE sales_return_lines RENAME COLUMN line_total_toman TO line_total_rial;
UPDATE sales_return_lines SET unit_price_rial=unit_price_rial*10, unit_cost_rial=unit_cost_rial*10, line_total_rial=line_total_rial*10;
ALTER TABLE sales_return_refunds RENAME COLUMN amount_toman TO amount_rial;
UPDATE sales_return_refunds SET amount_rial=amount_rial*10;
ALTER TABLE payment_checks RENAME COLUMN amount_toman TO amount_rial;
UPDATE payment_checks SET amount_rial=amount_rial*10;
ALTER TABLE purchase_returns RENAME COLUMN total_toman TO total_rial;
ALTER TABLE purchase_returns RENAME COLUMN stock_cost_toman TO stock_cost_rial;
UPDATE purchase_returns SET total_rial=total_rial*10, stock_cost_rial=stock_cost_rial*10;
ALTER TABLE purchase_return_lines RENAME COLUMN unit_purchase_toman TO unit_purchase_rial;
ALTER TABLE purchase_return_lines RENAME COLUMN unit_stock_cost_toman TO unit_stock_cost_rial;
ALTER TABLE purchase_return_lines RENAME COLUMN line_total_toman TO line_total_rial;
UPDATE purchase_return_lines SET unit_purchase_rial=unit_purchase_rial*10, unit_stock_cost_rial=unit_stock_cost_rial*10, line_total_rial=line_total_rial*10;
ALTER TABLE purchase_return_refunds RENAME COLUMN amount_toman TO amount_rial;
UPDATE purchase_return_refunds SET amount_rial=amount_rial*10;
-- Restore exact source Rials where the first import rounded to Toman.
UPDATE product_prices SET amount_rial=(SELECT s.last_sale_rial FROM products p
 JOIN legacy_price_suggestions s ON s.product_code=p.sku AND s.source_db='db_1405'
 WHERE p.id=product_prices.product_id ORDER BY s.import_id DESC LIMIT 1)
 WHERE price_type_id=(SELECT id FROM price_types WHERE code='parsian_last_sale')
 AND EXISTS(SELECT 1 FROM products p JOIN legacy_price_suggestions s ON s.product_code=p.sku
 WHERE p.id=product_prices.product_id AND s.source_db='db_1405' AND s.last_sale_rial>0)
 AND NOT EXISTS(SELECT 1 FROM price_changes c WHERE c.product_id=product_prices.product_id AND c.price_type_id=product_prices.price_type_id);
UPDATE stock_balances SET avg_cost_rial=(SELECT s.last_purchase_rial FROM products p
 JOIN legacy_price_suggestions s ON s.product_code=p.sku AND s.source_db='db_1405'
 WHERE p.id=stock_balances.product_id ORDER BY s.import_id DESC LIMIT 1)
 WHERE EXISTS(SELECT 1 FROM products p JOIN legacy_price_suggestions s ON s.product_code=p.sku
 WHERE p.id=stock_balances.product_id AND p.cost_confirmed=0 AND s.source_db='db_1405'
 AND s.last_purchase_rial>0)
 AND NOT EXISTS(SELECT 1 FROM invoice_lines l WHERE l.product_id=stock_balances.product_id);
