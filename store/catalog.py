"""Products, named price tiers, immutable price history, and stock corrections."""
import sqlite3

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import require_login, require_role
from .db import get_db, transaction
from .pagination import paginate_query, page_url
from .search import normalize_code, normalize_name
from .settings import current_settings
from .utils import audit, integer, required

catalog = Blueprint('catalog', __name__)


def product_rows(db, term=''):
    name_term = normalize_name(term)
    code_term = normalize_code(term)
    name_pattern = '%' + name_term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    code_pattern = '%' + code_term + '%' if code_term else '<no-code-match>'
    prefix_pattern = code_term + '%' if code_term else '<no-code-match>'
    return paginate_query(db, '''SELECT p.*,COALESCE(sb.quantity,0) stock,COALESCE(sb.avg_cost_rial,0) avg_cost
        FROM products p LEFT JOIN stock_balances sb ON sb.product_id=p.id AND sb.branch_id=1
        WHERE p.is_active=1 AND (?='' OR p.search_name LIKE ? ESCAPE '\\' OR p.search_sku LIKE ?)
        ORDER BY CASE WHEN p.search_sku=? THEN 0 WHEN p.search_sku LIKE ? THEN 1 ELSE 2 END,p.name''',
        (name_term, name_pattern, code_pattern, code_term, prefix_pattern))


@catalog.get('/products')
@require_login
def list_products():
    db = get_db()
    term = request.args.get('q', '').strip()[:80]
    products, pager = product_rows(db, term)
    stock_summary = db.execute('''SELECT COUNT(*) products,
        COALESCE(SUM(sb.quantity),0) units,
        COALESCE(SUM(CASE WHEN sb.quantity>0 THEN 1 ELSE 0 END),0) positive,
        COALESCE(SUM(CASE WHEN sb.quantity<0 THEN 1 ELSE 0 END),0) negative
        FROM products p JOIN stock_balances sb ON sb.product_id=p.id AND sb.branch_id=1
        WHERE p.is_active=1''').fetchone()
    tiers = db.execute('SELECT * FROM price_types WHERE is_active=1 ORDER BY id').fetchall()
    product_ids = [row['id'] for row in products]
    prices = db.execute('SELECT product_id,price_type_id,amount_rial FROM product_prices WHERE is_active=1 AND product_id IN (' + ','.join('?' for _ in product_ids) + ')', product_ids).fetchall() if product_ids else []
    by_product = {(row['product_id'], row['price_type_id']): row['amount_rial'] for row in prices}
    return render_template('products.html', products=products, tiers=tiers, prices=by_product,
                           stock_summary=stock_summary, term=term, pager=pager)


@catalog.post('/products')
@require_role('owner', 'manager')
def create_product():
    try:
        sku = required(request.form.get('sku'), 'کد کالا', 60)
        name = required(request.form.get('name'), 'نام کالا')
        brand = request.form.get('brand', '').strip()[:80]
        category = request.form.get('category', '').strip()[:80]
        min_stock = integer(request.form.get('min_stock', current_settings()['default_min_stock']),
                            'حداقل موجودی', maximum=10**8)
        with transaction(get_db()) as db:
            cur = db.execute('''INSERT INTO products(sku,name,brand,category,min_stock,search_name,search_sku)
                VALUES (?,?,?,?,?,?,?)''',
                (sku, name, brand, category, min_stock,
                 normalize_name(' '.join((name, brand, category))), normalize_code(sku)))
            db.execute('INSERT INTO stock_balances(branch_id,product_id) VALUES (1,?)', (cur.lastrowid,))
            audit(db, 'product.create', 'product', cur.lastrowid)
        flash('کالا ثبت شد.', 'success')
        return redirect(url_for('catalog.product_detail', product_id=cur.lastrowid))
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash('کد کالا تکراری است یا داده نامعتبر است: ' + str(exc), 'error')
        return redirect(url_for('catalog.list_products'))


