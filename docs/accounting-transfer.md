# قالب انتقال اطلاعات حسابداری

در «تنظیمات ← بکاپ و انتقال داده» می‌توان یک بستهٔ ZIP انتقال را روی **نصب تازه و خالی** وارد کرد. قالب خالی از همان صفحه قابل دانلود است. ورود به‌صورت یک تراکنش انجام می‌شود؛ اگر اعتبارسنجی یک ردیف خطا بدهد، هیچ داده‌ای ثبت نمی‌شود.

بکاپ بومی محصولات مختلف ساختار یکسانی ندارد. این قالب برای خروجی CSV یا مبدل اختصاصی هر محصول است؛ فایل بومیِ ناشناخته مستقیماً قابل خواندن نیست. بدون نمونهٔ بکاپ، پشتیبانی از فرمت بومی را نمی‌توان تأیید کرد.

## ساختار ZIP

- `manifest.json`: `format` برابر `qatyar-accounting-transfer`، `format_version` برابر `1`، `money_unit` برابر `rial` یا `toman`، `calendar` برابر `jalali` یا `gregorian`، `fiscal_year` چهاررقمی، و مانده‌های اختیاری `cash_opening` و `bank_opening`.
- `products.csv` و `parties.csv` الزامی هستند. `invoices.csv` و `invoice_lines.csv` باید با هم باشند. `checks.csv` و `journal.csv` اختیاری هستند.
- فایل‌های CSV باید UTF-8 باشند؛ UTF-8 BOM هم پذیرفته می‌شود. سطر اول باید دقیقاً نام ستون‌های قالب دانلودی را داشته باشد. واحد همهٔ مبلغ‌های یک بسته مطابق `money_unit` است و برنامه در پایگاه داده ریال ذخیره می‌کند.

| فایل | ستون‌ها | معنی مهم |
| --- | --- | --- |
| `products.csv` | `sku,name,brand,category,quantity,avg_cost,sale_price,cost_confirmed` | موجودی آغاز کار؛ `cost_confirmed` برابر `0` یا `1` |
| `parties.csv` | `code,name,kind,phone,opening_balance` | `kind`: `customer`، `supplier` یا `both`؛ ماندهٔ مثبت طلب فروشگاه و ماندهٔ منفی بدهی فروشگاه است |
| `invoices.csv` | `document_id,kind,number,date,party_code,total,note` | `kind`: `sale`، `purchase`، `sale_return` یا `purchase_return` |
| `invoice_lines.csv` | `document_id,sku,quantity,unit_price,line_total` | `document_id` و `sku` باید در فایل‌های دیگر وجود داشته باشند؛ جمع ردیف‌ها باید با مبلغ فاکتور برابر باشد |
| `checks.csv` | `number,direction,party_code,bank_name,amount,due_date,status,note` | `direction`: `received` یا `issued`؛ `status`: `pending`، `deposited`، `bounced`، `cleared` یا `cancelled` |
| `journal.csv` | `document_number,date,kol_code,moeen_code,tafsili_code,account_name,debit,credit,note` | جمع بدهکار و بستانکار هر شماره سند باید برابر باشد |

تاریخ‌ها می‌توانند `1405/09/04` یا `2026-11-25` باشند. انتخاب تقویم در تنظیمات، نمایش و ورود تاریخ را کنترل می‌کند؛ داده‌های ذخیره‌شده جابه‌جا نمی‌شوند.

فاکتورهای قبلی در فهرست اصلی فاکتورها نمایش داده می‌شوند و برای جلوگیری از ثبت دوبارهٔ موجودی یا بدهی فقط‌خواندنی‌اند. ماندهٔ ابتدای دوره در حساب طرف‌حساب و صندوق/بانک منظور می‌شود. چک‌های باز وارد جریان فعال می‌شوند؛ چک‌های وصول‌شده یا ابطال‌شده فقط برای بررسی حفظ می‌شوند. ردیف‌های دفتر برای تطبیق حفظ می‌شوند و بدون نگاشت نوع حساب، از آنها سود قطعی استنتاج نمی‌شود.
