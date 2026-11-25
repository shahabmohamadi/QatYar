"""Atomic purchase/sale posting, stock changes, and invoice settlements."""
import sqlite3
import uuid

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import require_login
from .db import get_db, transaction
from .pagination import paginate_query
from .settings import current_settings
from .utils import audit, integer

invoices = Blueprint('invoices', __name__)


def invoice_form_data(db, kind):
    party_kinds = ('customer', 'both') if kind == 'sale' else ('supplier', 'both')
    parties = db.execute('SELECT id,name,kind FROM parties WHERE is_active=1 AND kind IN (?,?) ORDER BY name',
                         party_kinds).fetchall()
    products = db.execute('SELECT id,sku,name FROM products WHERE is_active=1 ORDER BY name').fetchall()
    tiers = db.execute('SELECT id,label FROM price_types WHERE is_active=1 ORDER BY id').fetchall()
    prices = db.execute('SELECT product_id,price_type_id,amount_rial FROM product_prices WHERE is_active=1').fetchall()
    accounts = db.execute('SELECT id,kind,name FROM financial_accounts WHERE is_active=1 ORDER BY kind,id').fetchall()
    return parties, products, tiers, prices, accounts


def parsed_lines(kind):
    product_ids = request.form.getlist('product_id')
    quantities = request.form.getlist('quantity')
    tier_ids = request.form.getlist('price_type_id')
    manual_prices = request.form.getlist('unit_price_rial')
    costs = request.form.getlist('unit_cost_rial')
    if not product_ids or len(product_ids) > 100 or len(product_ids) != len(quantities):
        raise ValueError('فاکتور باید بین ۱ تا ۱۰۰ ردیف معتبر داشته باشد.')
    if kind == 'sale' and len(tier_ids) != len(product_ids):
        raise ValueError('نوع قیمت هر ردیف فروش باید مشخص باشد.')
    if kind == 'sale' and manual_prices and len(manual_prices) != len(product_ids):
        raise ValueError('قیمت دستی ردیف‌های فروش معتبر نیست.')
    if kind == 'purchase' and len(costs) != len(product_ids):
        raise ValueError('قیمت خرید هر ردیف باید مشخص باشد.')
    result = []
    for i, raw in enumerate(product_ids):
        product_id = integer(raw, 'کالا', minimum=1)
        quantity = integer(quantities[i], 'تعداد', minimum=1, maximum=10**8)
        if kind == 'sale':
            manual = integer(manual_prices[i], 'قیمت فروش دستی', minimum=1) if manual_prices and manual_prices[i].strip() else None
            detail = integer(tier_ids[i], 'نوع قیمت', minimum=1) if manual is None else None
        else:
            manual = None
            detail = integer(costs[i], 'قیمت خرید')
        result.append((product_id, quantity, detail, manual))
    return result


