"""Purchase returns reduce payable and stock, while preserving source invoice links."""
import sqlite3
import uuid
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import require_login, require_role
from .db import get_db, transaction
from .pagination import paginate_query
from .utils import audit, integer, required

purchase_returns = Blueprint('purchase_returns', __name__)


def supplier_credit(db, purchase_id):
    invoice = db.execute("SELECT total_rial FROM invoices WHERE id=? AND kind='purchase'", (purchase_id,)).fetchone()
    if not invoice:
        raise ValueError('فاکتور خرید پیدا نشد.')
    returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM purchase_returns WHERE purchase_invoice_id=?',
                          (purchase_id,)).fetchone()[0]
    paid = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM money_movements WHERE invoice_id=?',
                      (purchase_id,)).fetchone()[0]
    paid += db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE invoice_id=? AND status='cleared'",
                       (purchase_id,)).fetchone()[0]
    refunded = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM purchase_return_refunds r
      JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.purchase_invoice_id=?''', (purchase_id,)).fetchone()[0]
    return max(0, paid - (invoice['total_rial'] - returned) - refunded)


@purchase_returns.get('/purchase-returns')
@require_login
def list_returns():
    rows, pager = paginate_query(get_db(), '''SELECT pr.*,i.number purchase_number,p.name party_name,
      COALESCE((SELECT SUM(amount_rial) FROM purchase_return_refunds r WHERE r.return_id=pr.id),0) refunded
      FROM purchase_returns pr JOIN invoices i ON i.id=pr.purchase_invoice_id
      JOIN parties p ON p.id=pr.party_id ORDER BY pr.id DESC''')
    return render_template('purchase_returns.html', rows=rows, pager=pager)


@purchase_returns.get('/purchase-returns/new')
@require_role('owner', 'manager')
def select_purchase():
    q = request.args.get('q', '').strip()[:80]
    rows, pager = paginate_query(get_db(), '''SELECT i.id,i.number,i.created_at,p.name party_name
      FROM invoices i JOIN parties p ON p.id=i.party_id WHERE i.kind='purchase'
      AND (?='' OR i.number LIKE ? OR p.name LIKE ?)
      AND EXISTS(SELECT 1 FROM invoice_lines il JOIN stock_balances sb
        ON sb.product_id=il.product_id AND sb.branch_id=1 WHERE il.invoice_id=i.id
        AND sb.quantity>0 AND il.quantity>COALESCE((SELECT SUM(rl.quantity) FROM purchase_return_lines rl
          WHERE rl.invoice_line_id=il.id),0))
      ORDER BY i.id DESC''', (q, '%' + q + '%', '%' + q + '%'))
    return render_template('return_select_invoice.html', kind='purchase', rows=rows, pager=pager, q=q)


@purchase_returns.route('/purchase-returns/new/<int:purchase_id>', methods=['GET', 'POST'])
@require_role('owner', 'manager')
def new_return(purchase_id):
    db = get_db()
    invoice = db.execute('''SELECT i.*,p.name party_name FROM invoices i
      JOIN parties p ON p.id=i.party_id WHERE i.id=? AND i.kind='purchase' ''', (purchase_id,)).fetchone()
    if not invoice:
        abort(404)
    if request.method == 'POST':
        try:
            reason = required(request.form.get('reason'), 'دلیل برگشت خرید', 300)
            quantities = request.form.getlist('quantity')
            line_ids = request.form.getlist('line_id')
            with transaction(db):
                lines = db.execute('''SELECT il.*,p.name product_name,
                  COALESCE((SELECT SUM(quantity) FROM purchase_return_lines rl WHERE rl.invoice_line_id=il.id),0) returned
                  FROM invoice_lines il JOIN products p ON p.id=il.product_id
                  WHERE il.invoice_id=? ORDER BY il.id''', (purchase_id,)).fetchall()
                if not quantities:
                    raise ValueError('ردیف‌های برگشت خرید معتبر نیست.')
                if line_ids:
                    if len(line_ids) != len(quantities) or len(line_ids) != len(set(line_ids)):
                        raise ValueError('ردیف‌های برگشت خرید معتبر نیست.')
                    quantities_by_id = {integer(line_id, 'ردیف فاکتور', minimum=1): raw
                                        for line_id, raw in zip(line_ids, quantities)}
                    if not set(quantities_by_id).issubset({line['id'] for line in lines}):
                        raise ValueError('ردیف انتخابی متعلق به این فاکتور نیست.')
                elif len(lines) == len(quantities):
                    quantities_by_id = {line['id']: raw for line, raw in zip(lines, quantities)}
                else:
                    raise ValueError('ردیف‌های برگشت خرید معتبر نیست.')
                selected = []
                total = stock_cost = 0
                for line in lines:
                    raw = quantities_by_id.get(line['id'], '0')
                    qty = integer(raw or '0', 'تعداد برگشتی', maximum=10**8)
                    if qty > line['quantity'] - line['returned']:
                        raise ValueError('تعداد برگشتی از باقیماندهٔ خرید بیشتر است.')
                    if not qty:
                        continue
                    current = db.execute('SELECT quantity,avg_cost_rial FROM stock_balances WHERE branch_id=1 AND product_id=?',
                                         (line['product_id'],)).fetchone()
                    if not current or current['quantity'] < qty:
                        raise ValueError('موجودی این کالا برای برگشت به تأمین‌کننده کافی نیست.')
                    selected.append((line, qty, current['avg_cost_rial']))
                    total += qty * line['unit_price_rial']
                    stock_cost += qty * current['avg_cost_rial']
                if not selected or total <= 0:
                    raise ValueError('حداقل یک ردیف با تعداد و مبلغ مثبت انتخاب کنید.')
                temporary = 'TMP-' + uuid.uuid4().hex
                cur = db.execute('''INSERT INTO purchase_returns(number,purchase_invoice_id,party_id,total_rial,
                    stock_cost_rial,reason,created_by) VALUES (?,?,?,?,?,?,?)''',
                    (temporary, purchase_id, invoice['party_id'], total, stock_cost, reason, g.user['id']))
                return_id = cur.lastrowid
                number = 'PRT' + str(return_id).zfill(7)
                db.execute('UPDATE purchase_returns SET number=? WHERE id=?', (number, return_id))
                for line, qty, unit_stock_cost in selected:
                    db.execute('''INSERT INTO purchase_return_lines(return_id,invoice_line_id,product_id,quantity,
                        unit_purchase_rial,unit_stock_cost_rial,line_total_rial) VALUES (?,?,?,?,?,?,?)''',
                        (return_id, line['id'], line['product_id'], qty, line['unit_price_rial'],
                         unit_stock_cost, qty * line['unit_price_rial']))
                    db.execute('UPDATE stock_balances SET quantity=quantity-? WHERE branch_id=1 AND product_id=?',
                               (qty, line['product_id']))
                    db.execute('''INSERT INTO stock_movements(branch_id,product_id,delta_qty,reason,invoice_id,note,created_by)
                        VALUES (1,? ,?,'purchase_return',?,?,?)''',
                        (line['product_id'], -qty, purchase_id, f'برگشت خرید {number}', g.user['id']))
                audit(db, 'purchase.return', 'purchase_return', return_id,
                      {'purchase_id': purchase_id, 'total_rial': total})
            flash('برگشت از خرید ثبت و موجودی اصلاح شد.', 'success')
            return redirect(url_for('purchase_returns.return_detail', return_id=return_id))
        except (ValueError, sqlite3.IntegrityError) as exc:
            flash(str(exc), 'error')
    lines, pager = paginate_query(db, '''SELECT il.*,p.name product_name,p.sku,
      COALESCE((SELECT SUM(quantity) FROM purchase_return_lines rl WHERE rl.invoice_line_id=il.id),0) returned
      FROM invoice_lines il JOIN products p ON p.id=il.product_id
      WHERE il.invoice_id=? ORDER BY il.id''', (purchase_id,))
    return render_template('purchase_return_new.html', invoice=invoice, lines=lines, pager=pager)


@purchase_returns.get('/purchase-returns/<int:return_id>')
@require_login
def return_detail(return_id):
    db = get_db()
    row = db.execute('''SELECT pr.*,i.number purchase_number,p.name party_name
      FROM purchase_returns pr JOIN invoices i ON i.id=pr.purchase_invoice_id
      JOIN parties p ON p.id=pr.party_id WHERE pr.id=?''', (return_id,)).fetchone()
    if not row:
        abort(404)
    lines, lines_pager = paginate_query(db, '''SELECT rl.*,p.name product_name,p.sku FROM purchase_return_lines rl
      JOIN products p ON p.id=rl.product_id WHERE rl.return_id=? ORDER BY rl.id''',
      (return_id,), param='page_lines')
    refunds, refunds_pager = paginate_query(db, 'SELECT * FROM purchase_return_refunds WHERE return_id=? ORDER BY id DESC',
                                           (return_id,), param='page_refunds')
    refunded = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM purchase_return_refunds WHERE return_id=?',
                          (return_id,)).fetchone()[0]
    refundable = min(row['total_rial'] - refunded,
                     supplier_credit(db, row['purchase_invoice_id']))
    return render_template('purchase_return_detail.html', row=row, lines=lines,
                           refunds=refunds, refundable=refundable,
                           lines_pager=lines_pager, refunds_pager=refunds_pager)


@purchase_returns.post('/purchase-returns/<int:return_id>/refunds')
@require_role('owner', 'manager')
def refund(return_id):
    try:
        amount = integer(request.form.get('amount_rial'), 'مبلغ دریافت از تأمین‌کننده', minimum=1)
        method = request.form.get('method', '')
        if method not in ('cash', 'bank'):
            raise ValueError('روش دریافت نامعتبر است.')
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            row = db.execute('SELECT id,purchase_invoice_id,total_rial FROM purchase_returns WHERE id=?',
                             (return_id,)).fetchone()
            if not row:
                abort(404)
            refunded = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM purchase_return_refunds WHERE return_id=?',
                                  (return_id,)).fetchone()[0]
            limit = min(row['total_rial'] - refunded, supplier_credit(db, row['purchase_invoice_id']))
            if amount > limit:
                raise ValueError('مبلغ دریافت از بستانکاری قابل وصول بیشتر است.')
            cur = db.execute('''INSERT INTO purchase_return_refunds(return_id,method,amount_rial,note,created_by)
                VALUES (?,?,?,?,?)''', (return_id, method, amount, note, g.user['id']))
            audit(db, 'purchase.return.refund', 'purchase_return_refund', cur.lastrowid)
        flash('دریافت از تأمین‌کننده ثبت شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('purchase_returns.return_detail', return_id=return_id))
