"""Sales returns reverse stock and receivables with traceable refunds."""
import sqlite3
import uuid
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import require_login, require_role
from .db import get_db, transaction
from .pagination import paginate_query
from .utils import audit, integer, required

returns = Blueprint('returns', __name__)


def sale_credit(db, sale_id):
    sale = db.execute("SELECT total_rial FROM invoices WHERE id=? AND kind='sale'", (sale_id,)).fetchone()
    if not sale:
        raise ValueError('فاکتور فروش پیدا نشد.')
    returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM sales_returns WHERE sale_invoice_id=?',
                          (sale_id,)).fetchone()[0]
    paid_cash = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM money_movements WHERE invoice_id=?',
                           (sale_id,)).fetchone()[0]
    paid_check = db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE invoice_id=? AND status='cleared'",
                            (sale_id,)).fetchone()[0]
    refunded = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM sales_return_refunds r
        JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.sale_invoice_id=?''', (sale_id,)).fetchone()[0]
    return max(0, paid_cash + paid_check - (sale['total_rial'] - returned) - refunded)


@returns.get('/returns')
@require_login
def list_returns():
    rows, pager = paginate_query(get_db(), '''SELECT sr.*,i.number sale_number,p.name party_name,
      COALESCE((SELECT SUM(amount_rial) FROM sales_return_refunds r WHERE r.return_id=sr.id),0) refunded
      FROM sales_returns sr JOIN invoices i ON i.id=sr.sale_invoice_id
      JOIN parties p ON p.id=sr.party_id ORDER BY sr.id DESC''')
    return render_template('returns.html', rows=rows, pager=pager)


@returns.get('/returns/new')
@require_role('owner', 'manager', 'clerk')
def select_sale():
    q = request.args.get('q', '').strip()[:80]
    rows, pager = paginate_query(get_db(), '''SELECT i.id,i.number,i.created_at,p.name party_name
      FROM invoices i JOIN parties p ON p.id=i.party_id WHERE i.kind='sale'
      AND (?='' OR i.number LIKE ? OR p.name LIKE ?)
      AND EXISTS(SELECT 1 FROM invoice_lines il WHERE il.invoice_id=i.id
        AND il.quantity>COALESCE((SELECT SUM(rl.quantity) FROM sales_return_lines rl
          WHERE rl.invoice_line_id=il.id),0))
      ORDER BY i.id DESC''', (q, '%' + q + '%', '%' + q + '%'))
    return render_template('return_select_invoice.html', kind='sale', rows=rows, pager=pager, q=q)


@returns.route('/returns/new/<int:sale_id>', methods=['GET', 'POST'])
@require_role('owner', 'manager', 'clerk')
def new_return(sale_id):
    db = get_db()
    sale = db.execute('''SELECT i.*,p.name party_name FROM invoices i
        JOIN parties p ON p.id=i.party_id WHERE i.id=? AND i.kind='sale' ''', (sale_id,)).fetchone()
    if not sale:
        abort(404)
    if request.method == 'POST':
        try:
            reason = required(request.form.get('reason'), 'دلیل برگشت', 300)
            quantities = request.form.getlist('quantity')
            line_ids = request.form.getlist('line_id')
            if not quantities:
                raise ValueError('تعداد برگشتی را مشخص کنید.')
            with transaction(db):
                lines = db.execute('''SELECT il.*,p.name product_name,
                  COALESCE((SELECT SUM(quantity) FROM sales_return_lines rl WHERE rl.invoice_line_id=il.id),0) returned
                  FROM invoice_lines il JOIN products p ON p.id=il.product_id
                  WHERE il.invoice_id=? ORDER BY il.id''', (sale_id,)).fetchall()
                if line_ids:
                    if len(line_ids) != len(quantities) or len(line_ids) != len(set(line_ids)):
                        raise ValueError('ردیف‌های برگشت معتبر نیست.')
                    quantities_by_id = {integer(line_id, 'ردیف فاکتور', minimum=1): raw
                                        for line_id, raw in zip(line_ids, quantities)}
                    if not set(quantities_by_id).issubset({line['id'] for line in lines}):
                        raise ValueError('ردیف انتخابی متعلق به این فاکتور نیست.')
                elif len(lines) == len(quantities):
                    quantities_by_id = {line['id']: raw for line, raw in zip(lines, quantities)}
                else:
                    raise ValueError('تعداد ردیف‌های برگشت معتبر نیست.')
                selected = []
                total = cost_total = 0
                for line in lines:
                    raw = quantities_by_id.get(line['id'], '0')
                    qty = integer(raw or '0', 'تعداد برگشتی', maximum=10**8)
                    if qty > line['quantity'] - line['returned']:
                        raise ValueError('تعداد برگشتی از باقیماندهٔ ردیف بیشتر است.')
                    if qty:
                        selected.append((line, qty))
                        total += qty * line['unit_price_rial']
                        cost_total += qty * line['unit_cost_rial']
                if not selected or total <= 0:
                    raise ValueError('حداقل یک ردیف با تعداد و مبلغ مثبت انتخاب کنید.')
                temporary = 'TMP-' + uuid.uuid4().hex
                cur = db.execute('''INSERT INTO sales_returns(number,sale_invoice_id,party_id,total_rial,
                    cost_total_rial,reason,created_by) VALUES (?,?,?,?,?,?,?)''',
                    (temporary, sale_id, sale['party_id'], total, cost_total, reason, g.user['id']))
                return_id = cur.lastrowid
                db.execute('UPDATE sales_returns SET number=? WHERE id=?', ('RET' + str(return_id).zfill(7), return_id))
                for line, qty in selected:
                    amount = qty * line['unit_price_rial']
                    db.execute('''INSERT INTO sales_return_lines(return_id,invoice_line_id,product_id,quantity,
                        unit_price_rial,unit_cost_rial,line_total_rial) VALUES (?,?,?,?,?,?,?)''',
                        (return_id, line['id'], line['product_id'], qty, line['unit_price_rial'],
                         line['unit_cost_rial'], amount))
                    stock = db.execute('SELECT quantity,avg_cost_rial FROM stock_balances WHERE branch_id=1 AND product_id=?',
                                       (line['product_id'],)).fetchone()
                    new_qty = stock['quantity'] + qty
                    if stock['quantity'] >= 0 and new_qty > 0:
                        new_avg = (stock['quantity'] * stock['avg_cost_rial'] + qty * line['unit_cost_rial'] + new_qty // 2) // new_qty
                    else:
                        new_avg = line['unit_cost_rial']
                    db.execute('''UPDATE stock_balances SET quantity=?,avg_cost_rial=?
                        WHERE branch_id=1 AND product_id=?''', (new_qty, new_avg, line['product_id']))
                    db.execute('''INSERT INTO stock_movements(branch_id,product_id,delta_qty,reason,invoice_id,note,created_by)
                        VALUES (1,? ,?,'sale_return',?,?,?)''',
                        (line['product_id'], qty, sale_id, f'برگشت فروش RET{return_id:07d}', g.user['id']))
                audit(db, 'sale.return', 'sales_return', return_id, {'sale_id': sale_id, 'total_rial': total})
            flash('برگشت از فروش ثبت و موجودی اصلاح شد.', 'success')
            return redirect(url_for('returns.return_detail', return_id=return_id))
        except (ValueError, sqlite3.IntegrityError) as exc:
            flash(str(exc), 'error')
    lines, pager = paginate_query(db, '''SELECT il.*,p.name product_name,p.sku,
      COALESCE((SELECT SUM(quantity) FROM sales_return_lines rl WHERE rl.invoice_line_id=il.id),0) returned
      FROM invoice_lines il JOIN products p ON p.id=il.product_id
      WHERE il.invoice_id=? ORDER BY il.id''', (sale_id,))
    return render_template('return_new.html', sale=sale, lines=lines, pager=pager)


@returns.get('/returns/<int:return_id>')
@require_login
def return_detail(return_id):
    db = get_db()
    row = db.execute('''SELECT sr.*,i.number sale_number,p.name party_name
      FROM sales_returns sr JOIN invoices i ON i.id=sr.sale_invoice_id
      JOIN parties p ON p.id=sr.party_id WHERE sr.id=?''', (return_id,)).fetchone()
    if not row:
        abort(404)
    lines, lines_pager = paginate_query(db, '''SELECT rl.*,p.name product_name,p.sku FROM sales_return_lines rl
      JOIN products p ON p.id=rl.product_id WHERE rl.return_id=? ORDER BY rl.id''',
      (return_id,), param='page_lines')
    refunds, refunds_pager = paginate_query(db, 'SELECT * FROM sales_return_refunds WHERE return_id=? ORDER BY id DESC',
                                           (return_id,), param='page_refunds')
    refunded = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM sales_return_refunds WHERE return_id=?',
                          (return_id,)).fetchone()[0]
    refundable = min(row['total_rial'] - refunded, sale_credit(db, row['sale_invoice_id']))
    return render_template('return_detail.html', row=row, lines=lines, refunds=refunds, refundable=refundable,
                           lines_pager=lines_pager, refunds_pager=refunds_pager)


@returns.post('/returns/<int:return_id>/refunds')
@require_role('owner', 'manager')
def refund(return_id):
    try:
        amount = integer(request.form.get('amount_rial'), 'مبلغ بازپرداخت', minimum=1)
        method = request.form.get('method', '')
        if method not in ('cash', 'bank'):
            raise ValueError('روش بازپرداخت نامعتبر است.')
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            row = db.execute('SELECT id,sale_invoice_id,total_rial FROM sales_returns WHERE id=?', (return_id,)).fetchone()
            if not row:
                abort(404)
            refunded = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM sales_return_refunds WHERE return_id=?',
                                  (return_id,)).fetchone()[0]
            limit = min(row['total_rial'] - refunded, sale_credit(db, row['sale_invoice_id']))
            if amount > limit:
                raise ValueError('مبلغ بازپرداخت از بستانکاری قابل بازپرداخت بیشتر است.')
            cur = db.execute('''INSERT INTO sales_return_refunds(return_id,method,amount_rial,note,created_by)
                VALUES (?,?,?,?,?)''', (return_id, method, amount, note, g.user['id']))
            audit(db, 'sale.return.refund', 'sales_return_refund', cur.lastrowid)
        flash('بازپرداخت ثبت شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('returns.return_detail', return_id=return_id))
