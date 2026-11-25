"""Validated accounting transfer bundles and an extensible import entry point."""
import csv
from datetime import datetime, timezone
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import secrets
import sqlite3
import zipfile

from flask import Blueprint, flash, redirect, request, send_file, url_for

from .auth import require_role
from .calendar import format_date, parse_date
from .db import connect, data_dir, migrate, transaction
from .legacy_import import import_parsian_archive
from .search import normalize_code, normalize_name
from .utils import audit

transfer = Blueprint('transfer', __name__)
FORMAT = 'qatyar-accounting-transfer'
MAX_ARCHIVE = 30_000_000
MAX_UNCOMPRESSED = 100_000_000
HEADERS = {
    'products.csv': ('sku', 'name', 'brand', 'category', 'quantity', 'avg_cost', 'sale_price', 'cost_confirmed'),
    'parties.csv': ('code', 'name', 'kind', 'phone', 'opening_balance'),
    'invoices.csv': ('document_id', 'kind', 'number', 'date', 'party_code', 'total', 'note'),
    'invoice_lines.csv': ('document_id', 'sku', 'quantity', 'unit_price', 'line_total'),
    'checks.csv': ('number', 'direction', 'party_code', 'bank_name', 'amount', 'due_date', 'status', 'note'),
    'journal.csv': ('document_number', 'date', 'kol_code', 'moeen_code', 'tafsili_code',
                    'account_name', 'debit', 'credit', 'note'),
}
KINDS = {'sale': 1, 'purchase': 2, 'purchase_return': 3, 'sale_return': 4}
CHECK_STATUS = {'pending': 0, 'deposited': 1, 'cleared': 2, 'bounced': 3, 'cancelled': 4}
_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def _number(value, label, *, negative=False, maximum=10**15):
    raw = str(value if value is not None else '').strip().translate(_DIGITS).replace(',', '').replace('٬', '')
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f'{label} باید عدد صحیح باشد.') from exc
    if abs(number) > maximum or (number < 0 and not negative):
        raise ValueError(f'{label} خارج از محدوده است.')
    return number


def _rows(archive, name):
    if name not in archive.namelist():
        return []
    text = archive.read(name).decode('utf-8-sig')
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames or not set(HEADERS[name]).issubset(reader.fieldnames):
        raise ValueError(f'ستون‌های فایل {name} کامل نیست.')
    rows = list(reader)
    if len(rows) > 100_000:
        raise ValueError(f'تعداد ردیف‌های {name} بیش از حد مجاز است.')
    return rows


def archive_format(path):
    if Path(path).stat().st_size > MAX_ARCHIVE:
        raise ValueError('حجم فایل انتقال بیش از ۳۰ مگابایت است؛ از ابزار خط فرمان استفاده کنید.')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 80:
            raise ValueError('ساختار فایل انتقال معتبر نیست.')
        if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED:
            raise ValueError('حجم بازشدهٔ فایل انتقال بیش از حد مجاز است.')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > 64_000:
            raise ValueError('شناسهٔ قالب فایل انتقال پیدا نشد.')
        manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
        if not isinstance(manifest, dict):
            raise ValueError('شناسهٔ قالب فایل انتقال معتبر نیست.')
        return manifest.get('format')


def load_standard(path):
    if archive_format(path) != FORMAT:
        raise ValueError('قالب فایل انتقال پشتیبانی نمی‌شود.')
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if not {'manifest.json', 'products.csv', 'parties.csv'} <= names or not names <= {'manifest.json', *HEADERS}:
            raise ValueError('فهرست فایل‌های انتقال معتبر نیست.')
        manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
        if manifest.get('format_version') != 1 or manifest.get('money_unit') not in ('rial', 'toman'):
            raise ValueError('نسخه یا واحد پول فایل انتقال پشتیبانی نمی‌شود.')
        if manifest.get('calendar') not in ('jalali', 'gregorian'):
            raise ValueError('تقویم فایل انتقال مشخص نیست.')
        year = str(manifest.get('fiscal_year', '')).translate(_DIGITS)
        if not re.fullmatch(r'\d{4}', year):
            raise ValueError('سال مالی فایل انتقال نامعتبر است.')
        if ('invoices.csv' in names) != ('invoice_lines.csv' in names):
            raise ValueError('فاکتورها و ردیف‌هایشان باید با هم ارائه شوند.')
        return manifest, year, {name: _rows(archive, name) for name in HEADERS}


