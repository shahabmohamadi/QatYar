"""Received/issued checks with due dates and audited settlement states."""
import sqlite3
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .auth import require_role
from .calendar import format_date, parse_date
from .db import get_db, transaction
from .pagination import paginate_query
from .settings import current_settings
from .utils import audit, integer, required

checks = Blueprint('checks', __name__)
_TRANSITIONS = {
    'pending': {'deposited', 'cleared', 'bounced', 'cancelled'},
    'deposited': {'cleared', 'bounced', 'cancelled'},
    'bounced': {'pending', 'cancelled'},
    'cleared': set(), 'cancelled': set(),
}


@checks.get('/checks')
@require_role('owner', 'manager')
def list_checks():
    db = get_db()
    status = request.args.get('status', '')
    if status not in ('', *_TRANSITIONS):
        status = ''
    rows, pager = paginate_query(db, '''SELECT c.*,p.name party_name,i.number invoice_number FROM payment_checks c
      LEFT JOIN parties p ON p.id=c.party_id LEFT JOIN invoices i ON i.id=c.invoice_id
      WHERE (?='' OR c.status=?) ORDER BY c.due_date_jalali,c.id DESC''', (status, status))
    historical, historical_pager = paginate_query(db, '''SELECT source_db,check_number,bank_name,amount_rial,
      due_date_jalali,kind_code,status_code,is_active,is_cancelled,note
      FROM legacy_checks ORDER BY source_db DESC,due_date_jalali DESC,id DESC''', param='page_historical')
    parties = db.execute('SELECT id,name FROM parties WHERE is_active=1 ORDER BY name').fetchall()
    invoices = db.execute('''SELECT i.id,i.number,p.name party_name,i.kind FROM invoices i
      JOIN parties p ON p.id=i.party_id ORDER BY i.id DESC''').fetchall()
    return render_template('checks.html', rows=rows, parties=parties, invoices=invoices,
                           status=status, historical=historical, pager=pager,
                           historical_pager=historical_pager)


@checks.post('/checks')
@require_role('owner', 'manager')
def create_check():
    try:
        direction = request.form.get('direction', '')
        if direction not in ('received', 'issued'):
            raise ValueError('نوع چک معتبر نیست.')
        party_id = integer(request.form.get('party_id') or '0', 'طرف حساب') or None
        invoice_id = integer(request.form.get('invoice_id') or '0', 'فاکتور') or None
        number = required(request.form.get('check_number'), 'شماره چک', 50)
        bank = request.form.get('bank_name', '').strip()[:100]
        amount = integer(request.form.get('amount_rial'), 'مبلغ چک', minimum=1)
        due = format_date(parse_date(request.form.get('due_date_jalali'),
                                     current_settings()['calendar']), 'jalali')
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            if party_id and not db.execute('SELECT 1 FROM parties WHERE id=?', (party_id,)).fetchone():
                raise ValueError('طرف حساب وجود ندارد.')
            if invoice_id:
                invoice = db.execute('SELECT party_id,kind FROM invoices WHERE id=?', (invoice_id,)).fetchone()
                expected = 'sale' if direction == 'received' else 'purchase'
                if not invoice or invoice['kind'] != expected:
                    raise ValueError('نوع فاکتور با جهت چک سازگار نیست.')
                if party_id and party_id != invoice['party_id']:
                    raise ValueError('طرف حساب چک و فاکتور یکسان نیست.')
                party_id = invoice['party_id']
            cur = db.execute('''INSERT INTO payment_checks(direction,party_id,invoice_id,check_number,
                bank_name,amount_rial,due_date_jalali,note,created_by) VALUES (?,?,?,?,?,?,?,?,?)''',
                (direction, party_id, invoice_id, number, bank, amount, due, note, g.user['id']))
            audit(db, 'check.create', 'payment_check', cur.lastrowid)
        flash('چک ثبت شد. تا هنگام وصول، به ماندهٔ نقد و بانک اضافه نمی‌شود.', 'success')
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc), 'error')
    return redirect(url_for('checks.list_checks'))


@checks.post('/checks/<int:check_id>/status')
@require_role('owner', 'manager')
def change_status(check_id):
    try:
        new_status = request.form.get('status', '')
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            row = db.execute('SELECT * FROM payment_checks WHERE id=?', (check_id,)).fetchone()
            if not row:
                raise ValueError('چک پیدا نشد.')
            if new_status not in _TRANSITIONS[row['status']]:
                raise ValueError('تغییر وضعیت چک مجاز نیست.')
            if new_status == 'cleared' and row['invoice_id']:
                invoice = db.execute('SELECT kind,total_rial FROM invoices WHERE id=?', (row['invoice_id'],)).fetchone()
                paid_cash = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM money_movements WHERE invoice_id=?',
                                       (row['invoice_id'],)).fetchone()[0]
                paid_checks = db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE invoice_id=? AND status='cleared'",
                                         (row['invoice_id'],)).fetchone()[0]
                if invoice['kind'] == 'sale':
                    returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM sales_returns WHERE sale_invoice_id=?',
                                          (row['invoice_id'],)).fetchone()[0]
                    refunded = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM sales_return_refunds r
                        JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.sale_invoice_id=?''',
                        (row['invoice_id'],)).fetchone()[0]
                else:
                    returned = db.execute('SELECT COALESCE(SUM(total_rial),0) FROM purchase_returns WHERE purchase_invoice_id=?',
                                          (row['invoice_id'],)).fetchone()[0]
                    refunded = db.execute('''SELECT COALESCE(SUM(r.amount_rial),0) FROM purchase_return_refunds r
                        JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.purchase_invoice_id=?''',
                        (row['invoice_id'],)).fetchone()[0]
                remaining = invoice['total_rial'] - returned - paid_cash - paid_checks + refunded
                if row['amount_rial'] > remaining:
                    raise ValueError('مبلغ چک از ماندهٔ قابل تسویهٔ فاکتور بیشتر است.')
            db.execute('''UPDATE payment_checks SET status=?,settled_at=CASE WHEN ?='cleared'
                THEN CURRENT_TIMESTAMP ELSE settled_at END WHERE id=?''', (new_status, new_status, check_id))
            db.execute('''INSERT INTO payment_check_events(check_id,old_status,new_status,note,created_by)
                VALUES (?,?,?,?,?)''', (check_id, row['status'], new_status, note, g.user['id']))
            audit(db, 'check.status', 'payment_check', check_id, {'from': row['status'], 'to': new_status})
        flash('وضعیت چک ثبت شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('checks.list_checks'))