@catalog.get('/products/<int:product_id>')
@require_login
def product_detail(product_id):
    db = get_db()
    product = db.execute('''SELECT p.*,COALESCE(sb.quantity,0) stock,COALESCE(sb.avg_cost_rial,0) avg_cost
        FROM products p LEFT JOIN stock_balances sb ON sb.product_id=p.id AND sb.branch_id=1
        WHERE p.id=?''', (product_id,)).fetchone()
    if not product:
        abort(404)
    tier_query = '''SELECT t.*,pp.amount_rial,COALESCE(pp.is_active,0) price_active
        FROM price_types t LEFT JOIN product_prices pp ON pp.price_type_id=t.id AND pp.product_id=?
        WHERE t.is_active=1 ORDER BY t.id'''
    tiers = db.execute(tier_query, (product_id,)).fetchall()
    tiers_page, tiers_pager = paginate_query(db, tier_query, (product_id,), param='page_tiers')
    history, history_pager = paginate_query(db, '''SELECT pc.*,t.label,u.display_name FROM price_changes pc
        JOIN price_types t ON t.id=pc.price_type_id JOIN users u ON u.id=pc.changed_by
        WHERE pc.product_id=? ORDER BY pc.id DESC''', (product_id,), param='page_history')
    movements, movements_pager = paginate_query(db, '''SELECT sm.*,u.display_name FROM stock_movements sm
        JOIN users u ON u.id=sm.created_by WHERE sm.product_id=? ORDER BY sm.id DESC''',
        (product_id,), param='page_stock')
    suggestion = db.execute('''SELECT * FROM legacy_price_suggestions
        WHERE source_db='db_1405' AND product_code=? ORDER BY import_id DESC LIMIT 1''',
        (product['sku'],)).fetchone()
    return render_template('product_detail.html', product=product, tiers=tiers, history=history,
                           movements=movements, suggestion=suggestion, tiers_page=tiers_page,
                           tiers_pager=tiers_pager, history_pager=history_pager,
                           movements_pager=movements_pager)


