UPDATE price_types SET label='آخرین قیمت ثبت‌شده'
 WHERE code='parsian_last_sale'
   AND NOT EXISTS (SELECT 1 FROM price_types WHERE label='آخرین قیمت ثبت‌شده');
UPDATE price_types SET label='آخرین قیمت ثبت‌شده (انتقالی)'
 WHERE code='parsian_last_sale' AND label LIKE '%پارسیان%';
UPDATE stock_movements SET note=replace(note,'پارسیان','دورهٔ قبل')
 WHERE instr(note,'پارسیان')>0;
UPDATE parties SET note=replace(note,'پارسیان','انتقال')
 WHERE instr(note,'پارسیان')>0;
