"""Import a verified Parsian SQL Server export without changing its source databases."""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import zipfile

from .db import connect, migrate, transaction
from .search import normalize_code, normalize_name

TABLES = (
    'tbl_Goods', 'tbl_GoodsGroup', 'tbl_Factor', 'tbl_FactNum', 'tbl_Document',
    'tbl_Tafsili', 'tbl_Kol', 'tbl_Moeen', 'tbl_Telephone', 'tbl_Check',
    'tbl_Storehouse', 'tbl_FactorKind', 'tbl_Unit', 'tbl_AccSetting',
)
SOURCE_DBS = ('db_1404', 'db_1405')


def number(value):
    return Decimal(str(value if value is not None else 0))


def as_int(value, label, warnings):
    amount = number(value)
    rounded = int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if amount != rounded:
        warnings[label + '.fractional_rial'] += 1
    return rounded


def code(value):
    amount = number(value)
    if amount != int(amount):
        raise ValueError('کد کالای کسری در فایل پارسیان پیدا شد.')
    return str(int(amount))


def source_archive(path):
    """Read only the expected tables and verify every exported row count."""
    archive = Path(path)
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise ValueError('آرشیو پارسیان خراب است.')
        manifest = json.loads(source.read('manifest.json').decode('utf-8'))
        if manifest.get('format') != 'qatyar-parsian-export' or manifest.get('format_version') != 1 or manifest.get('money_unit') != 'rial':
            raise ValueError('نوع یا واحد پول آرشیو پارسیان معتبر نیست.')
        dbs = {item['name']: item['tables'] for item in manifest['databases']}
        if set(dbs) != set(SOURCE_DBS):
            raise ValueError('هر دو سال مالی پارسیان باید در آرشیو باشند.')
        expected_names = {'manifest.json'} | {f'{db}-{table}.jsonl' for db in SOURCE_DBS for table in TABLES}
        if set(source.namelist()) != expected_names:
            raise ValueError('فهرست فایل‌های آرشیو پارسیان معتبر نیست.')
        rows = {}
        raw = {}
        for db in SOURCE_DBS:
            if set(dbs[db]) != set(TABLES):
                raise ValueError('فهرست جدول‌های پارسیان کامل نیست.')
            rows[db] = {}
            for table in TABLES:
                lines = source.read(f'{db}-{table}.jsonl').decode('utf-8').splitlines()
                if len(lines) != dbs[db][table]:
                    raise ValueError(f'شمار ردیف‌های {db}.{table} با گزارش صادرات یکسان نیست.')
                raw[db, table] = lines
                rows[db][table] = [json.loads(line, parse_float=Decimal) for line in lines]
        return manifest, rows, raw


def _account_key(kol, moeen, tafsili):
    return f'{int(kol or 0)}/{int(moeen or 0)}/{int(tafsili or 0)}'


def _prepare(db, rows, warnings):
    groups = {int(x['Code']): x.get('CName') or '' for x in rows['tbl_GoodsGroup']}
    goods = {code(x['Code']): x for x in rows['tbl_Goods']}
    accounts = {(int(x['KolCode']), int(x['MoeenCode']), int(x['Code'])): x for x in rows['tbl_Tafsili']}
    phones = {(int(x['KolCode']), int(x['MoeenCode']), int(x['TafsiliCode'])): x for x in rows['tbl_Telephone']}
    kol = {int(x['Code']): x.get('CName') or '' for x in rows['tbl_Kol']}
    moeen = {(int(x['KolCode']), int(x['Code'])): x.get('CName') or '' for x in rows['tbl_Moeen']}
    return groups, goods, accounts, phones, kol, moeen