def post_invoice(kind, party_id, lines, note, user_id, paid_amount=0, account_id=None):
    db = get_db()
    with transaction(db):
        party = db.execute('SELECT id,kind FROM parties WHERE id=? AND is_active=1', (party_id,)).fetchone()
        accepted = ('customer', 'both') if kind == 'sale' else ('supplier', 'both')
        if not party or party['kind'] not in accepted:
            raise ValueError('طرف حساب با نوع فاکتور سازگار نیست.')

        prepared = []
        total = 0
        actor = db.execute('SELECT role FROM users WHERE id=?', (user_id,)).fetchone()
        for product_id, quantity, detail, manual_price in lines:
            product = db.execute('SELECT id,cost_confirmed FROM products WHERE id=? AND is_active=1', (product_id,)).fetchone()
            if not product:
                raise ValueError('یکی از کالاها وجود ندارد.')
            stock = db.execute('SELECT quantity,avg_cost_rial FROM stock_balances WHERE branch_id=1 AND product_id=?',
                               (product_id,)).fetchone()
            if not stock:
                raise ValueError('موجودی کالا تعریف نشده است.')
            if kind == 'sale':
                if manual_price is not None:
                    if not actor or actor['role'] not in ('owner', 'manager'):
                        raise ValueError('قیمت دستی فقط برای مدیر مجاز است.')
                    unit_price = manual_price
                    tier_id = None
                else:
                    price = db.execute('''SELECT amount_rial FROM product_prices pp
                        JOIN price_types t ON t.id=pp.price_type_id AND t.is_active=1
                        WHERE pp.product_id=? AND pp.price_type_id=? AND pp.is_active=1''',
                        (product_id, detail)).fetchone()
                    if not price:
                        raise ValueError('برای یکی از کالاها قیمت انتخابی ثبت نشده است.')
                    unit_price = price['amount_rial']
                    tier_id = detail
                unit_cost = stock['avg_cost_rial']
                cost_provisional = int(not product['cost_confirmed'])
            else:
                unit_price = detail
                unit_cost = detail
                tier_id = None
                cost_provisional = 0
            if unit_price <= 0:
                raise ValueError('قیمت هر ردیف باید بیشتر از صفر باشد.')
            line_total = quantity * unit_price
            if line_total > 10**14:
                raise ValueError('مبلغ ردیف بیش از حد مجاز است.')
            total += line_total
            prepared.append((product_id, quantity, unit_price, unit_cost, tier_id, line_total, cost_provisional))
        if total <= 0 or total > 10**15:
            raise ValueError('مبلغ فاکتور نامعتبر است.')
        if paid_amount:
            account = db.execute('SELECT id,kind FROM financial_accounts WHERE id=? AND is_active=1',
                                 (account_id,)).fetchone()
            if not account or paid_amount > total:
                raise ValueError('حساب یا مبلغ پرداخت اولیه نامعتبر است.')

        temporary_number = 'TMP-' + uuid.uuid4().hex
        cur = db.execute('''INSERT INTO invoices(number,branch_id,kind,party_id,total_rial,note,created_by)
            VALUES (?,1,?,?,?,?,?)''', (temporary_number, kind, party_id, total, note, user_id))
        invoice_id = cur.lastrowid
        settings = current_settings(db)
        prefix = settings['sale_prefix'] if kind == 'sale' else settings['purchase_prefix']
        document_number = prefix + str(invoice_id).zfill(settings['invoice_digits'])
        db.execute('UPDATE invoices SET number=? WHERE id=?', (document_number, invoice_id))

        for product_id, quantity, unit_price, unit_cost, tier_id, line_total, cost_provisional in prepared:
            stock = db.execute('SELECT quantity,avg_cost_rial FROM stock_balances WHERE branch_id=1 AND product_id=?',
                               (product_id,)).fetchone()
            if kind == 'sale':
                if stock['quantity'] < quantity:
                    raise ValueError('موجودی یکی از کالاها برای فروش کافی نیست.')
                unit_cost = stock['avg_cost_rial']
                db.execute('UPDATE stock_balances SET quantity=quantity-? WHERE branch_id=1 AND product_id=?',
                           (quantity, product_id))
                delta = -quantity
            else:
                new_quantity = stock['quantity'] + quantity
                new_avg = (stock['quantity'] * stock['avg_cost_rial'] + quantity * unit_cost + new_quantity // 2) // new_quantity if stock['quantity'] > 0 else unit_cost
                db.execute('''UPDATE stock_balances SET quantity=?,avg_cost_rial=?
                    WHERE branch_id=1 AND product_id=?''', (new_quantity, new_avg, product_id))
                delta = quantity
            db.execute('''INSERT INTO invoice_lines(invoice_id,product_id,quantity,unit_price_rial,
                unit_cost_rial,line_total_rial,price_type_id,cost_provisional) VALUES (?,?,?,?,?,?,?,?)''',
                (invoice_id, product_id, quantity, unit_price, unit_cost, line_total, tier_id, cost_provisional))
            db.execute('''INSERT INTO stock_movements(branch_id,product_id,delta_qty,reason,invoice_id,created_by)
                VALUES (1,?,?,?,?,?)''', (product_id, delta, kind, invoice_id, user_id))
        if paid_amount:
            db.execute('''INSERT INTO money_movements(branch_id,invoice_id,party_id,direction,method,
                category,amount_rial,note,created_by,account_id) VALUES (?,?,?,?,?,'invoice',?,?,?,?)''',
                (1, invoice_id, party_id, 'in' if kind == 'sale' else 'out', account['kind'],
                 paid_amount, 'پرداخت هنگام ثبت فاکتور', user_id, account_id))
        audit(db, 'invoice.post', 'invoice', invoice_id, {'kind': kind, 'total_rial': total})
    return invoice_id


@invoices.get('/invoices')
@require_login
def list_invoices():
    db = get_db()
    kind = request.args.get('kind', '')
    if kind not in ('', 'sale', 'purchase'):
        kind = ''
    rows, pager = paginate_query(db, '''SELECT i.*,p.name party_name,
        i.total_rial
        - CASE WHEN i.kind='sale' THEN COALESCE((SELECT SUM(total_rial) FROM sales_returns r WHERE r.sale_invoice_id=i.id),0)
          ELSE COALESCE((SELECT SUM(total_rial) FROM purchase_returns r WHERE r.purchase_invoice_id=i.id),0) END
        - COALESCE((SELECT SUM(amount_rial) FROM money_movements m WHERE m.invoice_id=i.id),0)
        - COALESCE((SELECT SUM(amount_rial) FROM payment_checks c WHERE c.invoice_id=i.id AND c.status='cleared'),0)
        + CASE WHEN i.kind='sale' THEN COALESCE((SELECT SUM(rf.amount_rial) FROM sales_return_refunds rf
          JOIN sales_returns sr ON sr.id=rf.return_id WHERE sr.sale_invoice_id=i.id),0)
          ELSE COALESCE((SELECT SUM(rf.amount_rial) FROM purchase_return_refunds rf
          JOIN purchase_returns pr ON pr.id=rf.return_id WHERE pr.purchase_invoice_id=i.id),0) END balance_rial
        FROM invoices i JOIN parties p ON p.id=i.party_id
        WHERE (?='' OR i.kind=?) ORDER BY i.id DESC''', (kind, kind))
    historical, historical_pager = paginate_query(db, '''SELECT id,title_code,factor_num,party_name,total_rial,date_jalali
        FROM legacy_invoices WHERE source_db='db_1405' AND title_code IN (1,2,3,4)
        ORDER BY date_jalali DESC,id DESC''', param='page_historical')
    historical_count = historical_pager.total
    return render_template('invoices.html', invoices=rows, kind=kind,
                           historical=historical, historical_count=historical_count,
                           pager=pager, historical_pager=historical_pager)


@invoices.route('/invoices/new/<kind>', methods=['GET', 'POST'])
@require_login
def new_invoice(kind):
    if kind not in ('sale', 'purchase'):
        abort(404)
    if request.method == 'POST':
        try:
            party_id = integer(request.form.get('party_id'), 'طرف حساب', minimum=1)
            note = request.form.get('note', '').strip()[:500]
            paid_amount = integer(request.form.get('paid_amount_rial') or '0', 'مبلغ پرداخت اولیه')
            account_id = integer(request.form.get('account_id') or '0', 'حساب', minimum=0) or None
            invoice_id = post_invoice(kind, party_id, parsed_lines(kind), note, g.user['id'],
                                      paid_amount, account_id)
            flash('فاکتور ثبت شد و موجودی به‌روز شد.', 'success')
            return redirect(url_for('invoices.invoice_detail', invoice_id=invoice_id))
        except (ValueError, sqlite3.IntegrityError) as exc:
            flash(str(exc), 'error')
    parties, products, tiers, prices, accounts = invoice_form_data(get_db(), kind)
    return render_template('invoice_new.html', kind=kind, parties=parties, products=products,
                           tiers=tiers, prices=[dict(x) for x in prices], accounts=accounts)


@invoices.get('/invoices/<int:invoice_id>')
@require_login
def invoice_detail(invoice_id):
    db = get_db()
    invoice = db.execute('''SELECT i.*,p.name party_name FROM invoices i
        JOIN parties p ON p.id=i.party_id WHERE i.id=?''', (invoice_id,)).fetchone()
    if not invoice:
        abort(404)
    lines, lines_pager = paginate_query(db, '''SELECT il.*,p.sku,p.name,t.label price_label FROM invoice_lines il
        JOIN products p ON p.id=il.product_id
        LEFT JOIN price_types t ON t.id=il.price_type_id WHERE il.invoice_id=? ORDER BY il.id''',
        (invoice_id,), param='page_lines')
    payments, payments_pager = paginate_query(db, '''SELECT m.*,a.name account_name FROM money_movements m
        LEFT JOIN financial_accounts a ON a.id=m.account_id WHERE invoice_id=? ORDER BY m.id DESC''',
        (invoice_id,), param='page_payments')
    accounts = db.execute('SELECT id,kind,name FROM financial_accounts WHERE is_active=1 ORDER BY kind,id').fetchall()
    cleared_checks, checks_pager = paginate_query(db,
        "SELECT * FROM payment_checks WHERE invoice_id=? AND status='cleared' ORDER BY id DESC",
        (invoice_id,), param='page_checks')
    if invoice['kind'] == 'sale':
        returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM sales_returns WHERE sale_invoice_id=?',
                              (invoice_id,)).fetchone()[0]
        refunds = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM sales_return_refunds r
            JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.sale_invoice_id=?''', (invoice_id,)).fetchone()[0]
    else:
        returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM purchase_returns WHERE purchase_invoice_id=?',
                              (invoice_id,)).fetchone()[0]
        refunds = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM purchase_return_refunds r
            JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.purchase_invoice_id=?''', (invoice_id,)).fetchone()[0]
    return_table, return_link = (('sales_returns', 'sale_invoice_id') if invoice['kind'] == 'sale'
                                 else ('purchase_returns', 'purchase_invoice_id'))
    related_returns, returns_pager = paginate_query(db,
        f'SELECT id,number,total_rial FROM {return_table} WHERE {return_link}=? ORDER BY id DESC',
        (invoice_id,), param='page_returns')
    sale_returns = related_returns if invoice['kind'] == 'sale' else []
    purchase_returns = related_returns if invoice['kind'] == 'purchase' else []
    paid = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM money_movements WHERE invoice_id=?',
                      (invoice_id,)).fetchone()[0]
    paid += db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE invoice_id=? AND status='cleared'",
                       (invoice_id,)).fetchone()[0]
    return render_template('invoice_detail.html', invoice=invoice, lines=lines, payments=payments,
                           cleared_checks=cleared_checks, sale_returns=sale_returns,
                           purchase_returns=purchase_returns, returned=returned,
                           outstanding=invoice['total_rial'] - returned - paid + refunds,
                           accounts=accounts, lines_pager=lines_pager, payments_pager=payments_pager,
                           checks_pager=checks_pager, returns_pager=returns_pager)