def import_standard(path):
    """Import one exact transfer into an otherwise empty shop, atomically."""
    manifest, year, rows = load_standard(path)
    multiplier = 10 if manifest['money_unit'] == 'toman' else 1
    def money(raw, label, negative=False):
        result = _number(raw, label, negative=negative) * multiplier
        if abs(result) > 10**15:
            raise ValueError(f'{label} خارج از محدوده است.')
        return result
    if not any(rows.values()):
        raise ValueError('فایل انتقال هیچ داده‌ای ندارد.')
    products = {}
    for row in rows['products.csv']:
        sku = (row['sku'] or '').strip()
        name = (row['name'] or '').strip()
        if not sku or len(sku) > 80 or not name or len(name) > 160 or sku.casefold() in products:
            raise ValueError('کد یا نام کالا خالی، تکراری یا بیش از حد بلند است.')
        if row['cost_confirmed'] not in ('0', '1'):
            raise ValueError('وضعیت تأیید بهای کالا باید ۰ یا ۱ باشد.')
        products[sku.casefold()] = (sku, name, (row['brand'] or '')[:80], (row['category'] or '')[:80],
                                    _number(row['quantity'], 'موجودی کالا', negative=True, maximum=10**9),
                                    money(row['avg_cost'], 'بهای کالا'), money(row['sale_price'], 'قیمت فروش'),
                                    row['cost_confirmed'] == '1')
    parties = {}
    for row in rows['parties.csv']:
        code = (row['code'] or '').strip()
        name = (row['name'] or '').strip()
        if not code or len(code) > 80 or not name or len(name) > 160 or code.casefold() in parties:
            raise ValueError('کد یا نام طرف‌حساب خالی، تکراری یا بیش از حد بلند است.')
        if row['kind'] not in ('customer', 'supplier', 'both'):
            raise ValueError('نوع طرف‌حساب باید customer، supplier یا both باشد.')
        parties[code.casefold()] = (code, name, row['kind'], (row['phone'] or '')[:30],
                                    money(row['opening_balance'], 'مانده طرف‌حساب', negative=True))
    documents = {}
    for row in rows['invoices.csv']:
        key = (row['document_id'] or '').strip()
        if not key or key in documents or row['kind'] not in KINDS or len(key) > 80:
            raise ValueError('شناسه یا نوع فاکتور نامعتبر یا تکراری است.')
        party_code = (row['party_code'] or '').strip().casefold()
        if party_code not in parties:
            raise ValueError('طرف‌حساب یکی از فاکتورها پیدا نشد.')
        number = (row['number'] or '').strip()
        if not number or len(number) > 80:
            raise ValueError('شماره فاکتور نامعتبر است.')
        day = format_date(parse_date(row['date'], manifest['calendar']), 'jalali')
        documents[key] = {'kind': KINDS[row['kind']], 'number': number, 'day': day,
                          'party': parties[party_code], 'total': money(row['total'], 'مبلغ فاکتور'),
                          'note': (row['note'] or '')[:500], 'lines': []}
        if documents[key]['total'] <= 0:
            raise ValueError('مبلغ فاکتور باید مثبت باشد.')
    for row in rows['invoice_lines.csv']:
        key = (row['document_id'] or '').strip()
        sku = (row['sku'] or '').strip().casefold()
        if key not in documents or sku not in products:
            raise ValueError('فاکتور یا کالای یکی از ردیف‌ها پیدا نشد.')
        quantity = _number(row['quantity'], 'تعداد ردیف', maximum=10**9)
        price = money(row['unit_price'], 'قیمت ردیف')
        total = money(row['line_total'], 'جمع ردیف')
        if not quantity or total != quantity * price:
            raise ValueError('جمع یکی از ردیف‌های فاکتور با تعداد و قیمت برابر نیست.')
        documents[key]['lines'].append((products[sku], quantity, price, total))
    for item in documents.values():
        if not item['lines'] or item['total'] != sum(line[3] for line in item['lines']):
            raise ValueError('مبلغ فاکتور با جمع ردیف‌های آن برابر نیست.')
    checks = []
    for row in rows['checks.csv']:
        direction = row['direction']
        status = row['status']
        party_code = (row['party_code'] or '').strip().casefold()
        if direction not in ('received', 'issued') or status not in CHECK_STATUS:
            raise ValueError('جهت یا وضعیت چک نامعتبر است.')
        if party_code and party_code not in parties:
            raise ValueError('طرف‌حساب یکی از چک‌ها پیدا نشد.')
        number = (row['number'] or '').strip()
        if not number or len(number) > 50:
            raise ValueError('شماره چک نامعتبر است.')
        amount = money(row['amount'], 'مبلغ چک')
        if amount <= 0:
            raise ValueError('مبلغ چک باید مثبت باشد.')
        checks.append((number, direction, party_code, (row['bank_name'] or '')[:100],
                       amount,
                       format_date(parse_date(row['due_date'], manifest['calendar']), 'jalali'),
                       status, (row['note'] or '')[:240]))
    journal = []
    document_sums = {}
    for row in rows['journal.csv']:
        number = _number(row['document_number'], 'شماره سند', maximum=10**9)
        debit = money(row['debit'], 'بدهکار سند')
        credit = money(row['credit'], 'بستانکار سند')
        if not number or (not debit and not credit) or (debit and credit):
            raise ValueError('ردیف سند حسابداری معتبر نیست.')
        document_sums[number] = document_sums.get(number, 0) + debit - credit
        journal.append((number, format_date(parse_date(row['date'], manifest['calendar']), 'jalali'),
                        _number(row['kol_code'], 'کد کل', maximum=10**9),
                        _number(row['moeen_code'], 'کد معین', maximum=10**9),
                        _number(row['tafsili_code'], 'کد تفصیلی', maximum=10**9),
                        (row['account_name'] or '')[:160], debit, credit, (row['note'] or '')[:500]))
    if any(document_sums.values()):
        raise ValueError('جمع بدهکار و بستانکار یکی از اسناد برابر نیست.')
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    migrate()
    db = connect()
    try:
        with transaction(db):
            if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in
                   ('products', 'parties', 'invoices', 'money_movements', 'legacy_imports', 'payment_checks')):
                raise ValueError('ورود داده فقط روی فروشگاه تازه و خالی مجاز است.')
            owner = db.execute("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
            if not owner:
                raise ValueError('ابتدا حساب مالک برنامه را بسازید.')
            source_db = 'transfer_' + year
            count = sum(len(item) for item in rows.values())
            imported = db.execute('''INSERT INTO legacy_imports(source_sha256,source_created_at,money_unit,
                record_count,warning_json) VALUES (?,?,?,?,?)''',
                (digest, datetime.now(timezone.utc).isoformat(), 'rial', count, '{}')).lastrowid
            product_ids = {}
            for sku_key, (sku, name, brand, category, quantity, cost, price, confirmed) in products.items():
                product_id = db.execute('''INSERT INTO products(sku,name,brand,category,min_stock,
                    search_name,search_sku,cost_confirmed) VALUES (?,?,?,?,0,?,?,?)''',
                    (sku, name, brand, category,
                     normalize_name(' '.join((name, brand, category))), normalize_code(sku), int(confirmed))).lastrowid
                product_ids[sku_key] = product_id
                db.execute('INSERT INTO stock_balances(branch_id,product_id,quantity,avg_cost_rial) VALUES (1,?,?,?)',
                           (product_id, quantity, cost))
                db.execute('INSERT INTO legacy_stock(import_id,source_db,product_code,quantity) VALUES (?,?,?,?)',
                           (imported, source_db, sku, quantity))
                if quantity:
                    db.execute('''INSERT INTO stock_movements(branch_id,product_id,delta_qty,reason,note,created_by)
                        VALUES (1,? ,?,'opening','موجودی ابتدای دوره',?)''',
                        (product_id, quantity, owner['id']))
                if price:
                    db.execute('''INSERT INTO product_prices(product_id,price_type_id,amount_rial,updated_by)
                        VALUES (?,1,?,?)''', (product_id, price, owner['id']))
            party_ids = {}
            for code_key, (code, name, kind, phone, balance) in parties.items():
                party_id = db.execute('INSERT INTO parties(name,kind,phone) VALUES (?,?,?)',
                                      (name, kind, phone)).lastrowid
                party_ids[code_key] = party_id
                if balance:
                    db.execute('INSERT INTO opening_party_balances(party_id,import_id,amount_rial) VALUES (?,?,?)',
                               (party_id, imported, balance))
            for code, key in (('cash_opening', 10), ('bank_opening', 11)):
                amount = money(manifest.get(code, '0'), 'مانده حساب', negative=True)
                db.execute('INSERT INTO opening_account_balances(account_code,import_id,amount_rial) VALUES (?,?,?)',
                           (key, imported, amount))
            for item in documents.values():
                party = item['party']
                doc_id = db.execute('''INSERT INTO legacy_invoices(import_id,source_db,title_code,factor_num,
                    date_jalali,party_key,party_name,total_rial,lines_count,note)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (imported, source_db, item['kind'], item['number'], item['day'], party[0], party[1],
                     item['total'], len(item['lines']), item['note'])).lastrowid
                for index, (product, quantity, price, total) in enumerate(item['lines'], 1):
                    db.execute('''INSERT INTO legacy_invoice_lines(legacy_invoice_id,source_row_id,product_code,
                        product_name,quantity,unit_price_rial,line_total_rial) VALUES (?,?,?,?,?,?,?)''',
                        (doc_id, index, product[0], product[1], quantity, price, total))
            for index, (number, direction, party_code, bank, amount, due, status, note) in enumerate(checks, 1):
                if status in ('pending', 'deposited', 'bounced'):
                    db.execute('''INSERT INTO payment_checks(direction,party_id,check_number,bank_name,
                        amount_rial,due_date_jalali,status,note,created_by)
                        VALUES (?,?,?,?,?,?,?,?,?)''',
                        (direction, party_ids.get(party_code), number, bank, amount, due, status, note, owner['id']))
                else:
                    db.execute('''INSERT INTO legacy_checks(import_id,source_db,source_row_id,check_number,
                        bank_name,amount_rial,due_date_jalali,kind_code,status_code,is_active,is_cancelled,note)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (imported, source_db, index, number, bank, amount, due,
                         1 if direction == 'received' else 2, CHECK_STATUS[status], 1, int(status == 'cancelled'), note))
            for index, (number, day, kol, moeen, tafsili, account, debit, credit, note) in enumerate(journal, 1):
                db.execute('''INSERT INTO legacy_journal(import_id,source_db,source_row_id,document_num,
                    date_jalali,kol_code,moeen_code,tafsili_code,account_name,debit_rial,credit_rial,note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (imported, source_db, index, number, day, kol, moeen, tafsili,
                     account, debit, credit, note))
            audit(db, 'transfer.import', 'legacy_import', imported,
                  {'products': len(products), 'parties': len(parties), 'invoices': len(documents),
                   'checks': len(checks), 'journal_lines': len(journal)})
        return {'products': len(products), 'parties': len(parties),
                'invoices': len(documents), 'checks': len(checks), 'journal_lines': len(journal)}
    finally:
        db.close()


@transfer.get('/settings/transfer/template')
@require_role('owner')
def download_template():
    output = BytesIO()
    manifest = {'format': FORMAT, 'format_version': 1, 'money_unit': 'rial',
                'calendar': 'jalali', 'fiscal_year': '1405',
                'cash_opening': '0', 'bank_opening': '0'}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, columns in HEADERS.items():
            archive.writestr(name, ','.join(columns) + '\n')
    output.seek(0)
    return send_file(output, mimetype='application/zip', as_attachment=True,
                     download_name='accounting-transfer-template.zip')


@transfer.post('/settings/transfer')
@require_role('owner')
def upload():
    uploaded = request.files.get('archive')
    if not uploaded or not uploaded.filename:
        flash('فایل انتقال انتخاب نشده است.', 'error')
        return redirect(url_for('settings.page', tab='backup'))
    path = data_dir() / ('transfer-' + secrets.token_hex(10) + '.zip')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as target:
            while block := uploaded.stream.read(1024 * 1024):
                target.write(block)
                if target.tell() > MAX_ARCHIVE:
                    raise ValueError('حجم فایل انتقال بیش از ۳۰ مگابایت است.')
        if not zipfile.is_zipfile(path):
            raise ValueError('فایل انتقال باید ZIP معتبر باشد.')
        kind = archive_format(path)
        if kind == FORMAT:
            result = import_standard(path)
        elif kind == 'qatyar-parsian-export':
            result = import_parsian_archive(path)
        else:
            raise ValueError('قالب این بکاپ شناخته نشد. برای آن باید مبدل منبع یا خروجی استاندارد آماده شود.')
        if kind == FORMAT:
            flash(f"انتقال کامل شد: {result['products']} کالا، {result['parties']} طرف‌حساب، "
                  f"{result['invoices']} فاکتور و {result['checks']} چک.", 'success')
        else:
            flash(f"انتقال کامل شد: {result['record_count']} ردیف ثبت شد. "
                  'موارد نیازمند تطبیق را در گزارش اسناد بررسی کنید.', 'success')
    except (ValueError, KeyError, TypeError, UnicodeError, OSError, sqlite3.Error, zipfile.BadZipFile, csv.Error) as exc:
        flash('ورود داده انجام نشد: ' + str(exc)[:180], 'error')
    finally:
        path.unlink(missing_ok=True)
    return redirect(url_for('settings.page', tab='backup'))