@catalog.post('/products/<int:product_id>/edit')
@require_role('owner', 'manager')
def edit_product(product_id):
    try:
        name = required(request.form.get('name'), 'نام کالا')
        brand = request.form.get('brand', '').strip()[:80]
        category = request.form.get('category', '').strip()[:80]
        min_stock = integer(request.form.get('min_stock', '0'), 'حداقل موجودی', maximum=10**8)
        with transaction(get_db()) as db:
            cur = db.execute('UPDATE products SET name=?,brand=?,category=?,min_stock=?,search_name=? WHERE id=?',
                             (name, brand, category, min_stock,
                              normalize_name(' '.join((name, brand, category))), product_id))
            if not cur.rowcount:
                abort(404)
            audit(db, 'product.edit', 'product', product_id)
        flash('مشخصات کالا ذخیره شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('catalog.product_detail', product_id=product_id))


@catalog.post('/products/<int:product_id>/prices')
@require_role('owner', 'manager')
def set_price(product_id):
    try:
        tier_id = integer(request.form.get('price_type_id'), 'نوع قیمت', minimum=1)
        amount = integer(request.form.get('amount_rial'), 'قیمت')
        with transaction(get_db()) as db:
            if not db.execute('SELECT 1 FROM products WHERE id=? AND is_active=1', (product_id,)).fetchone():
                abort(404)
            if not db.execute('SELECT 1 FROM price_types WHERE id=? AND is_active=1', (tier_id,)).fetchone():
                raise ValueError('نوع قیمت وجود ندارد.')
            old = db.execute('SELECT amount_rial,is_active FROM product_prices WHERE product_id=? AND price_type_id=?',
                             (product_id, tier_id)).fetchone()
            db.execute('''INSERT INTO product_prices(product_id,price_type_id,amount_rial,updated_by)
                VALUES (?,?,?,?) ON CONFLICT(product_id,price_type_id) DO UPDATE SET
                amount_rial=excluded.amount_rial,is_active=1,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
                (product_id, tier_id, amount, g.user['id']))
            db.execute('''INSERT INTO price_changes(product_id,price_type_id,old_amount_rial,new_amount_rial,changed_by)
                VALUES (?,?,?,?,?)''', (product_id, tier_id, old['amount_rial'] if old and old['is_active'] else None,
                                       amount, g.user['id']))
            audit(db, 'price.set', 'product', product_id, {'tier_id': tier_id, 'amount_rial': amount})
        flash('قیمت ذخیره شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('catalog.product_detail', product_id=product_id))


@catalog.post('/products/<int:product_id>/prices/<int:tier_id>/remove')
@require_role('owner', 'manager')
def remove_price(product_id, tier_id):
    with transaction(get_db()) as db:
        old = db.execute('SELECT amount_rial FROM product_prices WHERE product_id=? AND price_type_id=? AND is_active=1',
                         (product_id, tier_id)).fetchone()
        if old:
            db.execute('UPDATE product_prices SET is_active=0,updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE product_id=? AND price_type_id=?',
                       (g.user['id'], product_id, tier_id))
            db.execute('INSERT INTO price_changes(product_id,price_type_id,old_amount_rial,new_amount_rial,changed_by) VALUES (?,?,?,?,?)',
                       (product_id, tier_id, old['amount_rial'], None, g.user['id']))
            audit(db, 'price.remove', 'product', product_id, {'tier_id': tier_id})
    flash('قیمت از فهرست فعال حذف شد و سابقه‌اش باقی ماند.', 'success')
    return redirect(url_for('catalog.product_detail', product_id=product_id))


@catalog.post('/price-types')
@require_role('owner', 'manager')
def create_price_type():
    try:
        label = required(request.form.get('label'), 'عنوان نوع قیمت', 50)
        with transaction(get_db()) as db:
            code = 'custom-' + __import__('secrets').token_hex(5)
            cur = db.execute('INSERT INTO price_types(code,label) VALUES (?,?)', (code, label))
            audit(db, 'price_type.create', 'price_type', cur.lastrowid)
        flash('نوع قیمت اضافه شد.', 'success')
    except (ValueError, sqlite3.IntegrityError):
        flash('عنوان نوع قیمت تکراری یا نامعتبر است.', 'error')
    return redirect(url_for('catalog.list_products'))


@catalog.post('/products/<int:product_id>/stock-adjustments')
@require_role('owner', 'manager')
def adjust_stock(product_id):
    try:
        delta = integer(request.form.get('delta_qty'), 'تغییر موجودی', minimum=-(10**8), maximum=10**8)
        if delta == 0:
            raise ValueError('تغییر موجودی نمی‌تواند صفر باشد.')
        note = required(request.form.get('note'), 'دلیل اصلاح موجودی', 240)
        with transaction(get_db()) as db:
            current = db.execute('SELECT quantity FROM stock_balances WHERE branch_id=1 AND product_id=?',
                                 (product_id,)).fetchone()
            if not current:
                abort(404)
            if current['quantity'] + delta < 0:
                raise ValueError('موجودی نمی‌تواند منفی شود.')
            db.execute('UPDATE stock_balances SET quantity=quantity+? WHERE branch_id=1 AND product_id=?',
                       (delta, product_id))
            db.execute('''INSERT INTO stock_movements(branch_id,product_id,delta_qty,reason,note,created_by)
                VALUES (1,? ,?,'adjustment',?,?)''', (product_id, delta, note, g.user['id']))
            audit(db, 'stock.adjust', 'product', product_id, {'delta': delta, 'note': note})
        flash('موجودی اصلاح شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('catalog.product_detail', product_id=product_id))


@catalog.post('/products/<int:product_id>/confirm-cost')
@require_role('owner', 'manager')
def confirm_cost(product_id):
    try:
        cost = integer(request.form.get('avg_cost_rial'), 'بهای تمام‌شده', minimum=1)
        note = required(request.form.get('note'), 'مبنای تأیید بهای تمام‌شده', 240)
        with transaction(get_db()) as db:
            product = db.execute('SELECT id FROM products WHERE id=?', (product_id,)).fetchone()
            if not product:
                abort(404)
            db.execute('UPDATE stock_balances SET avg_cost_rial=? WHERE branch_id=1 AND product_id=?',
                       (cost, product_id))
            db.execute('UPDATE products SET cost_confirmed=1 WHERE id=?', (product_id,))
            audit(db, 'product.cost.confirm', 'product', product_id, {'cost_rial': cost, 'note': note})
        flash('بهای تمام‌شده تأیید شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('catalog.product_detail', product_id=product_id))


@catalog.get('/api/v1/prices')
@require_login
def price_api():
    rows, pager = paginate_query(get_db(), '''SELECT p.id,p.sku,p.name,p.brand,t.code price_code,t.label price_label,
        pp.amount_rial,COALESCE(sb.quantity,0) stock
        FROM products p JOIN product_prices pp ON pp.product_id=p.id AND pp.is_active=1
        JOIN price_types t ON t.id=pp.price_type_id AND t.is_active=1
        LEFT JOIN stock_balances sb ON sb.product_id=p.id AND sb.branch_id=1
        WHERE p.is_active=1 ORDER BY p.name,t.id''')
    return jsonify({'items': [dict(row) for row in rows], 'page': pager.page,
                    'pages': pager.pages, 'total': pager.total,
                    'next_page': page_url(pager, pager.page + 1) if pager.page < pager.pages else None})