def import_parsian_archive(path, activate_current=True):
    """One transaction imports the archive and optionally opens the current catalog/stock."""
    migrate()
    manifest, all_rows, raw = source_archive(path)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    db = connect()
    try:
        if db.execute('SELECT id FROM legacy_imports WHERE source_sha256=?', (digest,)).fetchone():
            raise ValueError('این آرشیو قبلاً وارد شده است.')
        if db.execute('SELECT COUNT(*) FROM legacy_imports').fetchone()[0]:
            raise ValueError('برای ورود دوبارهٔ داده‌های پارسیان، نخست نسخهٔ جدید و روش همگام‌سازی را آماده کنید.')
        if activate_current and any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
                                    for table in ('products', 'parties', 'invoices', 'money_movements')):
            raise ValueError('فعال‌سازی داده‌های پارسیان فقط روی پایگاه دادهٔ خالی مجاز است.')
        warnings = Counter()
        record_count = sum(len(lines) for lines in raw.values())
        with transaction(db):
            cur = db.execute('''INSERT INTO legacy_imports(source_sha256,source_created_at,money_unit,record_count)
                VALUES (?,?,?,?)''', (digest, manifest['created_at'], 'rial', record_count))
            import_id = cur.lastrowid
            for source_db in SOURCE_DBS:
                for table in TABLES:
                    db.executemany('''INSERT INTO legacy_records(import_id,source_db,table_name,row_number,payload_json)
                        VALUES (?,?,?,?,?)''', ((import_id, source_db, table, n, line)
                                            for n, line in enumerate(raw[source_db, table], 1)))
                rows = all_rows[source_db]
                groups, goods, accounts, phones, kol, moeen = _prepare(source_db, rows, warnings)
                factors = defaultdict(list)
                stock = defaultdict(Decimal)
                latest_cost = {}
                latest_sale = {}
                for factor in rows['tbl_Factor']:
                    key = (int(factor['TitleCode']), int(factor['Factor_Num']))
                    factors[key].append(factor)
                    product_code = code(factor['GoodsCode'])
                    qty = number(factor['Tedad'])
                    io = int(factor['I/O'] or 0)
                    if io == 1:
                        stock[product_code] += qty
                    elif io == 2:
                        stock[product_code] -= qty
                    if key[0] in (2, 5) and number(factor['Price']) > 0:
                        old = latest_cost.get(product_code)
                        order = (factor.get('Factor_Date') or '', int(factor.get('ID') or 0))
                        if old is None or order > old[0]:
                            latest_cost[product_code] = (order, as_int(factor['Price'], 'cost', warnings))
                    if key[0] == 1 and number(factor['Price']) > 0:
                        old = latest_sale.get(product_code)
                        order = (factor.get('Factor_Date') or '', int(factor.get('ID') or 0))
                        if old is None or order > old[0]:
                            latest_sale[product_code] = (order, as_int(factor['Price'], 'sale_price', warnings))
                for product_code in goods:
                    amount = stock[product_code]
                    if amount != int(amount):
                        warnings['fractional_stock'] += 1
                    qty = int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                    if qty < 0:
                        warnings['negative_stock'] += 1
                    db.execute('INSERT INTO legacy_stock(import_id,source_db,product_code,quantity) VALUES (?,?,?,?)',
                               (import_id, source_db, product_code, qty))
                    sale_hint = latest_sale.get(product_code, (('', 0), 0))
                    purchase_hint = latest_cost.get(product_code, (('', 0), 0))
                    db.execute('''INSERT INTO legacy_price_suggestions(import_id,source_db,product_code,
                        last_sale_rial,last_sale_date,last_purchase_rial,last_purchase_date)
                        VALUES (?,?,?,?,?,?,?)''',
                        (import_id, source_db, product_code, sale_hint[1], sale_hint[0][0],
                         purchase_hint[1], purchase_hint[0][0]))
                headers = defaultdict(list)
                for header in rows['tbl_FactNum']:
                    headers[(int(header['TitleCode']), int(header['Fact_Num']))].append(header)
                for key in sorted(set(factors) | set(headers)):
                    lines = factors.get(key, [])
                    header_options = headers.get(key, [])
                    if len(header_options) > 1:
                        warnings['duplicate_invoice_headers'] += len(header_options) - 1
                    line_sum = sum((number(x['Tedad']) * number(x['Price']) for x in lines), Decimal(0))
                    matching = [x for x in header_options if number(x['Total']) == line_sum]
                    header = max(matching or header_options, key=lambda x: int(x['ID'])) if header_options else None
                    if header is None:
                        warnings['missing_invoice_headers'] += 1
                    if not lines:
                        warnings['invoice_headers_without_lines'] += 1
                    if header and lines and number(header['Total']) != line_sum:
                        warnings['invoice_total_mismatch'] += 1
                    first = lines[0] if lines else None
                    account = None
                    if header:
                        account = (int(header.get('Kol') or 0), int(header.get('Moeen') or 0), int(header.get('Tafsili') or 0))
                    elif first:
                        account = (int(first.get('Taraf_K') or 0), int(first.get('Taraf_M') or 0), int(first.get('Taraf_T') or 0))
                    account = account or (0, 0, 0)
                    party = accounts.get(account)
                    if party is None and account != (0, 0, 0):
                        warnings['unmatched_invoice_parties'] += 1
                    amount_rial = as_int(header['Total'] if header else line_sum, 'invoice_total', warnings)
                    cur = db.execute('''INSERT INTO legacy_invoices(import_id,source_db,title_code,factor_num,date_jalali,
                        party_key,party_name,total_rial,lines_count,note) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (import_id, source_db, key[0], key[1],
                         (first or header or {}).get('Factor_Date') or (header or {}).get('HDate') or '',
                         _account_key(*account), (party or {}).get('CName') or '', amount_rial, len(lines),
                         (first or {}).get('FactorComment') or ''))
                    invoice_id = cur.lastrowid
                    for factor in lines:
                        product_code = code(factor['GoodsCode'])
                        qty = number(factor['Tedad'])
                        if qty != int(qty):
                            warnings['fractional_invoice_quantity'] += 1
                        price = as_int(factor['Price'], 'invoice_line_price', warnings)
                        total = as_int(qty * number(factor['Price']), 'invoice_line_total', warnings)
                        db.execute('''INSERT INTO legacy_invoice_lines(legacy_invoice_id,source_row_id,product_code,
                            product_name,quantity,unit_price_rial,line_total_rial) VALUES (?,?,?,?,?,?,?)''',
                            (invoice_id, int(factor['ID']), product_code,
                             goods.get(product_code, {}).get('CName') or '', int(qty), price, total))
                for document in rows['tbl_Document']:
                    k, m, t = (int(document.get(name) or 0) for name in ('KolCode', 'MoeenCode', 'TafsiliCode'))
                    name = (accounts.get((k, m, t)) or {}).get('CName') or moeen.get((k, m)) or kol.get(k) or ''
                    db.execute('''INSERT INTO legacy_journal(import_id,source_db,source_row_id,document_num,
                        date_jalali,kol_code,moeen_code,tafsili_code,account_name,debit_rial,credit_rial,note)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (import_id, source_db, int(document['ID']), int(document.get('Sanad_Num') or 0),
                         document.get('SanadDate') or '', k, m, t, name,
                         as_int(document.get('Bed'), 'debit', warnings),
                         as_int(document.get('Bes'), 'credit', warnings),
                         document.get('Comment') or ''))
                for check in rows['tbl_Check']:
                    db.execute('''INSERT INTO legacy_checks(import_id,source_db,source_row_id,check_number,bank_name,
                        amount_rial,due_date_jalali,received_date_jalali,kind_code,status_code,is_active,
                        is_cancelled,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (import_id, source_db, int(check['ID']), str(check.get('Chk_Num') or ''),
                         check.get('Bank') or '', as_int(check.get('Money'), 'check_amount', warnings),
                         check.get('ExpDate') or '', check.get('RecDate') or '',
                         int(check.get('Chk_Kind') or 0), int(check.get('Vos_Pas') or 0),
                         int(bool(check.get('IsActive'))), int(bool(check.get('Lashe'))),
                         check.get('Comment') or ''))
                if activate_current and source_db == 'db_1405':
                    owner = db.execute("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
                    if not owner:
                        raise ValueError('برای فعال‌سازی، حساب مالک برنامه ابتدا باید ساخته شده باشد.')
                    owner_id = owner['id']
                    historical_tier = db.execute('''INSERT INTO price_types(code,label)
                        VALUES ('parsian_last_sale','آخرین فروش پارسیان (تاریخی)')''').lastrowid
                    for product_code, product in goods.items():
                        name = (product.get('CName') or '').strip() or f'کالای پارسیان {product_code}'
                        category = groups.get(int(product.get('GroupCode') or 0), '')[:80]
                        min_stock = max(0, int(product.get('OrderPoint') or 0))
                        cur = db.execute('''INSERT INTO products(sku,name,category,min_stock,is_active,search_name,
                            search_sku,cost_confirmed) VALUES (?,?,?,?,?,?,?,0)''',
                            (product_code, name, category, min_stock, 1,
                             normalize_name(' '.join((name, category))), normalize_code(product_code)))
                        qty = db.execute('''SELECT quantity FROM legacy_stock WHERE import_id=? AND source_db=?
                            AND product_code=?''', (import_id, source_db, product_code)).fetchone()['quantity']
                        cost_rial = latest_cost.get(product_code, (None, 0))[1]
                        estimated_cost = cost_rial
                        db.execute('''INSERT INTO stock_balances(branch_id,product_id,quantity,avg_cost_rial)
                            VALUES (1,?,?,?)''', (cur.lastrowid, qty, estimated_cost))
                        if qty:
                            db.execute('''INSERT INTO stock_movements(branch_id,product_id,delta_qty,reason,note,created_by)
                                VALUES (1,? ,?,'opening',?,?)''',
                                (cur.lastrowid, qty, 'موجودی منتقل‌شده از پارسیان ۱۴۰۵', owner_id))
                        sale_rial = latest_sale.get(product_code, (None, 0))[1]
                        if sale_rial > 0:
                            sale_rial = int(sale_rial)
                            if sale_rial > 0:
                                db.execute('''INSERT INTO product_prices(product_id,price_type_id,amount_rial,updated_by)
                                    VALUES (?,?,?,?)''', (cur.lastrowid, historical_tier, sale_rial, owner_id))
                    for account_key, party in accounts.items():
                        name = (party.get('CName') or '').strip()
                        if not name:
                            continue
                        phone = phones.get(account_key, {})
                        telephone = (phone.get('Mobile') or phone.get('Tel') or '')[:30]
                        cur = db.execute('''INSERT INTO parties(name,kind,phone,note) VALUES (?,'both',?,?)''',
                                         (name, telephone, f'شناسه پارسیان {source_db}: {_account_key(*account_key)}'))
                        db.execute('''INSERT INTO legacy_party_map(source_db,kol_code,moeen_code,tafsili_code,party_id)
                            VALUES (?,?,?,?,?)''', (source_db, *account_key, cur.lastrowid))
                    warnings['unconfirmed_imported_costs'] = len(goods)
                    warnings['legacy_latest_sale_prices_available'] = len(latest_sale)
            if activate_current:
                from .opening_balances import populate
                populate(db, import_id)
            db.execute('''UPDATE legacy_imports SET warning_count=?,warning_json=? WHERE id=?''',
                       (sum(value for key, value in warnings.items() if key != 'legacy_latest_sale_prices_available'),
                        json.dumps(dict(warnings), ensure_ascii=False), import_id))
        return {'import_id': import_id, 'record_count': record_count, 'warnings': dict(warnings)}
    finally:
        db.close()


def activate_existing_parsian_catalog():
    """Repair the initial 1405 import without changing shop-entered prices or stock."""
    migrate()
    db = connect()
    try:
        with transaction(db):
            source = db.execute('SELECT id FROM legacy_imports ORDER BY id DESC LIMIT 1').fetchone()
            if not source:
                raise ValueError('دادهٔ پارسیان در این پایگاه داده وجود ندارد.')
            owner = db.execute("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
            if not owner:
                raise ValueError('حساب مالک پیدا نشد.')
            source_products = db.execute("SELECT COUNT(*) FROM legacy_stock WHERE import_id=? AND source_db='db_1405'",
                                         (source['id'],)).fetchone()[0]
            mapped = db.execute('''SELECT COUNT(*) FROM products p JOIN legacy_stock s ON s.product_code=p.sku
                WHERE s.import_id=? AND s.source_db='db_1405' ''', (source['id'],)).fetchone()[0]
            if mapped != source_products:
                raise ValueError('همهٔ کالاهای پارسیان در کاتالوگ فعلی پیدا نشدند.')
            db.execute('''UPDATE products SET is_active=1 WHERE sku IN
                (SELECT product_code FROM legacy_stock WHERE import_id=? AND source_db='db_1405')''',
                (source['id'],))
            tier = db.execute("SELECT id FROM price_types WHERE code='parsian_last_sale'").fetchone()
            if not tier:
                tier_id = db.execute('''INSERT INTO price_types(code,label)
                    VALUES ('parsian_last_sale','آخرین فروش پارسیان (تاریخی)')''').lastrowid
            else:
                tier_id = tier['id']
            suggestions = db.execute('''SELECT p.id,s.last_sale_rial FROM products p
                JOIN legacy_price_suggestions s ON s.product_code=p.sku
                WHERE s.import_id=? AND s.source_db='db_1405' AND s.last_sale_rial>0''',
                (source['id'],)).fetchall()
            added = 0
            for row in suggestions:
                amount = int(row['last_sale_rial'])
                if amount <= 0:
                    continue
                cur = db.execute('''INSERT OR IGNORE INTO product_prices
                    (product_id,price_type_id,amount_rial,updated_by) VALUES (?,?,?,?)''',
                    (row['id'], tier_id, amount, owner['id']))
                added += cur.rowcount
            db.execute('''INSERT INTO audit_log(actor_user_id,action,entity_type,detail)
                VALUES (?,?,?,?)''', (owner['id'], 'parsian.catalog.activate', 'products',
                                     json.dumps({'products': mapped, 'historical_prices_added': added})))
        return {'products': mapped, 'historical_prices_added': added}
    finally:
        db.close()