@invoices.post('/invoices/<int:invoice_id>/payments')
@require_login
def pay_invoice(invoice_id):
    try:
        amount = integer(request.form.get('amount_rial'), 'مبلغ پرداخت', minimum=1)
        account_id = integer(request.form.get('account_id'), 'حساب دریافت یا پرداخت', minimum=1)
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            account = db.execute('SELECT id,kind FROM financial_accounts WHERE id=? AND is_active=1',
                                 (account_id,)).fetchone()
            if not account:
                raise ValueError('حساب انتخابی فعال نیست.')
            invoice = db.execute('SELECT id,branch_id,party_id,kind,total_rial FROM invoices WHERE id=?',
                                 (invoice_id,)).fetchone()
            if not invoice:
                abort(404)
            paid = db.execute('SELECT COALESCE(SUM(amount_rial),0) paid FROM money_movements WHERE invoice_id=?',
                              (invoice_id,)).fetchone()['paid']
            paid += db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE invoice_id=? AND status='cleared'",
                               (invoice_id,)).fetchone()[0]
            if invoice['kind'] == 'sale':
                returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM sales_returns WHERE sale_invoice_id=?',
                                      (invoice_id,)).fetchone()[0]
                refunded = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM sales_return_refunds r
                    JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.sale_invoice_id=?''', (invoice_id,)).fetchone()[0]
            else:
                returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM purchase_returns WHERE purchase_invoice_id=?',
                                      (invoice_id,)).fetchone()[0]
                refunded = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM purchase_return_refunds r
                    JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.purchase_invoice_id=?''', (invoice_id,)).fetchone()[0]
            if amount > invoice['total_rial'] - returned - paid + refunded:
                raise ValueError('مبلغ پرداخت از مانده فاکتور بیشتر است.')
            direction = 'in' if invoice['kind'] == 'sale' else 'out'
            cur = db.execute('''INSERT INTO money_movements(branch_id,invoice_id,party_id,direction,method,
                category,amount_rial,note,created_by,account_id) VALUES (?,?,?,?,?,'invoice',?,?,?,?)''',
                (invoice['branch_id'], invoice_id, invoice['party_id'], direction, account['kind'],
                 amount, note, g.user['id'], account_id))
            audit(db, 'invoice.payment', 'money_movement', cur.lastrowid, {'invoice_id': invoice_id, 'amount_rial': amount})
        flash('پرداخت ثبت شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('invoices.invoice_detail', invoice_id=invoice_id))
