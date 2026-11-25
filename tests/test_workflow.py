"""End-to-end checks for money, stock, access, and portable backup."""
import os
import base64
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import re
import zipfile
from datetime import date

from store.app import create_app
from store.auth import password_hash
from store.backup import create_backup, restore_backup, scheduled_backup
from store.calendar import display_date, gregorian_to_jalali, parse_date
from store.db import connect, db_path, transaction
from store.legacy_import import activate_existing_parsian_catalog
from store.opening_balances import activate_existing as activate_opening_balances, remaining_rial, summary as opening_summary
from store.reports import _chart_data
from store.updates import release, version, version_key


class StoreWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        os.environ['STORE_DATA_DIR'] = str(Path(self.scratch.name) / 'shop-a')
        self.app = create_app()
        self.client = self.app.test_client()
        db = connect()
        with transaction(db):
            db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES (?,?,?,?)',
                       ('admin', 'مدیر', password_hash('A-strong-test-password!'), 'owner'))
        db.close()
        self.client.get('/login')
        with self.client.session_transaction() as browser_session:
            login_csrf = browser_session['login_csrf']
        response = self.client.post('/login', data={'username': 'admin', 'password': 'A-strong-test-password!',
                                                     'login_csrf': login_csrf})
        self.assertEqual(response.status_code, 302)
        db = connect()
        self.csrf = db.execute('SELECT csrf_token FROM sessions').fetchone()[0]
        db.close()

    def tearDown(self):
        self.scratch.cleanup()
        os.environ.pop('STORE_DATA_DIR', None)

    def post(self, path, data):
        return self.client.post(path, data={'csrf_token': self.csrf, **data}, follow_redirects=True)

    def test_purchase_sale_payment_and_transfer(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        font = self.client.get('/static/fonts/Vazir-Regular.woff2')
        self.assertEqual(font.status_code, 200)
        font.close()
        self.assertEqual(self.post('/products', {'sku': 'LCD-100', 'name': 'نمایشگر آزمایشی',
                                                'brand': 'نمونه', 'min_stock': '2'}).status_code, 200)
        self.assertIn('LCD-100'.encode(), self.client.get('/products?q=LCD%DB%B1%DB%B0%DB%B0').data)
        self.assertIn('LCD-100'.encode(), self.client.get('/products?q=%D9%86%D9%85%D8%A7%D9%8A%D8%B4%DA%AF%D8%B1').data)
        self.assertEqual(self.post('/products/1/prices', {'price_type_id': '1', 'amount_rial': '60000'}).status_code, 200)
        self.assertEqual(self.post('/products/1/prices', {'price_type_id': '2', 'amount_rial': '55000'}).status_code, 200)
        self.assertEqual(self.post('/parties', {'name': 'طرف حساب نمونه', 'kind': 'both'}).status_code, 200)

        purchase = self.post('/invoices/new/purchase', {
            'party_id': '1', 'product_id': '1', 'quantity': '5', 'unit_cost_rial': '40000',
        })
        self.assertIn('K-000001'.encode(), purchase.data)
        db = connect()
        self.assertEqual(tuple(db.execute('SELECT quantity,avg_cost_rial FROM stock_balances').fetchone()), (5, 40000))
        db.close()

        sale = self.post('/invoices/new/sale', {
            'party_id': '1', 'product_id': '1', 'quantity': '2', 'price_type_id': '1',
        })
        self.assertIn('F-000002'.encode(), sale.data)
        db = connect()
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 3)
        self.assertEqual(db.execute("SELECT unit_cost_rial FROM invoice_lines WHERE invoice_id=2").fetchone()[0], 40000)
        db.close()
        self.post('/invoices/2/payments', {'amount_rial': '50000', 'account_id': '1'})
        self.assertEqual(self.client.get('/accounts').status_code, 200)
        self.assertEqual(self.client.get('/products').status_code, 200)
        self.assertEqual(self.client.get('/parties/1').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/prices').json['items'][0]['sku'], 'LCD-100')

        failed = self.post('/invoices/new/sale', {
            'party_id': '1', 'product_id': '1', 'quantity': '9', 'price_type_id': '1',
        })
        self.assertIn('موجودی'.encode(), failed.data)
        db = connect()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM invoices').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 3)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM price_changes').fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT SUM(total_rial) FROM invoices WHERE kind='sale'").fetchone()[0], 120000)
        db.close()

        archive = Path(self.scratch.name) / 'transfer.zip'
        create_backup(archive)
        self.assertTrue(archive.exists())
        web_archive = self.post('/backups/download', {})
        self.assertEqual(web_archive.status_code, 200)
        self.assertGreater(len(web_archive.data), 1000)
        web_archive.close()
        os.environ['STORE_DATA_DIR'] = str(Path(self.scratch.name) / 'shop-b')
        restore_backup(archive)
        self.assertTrue(db_path().exists())
        moved = connect()
        self.assertEqual(moved.execute('SELECT COUNT(*) FROM invoices').fetchone()[0], 2)
        self.assertEqual(moved.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
        self.assertEqual(moved.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        moved.close()

    def test_rial_accounts_report_dates_and_settings_tabs(self):
        self.post('/products', {'sku': 'P-1', 'name': 'قطعه', 'min_stock': '0'})
        self.post('/products/1/prices', {'price_type_id': '1', 'amount_rial': '12,345'})
        self.post('/parties', {'name': 'آزمایشی', 'kind': 'both'})
        self.post('/settings/accounts', {'name': 'بانک دوم', 'kind': 'bank'})
        self.post('/invoices/new/purchase', {'party_id': '1', 'product_id': '1',
            'quantity': '2', 'unit_cost_rial': '5,000', 'paid_amount_rial': '10,000', 'account_id': '3'})
        self.post('/invoices/new/sale', {'party_id': '1', 'product_id': '1',
            'quantity': '1', 'price_type_id': '1', 'paid_amount_rial': '12,345', 'account_id': '3'})
        db = connect()
        self.assertEqual(db.execute('SELECT amount_rial FROM product_prices').fetchone()[0], 12345)
        self.assertEqual(db.execute('SELECT SUM(amount_rial) FROM money_movements WHERE account_id=3').fetchone()[0], 22345)
        db.close()
        self.assertIn('2,345'.encode(), self.client.get('/invoices/2').data)
        self.assertIn('12,345'.encode(), self.client.get('/profit-loss').data)
        self.assertIn('>0<'.encode(), self.client.get('/profit-loss?from=2000-01-01&to=2000-12-31').data)
        payments = self.client.get('/settings?tab=payments').data
        self.assertIn('بانک دوم'.encode(), payments)
        self.assertNotIn('action="/settings/transfer"'.encode(), payments)
        self.assertIn('به‌روزرسانی'.encode(), self.client.get('/settings?tab=updates').data)

    def test_github_release_requires_matching_asset_and_digest(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def read(self, _limit):
                return json.dumps({'tag_name': 'v1.2.0', 'body': 'تغییرات نسخه',
                    'assets': [{'name': 'QatYar-Windows.zip', 'size': 1000,
                        'digest': 'sha256:' + 'a' * 64,
                        'url': 'https://api.github.com/repos/acme/qatyar/releases/assets/123'}]}).encode()
        with patch('store.updates.urllib.request.urlopen', return_value=Response()):
            result = release('acme/qatyar')
        self.assertEqual(result['tag'], 'v1.2.0')
        self.assertEqual(version_key(result['tag']), (1, 2, 0))
        self.assertEqual(result['digest'], 'sha256:' + 'a' * 64)
        with self.assertRaises(ValueError):
            release('acme/qatyar/other')

    def test_accounting_transfer_continues_inventory_and_calendar(self):
        manifest = {'format': 'qatyar-accounting-transfer', 'format_version': 1,
                    'money_unit': 'toman', 'calendar': 'jalali', 'fiscal_year': '1405',
                    'cash_opening': '1000', 'bank_opening': '2000'}
        files = {
            'products.csv': 'sku,name,brand,category,quantity,avg_cost,sale_price,cost_confirmed\nP-1,نمایشگر,,,5,100,150,1\n',
            'parties.csv': 'code,name,kind,phone,opening_balance\nC-1,مشتری,both,,200\n',
            'invoices.csv': 'document_id,kind,number,date,party_code,total,note\nD-1,sale,OLD-1,1405/09/04,C-1,300,\n',
            'invoice_lines.csv': 'document_id,sku,quantity,unit_price,line_total\nD-1,P-1,2,150,300\n',
            'checks.csv': 'number,direction,party_code,bank_name,amount,due_date,status,note\nCH-1,received,C-1,,500,1405/09/15,pending,\n',
        }
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            for name, content in files.items():
                archive.writestr(name, content)
        package.seek(0)
        response = self.post('/settings/transfer', {'archive': (package, 'transfer.zip')})
        self.assertIn('انتقال کامل شد'.encode(), response.data)
        db = connect()
        self.assertEqual(db.execute('SELECT quantity,avg_cost_rial FROM stock_balances').fetchone()[:], (5, 1000))
        self.assertEqual(db.execute('SELECT amount_rial FROM product_prices').fetchone()[0], 1500)
        self.assertEqual(db.execute('SELECT amount_rial FROM opening_party_balances').fetchone()[0], 2000)
        self.assertEqual(db.execute('SELECT total_rial FROM legacy_invoices').fetchone()[0], 3000)
        self.assertEqual(db.execute('SELECT status FROM payment_checks').fetchone()[0], 'pending')
        self.assertEqual(_chart_data(db, date(2026, 11, 25), date(2026, 11, 25), 'jalali')['totals']['sales'], 3000)
        db.close()
        accounts = self.client.get('/accounts').get_data(as_text=True)
        self.assertIn('<td>10,000</td>', accounts)
        self.assertIn('<td>20,000</td>', accounts)
        self.assertEqual(self.client.get('/legacy').status_code, 200)
        self.assertIn('OLD-1'.encode(), self.client.get('/invoices').data)
        self.assertNotIn('پارسیان'.encode(), self.client.get('/invoices').data)
        self.assertIn('OLD-1'.encode(), self.client.get('/?from=1405%2F09%2F01&to=1405%2F09%2F30').data)
        self.post('/settings/calendar', {'calendar': 'gregorian'})
        self.assertIn('2026-11-25'.encode(), self.client.get('/invoices').data)
        db = connect()
        self.assertEqual(_chart_data(db, date(2026, 11, 25), date(2026, 11, 25), 'gregorian')['totals']['sales'], 3000)
        db.close()
        self.assertIn('3,000'.encode(), self.client.get('/?from=2026-11-25&to=2026-11-25').data)
        self.post('/checks', {'direction': 'received', 'party_id': '1', 'check_number': 'CH-2',
                              'amount_rial': '100', 'due_date_jalali': '2026-12-06'})
        db = connect()
        self.assertEqual(db.execute("SELECT due_date_jalali FROM payment_checks WHERE check_number='CH-2'").fetchone()[0], '1405/09/15')
        db.close()
        sale = self.post('/invoices/new/sale', {'party_id': '1', 'product_id': '1',
                                                'quantity': '1', 'price_type_id': '1'})
        self.assertIn('F-000001'.encode(), sale.data)
        db = connect()
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 4)
        db.close()

    def test_calendar_round_trip_and_invalid_leap_day(self):
        self.assertEqual(gregorian_to_jalali(date(2026, 11, 25)), (1405, 9, 4))
        self.assertEqual(parse_date('۱۴۰۵/۰۹/۰۴', 'jalali'), date(2026, 11, 25))
        self.assertEqual(display_date('2026-11-25 00:00:00', 'jalali'), '1405/09/04 03:30')
        self.assertEqual(display_date('1405/09/04', 'gregorian'), '2026-11-25')
        with self.assertRaises(ValueError):
            parse_date('1404/12/30', 'jalali')

    def test_transfer_rejects_unbalanced_document_without_partial_data(self):
        manifest = {'format': 'qatyar-accounting-transfer', 'format_version': 1,
                    'money_unit': 'rial', 'calendar': 'jalali', 'fiscal_year': '1405'}
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('products.csv', 'sku,name,brand,category,quantity,avg_cost,sale_price,cost_confirmed\nP-1,قطعه,,,2,100,200,1\n')
            archive.writestr('parties.csv', 'code,name,kind,phone,opening_balance\nC-1,مشتری,both,,0\n')
            archive.writestr('journal.csv', 'document_number,date,kol_code,moeen_code,tafsili_code,account_name,debit,credit,note\n1,1405/09/04,10,0,0,صندوق,100,0,\n')
        package.seek(0)
        response = self.post('/settings/transfer', {'archive': (package, 'bad.zip')})
        self.assertIn('جمع بدهکار و بستانکار'.encode(), response.data)
        db = connect()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM products').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM legacy_imports').fetchone()[0], 0)
        db.close()

    def test_numbered_pages_and_return_forms(self):
        db = connect()
        with transaction(db):
            db.execute("INSERT INTO parties(id,name,kind) VALUES (1,'مشتری و تأمین‌کننده','both')")
            for number in range(1, 24):
                db.execute('''INSERT INTO products(id,sku,name,search_sku,search_name)
                    VALUES (?,?,?,?,?)''', (number, f'PART-{number:02}', f'قطعه {number:02}',
                                          f'PART-{number:02}', f'قطعه {number:02}'))
                db.execute('INSERT INTO stock_balances(branch_id,product_id) VALUES (1,?)', (number,))
                db.execute('''INSERT INTO product_prices(product_id,price_type_id,amount_rial,updated_by)
                    VALUES (?,1,200,1)''', (number,))
        db.close()
        def body_rows(path):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            first_body = re.search(rb'<tbody>(.*?)</tbody>', response.data, re.S).group(1)
            return first_body.count(b'<tr>'), response.data
        count, page1 = body_rows('/products')
        self.assertEqual(count, 10)
        self.assertIn(b'page=2', page1)
        count, page2 = body_rows('/products?page=2')
        self.assertEqual(count, 10)
        count, page3 = body_rows('/products?page=3')
        self.assertEqual(count, 3)
        self.assertIn(b'page=3', page3)
        self.assertEqual(body_rows('/products?page=999')[0], 3)
        self.assertEqual(body_rows('/products?page=invalid')[0], 10)
        searched_count, searched = body_rows('/products?q=PART&page=2')
        self.assertEqual(searched_count, 10)
        self.assertIn(b'q=PART', searched)
        self.assertEqual(len(self.client.get('/api/v1/prices').json['items']), 10)
        self.assertEqual(self.client.get('/api/v1/prices?page=3').json['page'], 3)

        ids = [str(i) for i in range(1, 13)]
        self.post('/invoices/new/purchase', {'party_id': '1', 'product_id': ids,
            'quantity': ['2'] * 12, 'unit_cost_rial': ['100'] * 12})
        self.post('/invoices/new/sale', {'party_id': '1', 'product_id': ids,
            'quantity': ['1'] * 12, 'price_type_id': ['1'] * 12})
        self.assertEqual(body_rows('/invoices')[0], 2)
        self.assertIn(b'F-000002', self.client.get('/returns/new').data)
        self.assertIn(b'K-000001', self.client.get('/purchase-returns/new').data)
        self.assertEqual(body_rows('/returns/new/2?page=2')[0], 2)
        returned = self.post('/returns/new/2?page=2', {'line_id': ['23', '24'],
            'quantity': ['1', '0'], 'reason': 'تست صفحه دوم'})
        self.assertIn(b'RET0000001', returned.data)
        self.assertEqual(body_rows('/purchase-returns/new/1?page=2')[0], 2)
        purchased = self.post('/purchase-returns/new/1?page=2', {'line_id': ['11', '12'],
            'quantity': ['1', '0'], 'reason': 'تست صفحه دوم'})
        self.assertIn(b'PRT0000001', purchased.data)
        db = connect()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM sales_return_lines').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM purchase_return_lines').fetchone()[0], 1)
        db.close()

    def test_all_list_pages_render(self):
        routes = ('/', '/products', '/invoices', '/returns', '/purchase-returns',
                  '/parties', '/checks', '/accounts', '/users', '/legacy',
                  '/legacy/invoices', '/legacy/stock', '/legacy/checks',
                  '/legacy/accounts', '/settings?tab=sales', '/settings?tab=payments',
                  '/settings?tab=backup', '/settings?tab=security', '/settings?tab=updates')
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_update_settings_show_current_or_new_version(self):
        db = connect()
        self.assertEqual(db.execute('SELECT github_repo FROM update_settings WHERE id=1').fetchone()[0],
                         'shahabmohamadi/QatYar')
        db.close()
        result = {'tag': 'v' + version(), 'notes': 'اصلاحات',
                  'url': 'https://api.github.com/repos/shahabmohamadi/QatYar/releases/assets/1',
                  'digest': 'sha256:' + 'a' * 64}
        with patch('store.updates.release', return_value=result):
            current = self.post('/settings/updates/check', {})
        self.assertIn('نسخه فعلی به‌روز است.'.encode(), current.data)
        with patch('store.updates.release', return_value={**result, 'tag': 'v9.0.0'}):
            newer = self.post('/settings/updates/check', {})
        self.assertIn('اعمال این نسخه'.encode(), newer.data)

    def test_csrf_and_permissions(self):
        self.assertEqual(self.client.post('/products', data={'sku': 'X', 'name': 'bad'}).status_code, 400)
        self.post('/users', {'username': 'seller', 'display_name': 'فروشنده', 'role': 'clerk',
                             'password': 'Another-strong-password!'})
        self.post('/logout', {})
        self.client.get('/login')
        with self.client.session_transaction() as browser_session:
            login_csrf = browser_session['login_csrf']
        self.client.post('/login', data={'username': 'seller', 'password': 'Another-strong-password!',
                                         'login_csrf': login_csrf})
        self.assertEqual(self.client.get('/users').status_code, 403)
        self.assertEqual(self.client.get('/accounts').status_code, 403)
        self.assertEqual(self.client.get('/settings').status_code, 403)

    def test_sale_return_check_settlement_and_profit(self):
        self.post('/products', {'sku': 'PART-1', 'name': 'قطعه', 'min_stock': '0'})
        self.post('/products/1/prices', {'price_type_id': '1', 'amount_rial': '200'})
        self.post('/parties', {'name': 'مشتری و تأمین‌کننده', 'kind': 'both'})
        self.post('/invoices/new/purchase', {'party_id': '1', 'product_id': '1',
                                             'quantity': '5', 'unit_cost_rial': '100'})
        self.post('/invoices/new/sale', {'party_id': '1', 'product_id': '1',
                                         'quantity': '3', 'price_type_id': '1'})
        self.post('/invoices/2/payments', {'amount_rial': '600', 'method': 'cash'})
        returned = self.post('/returns/new/2', {'quantity': '1', 'reason': 'خرابی کالا'})
        self.assertIn('RET0000001'.encode(), returned.data)
        db = connect()
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 3)
        self.assertEqual(db.execute('SELECT total_rial FROM sales_returns').fetchone()[0], 200)
        db.close()
        self.assertIn('200'.encode(), self.client.get('/returns/1').data)
        self.post('/returns/1/refunds', {'amount_rial': '200', 'method': 'cash'})
        self.assertIn('0'.encode(), self.client.get('/invoices/2').data)
        self.assertIn('200'.encode(), self.client.get('/profit-loss').data)
        self.post('/invoices/new/sale', {'party_id': '1', 'product_id': '1',
                                         'quantity': '1', 'price_type_id': '1'})
        self.post('/checks', {'direction': 'received', 'party_id': '1', 'invoice_id': '3',
                              'check_number': '12345', 'bank_name': 'بانک', 'amount_rial': '200',
                              'due_date_jalali': '۱۴۰۵/۰۹/۱۵'})
        db = connect()
        self.assertEqual(db.execute('SELECT due_date_jalali FROM payment_checks').fetchone()[0], '1405/09/15')
        self.assertEqual(db.execute('SELECT status FROM payment_checks').fetchone()[0], 'pending')
        db.close()
        self.post('/checks/1/status', {'status': 'cleared'})
        db = connect()
        self.assertEqual(db.execute('SELECT status FROM payment_checks').fetchone()[0], 'cleared')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM payment_check_events').fetchone()[0], 1)
        db.close()
        self.assertEqual(self.client.get('/checks').status_code, 200)
        self.assertEqual(self.client.get('/accounts').status_code, 200)
        self.assertEqual(self.client.get('/parties/1').status_code, 200)

    def test_purchase_return_reduces_stock_and_supplier_balance(self):
        self.post('/products', {'sku': 'PART-2', 'name': 'قطعه خریداری‌شده', 'min_stock': '0'})
        self.post('/parties', {'name': 'تأمین‌کننده', 'kind': 'supplier'})
        self.post('/invoices/new/purchase', {'party_id': '1', 'product_id': '1',
                                             'quantity': '5', 'unit_cost_rial': '100'})
        self.post('/invoices/1/payments', {'amount_rial': '500', 'account_id': '2'})
        returned = self.post('/purchase-returns/new/1', {'quantity': '2', 'reason': 'کالای معیوب'})
        self.assertIn('PRT0000001'.encode(), returned.data)
        db = connect()
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 3)
        self.assertEqual(db.execute('SELECT total_rial FROM purchase_returns').fetchone()[0], 200)
        db.close()
        self.assertEqual(self.client.get('/purchase-returns').status_code, 200)
        self.assertEqual(self.client.get('/accounts').status_code, 200)
        self.assertEqual(self.client.get('/parties/1').status_code, 200)
        self.post('/purchase-returns/1/refunds', {'amount_rial': '200', 'method': 'bank'})
        db = connect()
        self.assertEqual(db.execute('SELECT SUM(amount_rial) FROM purchase_return_refunds').fetchone()[0], 200)
        db.close()
        failed = self.post('/purchase-returns/new/1', {'quantity': '4', 'reason': 'زیادتر از باقیمانده'})
        self.assertIn('تعداد برگشتی'.encode(), failed.data)
        db = connect()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM purchase_returns').fetchone()[0], 1)
        db.close()

    def test_legacy_trading_summary_does_not_claim_net_profit(self):
        db = connect()
        with transaction(db):
            db.execute("INSERT INTO legacy_imports(source_sha256,source_created_at,money_unit,record_count) VALUES (?,?,?,?)",
                       ('test-digest', '2026-11-23T12:43:11+03:30', 'rial', 0))
            for index, (account, debit, credit) in enumerate(((60, 0, 10000), (61, 1000, 0),
                                                               (62, 5000, 0), (63, 0, 500),
                                                               (50, 200, 0)), 1):
                db.execute('''INSERT INTO legacy_journal(import_id,source_db,source_row_id,document_num,
                    date_jalali,kol_code,moeen_code,tafsili_code,account_name,debit_rial,credit_rial,note)
                    VALUES (1,'db_1405',?,?, '1405/01/01',?,0,0,'',?,?,'')''',
                           (index, index, account, debit, credit))
        db.close()
        page = self.client.get('/legacy/accounts?year=db_1405').get_data(as_text=True)
        self.assertIn('خرید خالص', page)
        self.assertIn('اختلاف معاملات', page)
        self.assertIn('4,300', page)
        self.assertIn('این عدد سود خالص', page)

    def test_imported_stock_is_sellable_with_explicit_price_and_provisional_cost(self):
        self.post('/products', {'sku': 'P-1', 'name': 'قطعه واردشده', 'min_stock': '0'})
        self.post('/parties', {'name': 'مشتری', 'kind': 'customer'})
        db = connect()
        with transaction(db):
            db.execute('UPDATE stock_balances SET quantity=3,avg_cost_rial=100 WHERE product_id=1')
            db.execute('UPDATE products SET cost_confirmed=0 WHERE id=1')
        db.close()
        response = self.post('/invoices/new/sale', {'party_id': '1', 'product_id': '1',
                             'quantity': '1', 'price_type_id': '', 'unit_price_rial': '250'})
        self.assertIn('F-000001'.encode(), response.data)
        db = connect()
        self.assertEqual(tuple(db.execute('SELECT unit_price_rial,unit_cost_rial,cost_provisional FROM invoice_lines').fetchone()),
                         (250, 100, 1))
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 2)
        db.close()
        self.assertIn('قطعی نیست'.encode(), self.client.get('/profit-loss').data)

    def test_purchase_replenishes_negative_imported_stock_to_zero(self):
        self.post('/products', {'sku': 'P-2', 'name': 'قطعه با کسری', 'min_stock': '0'})
        self.post('/parties', {'name': 'تأمین‌کننده', 'kind': 'supplier'})
        db = connect()
        with transaction(db):
            db.execute('UPDATE stock_balances SET quantity=-2,avg_cost_rial=80 WHERE product_id=1')
        db.close()
        response = self.post('/invoices/new/purchase', {'party_id': '1', 'product_id': '1',
                             'quantity': '2', 'unit_cost_rial': '120'})
        self.assertIn('K-000001'.encode(), response.data)
        db = connect()
        self.assertEqual(tuple(db.execute('SELECT quantity,avg_cost_rial FROM stock_balances').fetchone()), (0, 120))
        db.close()

    def test_activate_imported_catalog_preserves_stock_and_adds_historical_tier(self):
        self.post('/products', {'sku': '101', 'name': 'قطعه پارسیان', 'min_stock': '0'})
        db = connect()
        with transaction(db):
            db.execute("INSERT INTO legacy_imports(source_sha256,source_created_at,money_unit,record_count) VALUES ('a','2026-11-23','rial',0)")
            db.execute("INSERT INTO legacy_stock(import_id,source_db,product_code,quantity) VALUES (1,'db_1405','101',7)")
            db.execute("INSERT INTO legacy_price_suggestions(import_id,source_db,product_code,last_sale_rial) VALUES (1,'db_1405','101',1230)")
            db.execute('UPDATE products SET is_active=0 WHERE id=1')
            db.execute('UPDATE stock_balances SET quantity=7 WHERE product_id=1')
        db.close()
        first = activate_existing_parsian_catalog()
        second = activate_existing_parsian_catalog()
        self.assertEqual((first['products'], first['historical_prices_added']), (1, 1))
        self.assertEqual(second['historical_prices_added'], 0)
        db = connect()
        self.assertEqual(tuple(db.execute('SELECT is_active FROM products').fetchone()), (1,))
        self.assertEqual(db.execute('SELECT quantity FROM stock_balances').fetchone()[0], 7)
        self.assertEqual(db.execute('SELECT amount_rial FROM product_prices').fetchone()[0], 1230)
        db.close()

    def test_exact_parsian_opening_balance_and_settlement(self):
        self.post('/parties', {'name': 'مشتری پارسیان', 'kind': 'both'})
        db = connect()
        with transaction(db):
            db.execute("INSERT INTO legacy_imports(source_sha256,source_created_at,money_unit,record_count) VALUES ('b','2026-11-23','rial',0)")
            db.execute("INSERT INTO legacy_party_map(source_db,kol_code,moeen_code,tafsili_code,party_id) VALUES ('db_1405',12,2,1,1)")
            for index, (account, subaccount, detail, debit) in enumerate(((12, 2, 1, 123), (10, 0, 0, 1000),
                                                                          (11, 0, 0, 2000)), 1):
                db.execute('''INSERT INTO legacy_journal(import_id,source_db,source_row_id,document_num,
                    date_jalali,kol_code,moeen_code,tafsili_code,account_name,debit_rial,credit_rial,note)
                    VALUES (1,'db_1405',?,1,'1405/01/01',?,?,?,'',?,0,'')''',
                           (index, account, subaccount, detail, debit))
        db.close()
        self.assertEqual(activate_opening_balances(), {'parties': 1, 'net_party_rial': 123})
        self.assertIn('123'.encode(), self.client.get('/parties/1').data)
        response = self.post('/parties/1/opening-settlements', {'amount_rial': '123', 'method': 'cash'})
        self.assertIn('تسویهٔ ماندهٔ ابتدای دوره'.encode(), response.data)
        db = connect()
        self.assertEqual(remaining_rial(db, 1), 0)
        self.assertEqual(opening_summary(db)['cash_rial'], 1123)
        db.close()
        self.assertEqual(self.client.get('/accounts').status_code, 200)

    def test_settings_control_brand_operations_and_backup(self):
        self.assertEqual(self.client.get('/settings').status_code, 200)
        self.post('/settings/identity', {
            'app_name': 'فروش نو', 'app_tagline': 'مدیریت دقیق فروش',
            'shop_name': 'قطعات نمونه', 'shop_phone': '02112345678', 'shop_address': 'خیابان نمونه',
        })
        self.assertIn('فروش نو'.encode(), self.client.get('/settings').data)
        with self.app.test_client() as anonymous:
            self.assertIn('فروش نو'.encode(), anonymous.get('/login').data)
        self.assertIn('قطعات نمونه'.encode(), self.client.get('/invoices').data)
        self.post('/settings/inventory', {'branch_name': 'شعبه مرکزی', 'default_min_stock': '4'})
        self.post('/products', {'sku': 'LCD-200', 'name': 'نمایشگر نمونه', 'brand': 'نمونه'})
        db = connect()
        self.assertEqual(db.execute("SELECT min_stock FROM products WHERE sku='LCD-200'").fetchone()[0], 4)
        self.assertEqual(db.execute('SELECT name FROM branches WHERE id=1').fetchone()[0], 'شعبه مرکزی')
        db.close()

        self.post('/settings/sales', {'default_price_type_id': '2', 'sale_prefix': 'SALE-',
                                      'purchase_prefix': 'BUY-', 'invoice_digits': '4',
                                      'default_payment_method': 'bank'})
        self.post('/settings/price-types/2/toggle', {})
        db = connect()
        self.assertEqual(db.execute('SELECT is_active FROM price_types WHERE id=2').fetchone()[0], 1)
        db.close()
        self.post('/settings/payments', {'cash_label': 'صندوق', 'bank_label': 'کارت‌خوان'})
        form = self.client.get('/invoices/new/sale').get_data(as_text=True)
        self.assertIn('value="2" selected', form)
        self.assertIn('کارت‌خوان', self.client.get('/accounts').get_data(as_text=True))
        self.post('/products/1/prices', {'price_type_id': '2', 'amount_rial': '70000'})
        self.post('/parties', {'name': 'تأمین‌کننده', 'kind': 'supplier'})
        self.post('/parties', {'name': 'مشتری', 'kind': 'customer'})
        self.post('/invoices/new/purchase', {'party_id': '1', 'product_id': '1',
                                             'quantity': '3', 'unit_cost_rial': '40000'})
        self.post('/invoices/new/sale', {'party_id': '2', 'product_id': '1',
                                         'quantity': '1', 'price_type_id': '2'})
        db = connect()
        numbers = [row[0] for row in db.execute('SELECT number FROM invoices ORDER BY id')]
        self.assertEqual(numbers, ['BUY-0001', 'SALE-0002'])
        db.close()

        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/F9sAAAAASUVORK5CYII=')
        uploaded = self.post('/settings/logo', {'logo': (io.BytesIO(png), 'shop.png')})
        self.assertIn('لوگوی جدید'.encode(), uploaded.data)
        self.assertEqual(self.client.get('/brand-logo').mimetype, 'image/png')

        backup_dir = Path(self.scratch.name) / 'daily'
        self.post('/settings/backup', {'backup_enabled': 'off', 'backup_retention_days': '2',
                                       'backup_directory': str(backup_dir)})
        self.assertIsNone(scheduled_backup())
        self.post('/settings/backup', {'backup_enabled': 'on', 'backup_retention_days': '2',
                                       'backup_directory': str(backup_dir)})
        saved = scheduled_backup()
        self.assertTrue(saved.exists())
        db = connect()
        self.assertEqual(db.execute("SELECT outcome FROM backup_runs ORDER BY id DESC LIMIT 1").fetchone()[0], 'success')
        db.close()
        os.environ['STORE_DATA_DIR'] = str(Path(self.scratch.name) / 'moved-shop')
        restore_backup(saved)
        db = connect()
        row = db.execute('SELECT app_name,shop_name,logo_mime,sale_prefix FROM shop_settings WHERE id=1').fetchone()
        self.assertEqual(tuple(row), ('فروش نو', 'قطعات نمونه', 'image/png', 'SALE-'))
        db.close()


if __name__ == '__main__':
    unittest.main()
