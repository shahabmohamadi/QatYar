"""Customers and suppliers, with separate receivable and payable balances."""
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .auth import require_login, require_role
from .db import get_db, transaction
from .pagination import paginate_query
from .opening_balances import remaining_rial
from .utils import audit, required

parties = Blueprint('parties', __name__)


@parties.get('/parties')
@require_login
def list_parties():
    db = get_db()
    rows, pager = paginate_query(db, '''SELECT p.*,
      COALESCE((SELECT SUM(total_rial) FROM invoices WHERE party_id=p.id AND kind='sale'),0)
      - COALESCE((SELECT SUM(total_rial) FROM sales_returns WHERE party_id=p.id),0)
      - COALESCE((SELECT SUM(amount_rial) FROM money_movements WHERE party_id=p.id AND category='invoice' AND direction='in'),0)
      - COALESCE((SELECT SUM(amount_rial) FROM payment_checks WHERE party_id=p.id AND direction='received' AND status='cleared' AND invoice_id IS NOT NULL),0)
      + COALESCE((SELECT SUM(r.amount_rial) FROM sales_return_refunds r JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.party_id=p.id),0) receivable,
      COALESCE((SELECT SUM(total_rial) FROM invoices WHERE party_id=p.id AND kind='purchase'),0)
      - COALESCE((SELECT SUM(total_rial) FROM purchase_returns WHERE party_id=p.id),0)
      - COALESCE((SELECT SUM(amount_rial) FROM money_movements WHERE party_id=p.id AND category='invoice' AND direction='out'),0)
      - COALESCE((SELECT SUM(amount_rial) FROM payment_checks WHERE party_id=p.id AND direction='issued' AND status='cleared' AND invoice_id IS NOT NULL),0)
      + COALESCE((SELECT SUM(r.amount_rial) FROM purchase_return_refunds r JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.party_id=p.id),0) payable
      FROM parties p WHERE p.is_active=1 ORDER BY p.name''')
    opening = {row['party_id']: row['remaining'] for row in db.execute('''SELECT b.party_id,
        b.amount_rial-COALESCE(SUM(CASE WHEN s.direction='in' THEN s.amount_rial ELSE -s.amount_rial END),0) remaining
        FROM opening_party_balances b LEFT JOIN opening_settlements s ON s.party_id=b.party_id
        WHERE b.party_id IN (''' + ','.join('?' for _ in rows) + ''') GROUP BY b.party_id''',
        [row['id'] for row in rows])} if rows else {}
    parties = [{**dict(row), 'opening_rial': opening.get(row['id'])} for row in rows]
    return render_template('parties.html', parties=parties, pager=pager)


@parties.post('/parties')
@require_role('owner', 'manager', 'clerk')
def create_party():
    try:
        name = required(request.form.get('name'), 'نام طرف حساب')
        kind = request.form.get('kind', '')
        if kind not in ('customer', 'supplier', 'both'):
            raise ValueError('نوع طرف حساب نامعتبر است.')
        phone = request.form.get('phone', '').strip()[:30]
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            cur = db.execute('INSERT INTO parties(name,kind,phone,note) VALUES (?,?,?,?)',
                             (name, kind, phone, note))
            audit(db, 'party.create', 'party', cur.lastrowid)
        flash('طرف حساب ثبت شد.', 'success')
        return redirect(url_for('parties.party_detail', party_id=cur.lastrowid))
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc), 'error')
        return redirect(url_for('parties.list_parties'))


@parties.get('/parties/<int:party_id>')
@require_login
def party_detail(party_id):
    db = get_db()
    party = db.execute('SELECT * FROM parties WHERE id=?', (party_id,)).fetchone()
    if not party:
        abort(404)
    invoices, invoices_pager = paginate_query(db, '''SELECT i.*,
      COALESCE((SELECT SUM(amount_rial) FROM money_movements m WHERE m.invoice_id=i.id AND m.category='invoice'),0)
      + COALESCE((SELECT SUM(amount_rial) FROM payment_checks c WHERE c.invoice_id=i.id AND c.status='cleared'),0) paid_rial,
      i.total_rial
      - CASE WHEN i.kind='sale' THEN COALESCE((SELECT SUM(total_rial) FROM sales_returns sr WHERE sr.sale_invoice_id=i.id),0)
        ELSE COALESCE((SELECT SUM(total_rial) FROM purchase_returns pr WHERE pr.purchase_invoice_id=i.id),0) END
      - COALESCE((SELECT SUM(amount_rial) FROM money_movements m WHERE m.invoice_id=i.id AND m.category='invoice'),0)
      - COALESCE((SELECT SUM(amount_rial) FROM payment_checks c WHERE c.invoice_id=i.id AND c.status='cleared'),0)
      + CASE WHEN i.kind='sale' THEN COALESCE((SELECT SUM(r.amount_rial) FROM sales_return_refunds r
        JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.sale_invoice_id=i.id),0)
        ELSE COALESCE((SELECT SUM(r.amount_rial) FROM purchase_return_refunds r
        JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.purchase_invoice_id=i.id),0) END balance_rial
      FROM invoices i WHERE i.party_id=? ORDER BY i.id DESC''', (party_id,), param='page_invoices')
    movements, movements_pager = paginate_query(db,
        'SELECT * FROM money_movements WHERE party_id=? ORDER BY id DESC',
        (party_id,), param='page_movements')
    balances = db.execute('''SELECT
      COALESCE((SELECT SUM(total_rial) FROM invoices WHERE party_id=? AND kind='sale'),0)
      - COALESCE((SELECT SUM(total_rial) FROM sales_returns WHERE party_id=?),0)
      - COALESCE((SELECT SUM(amount_rial) FROM money_movements WHERE party_id=? AND category='invoice' AND direction='in'),0)
      - COALESCE((SELECT SUM(amount_rial) FROM payment_checks WHERE party_id=? AND direction='received' AND status='cleared' AND invoice_id IS NOT NULL),0)
      + COALESCE((SELECT SUM(r.amount_rial) FROM sales_return_refunds r JOIN sales_returns sr ON sr.id=r.return_id WHERE sr.party_id=?),0) receivable,
      COALESCE((SELECT SUM(total_rial) FROM invoices WHERE party_id=? AND kind='purchase'),0)
      - COALESCE((SELECT SUM(total_rial) FROM purchase_returns WHERE party_id=?),0)
      - COALESCE((SELECT SUM(amount_rial) FROM money_movements WHERE party_id=? AND category='invoice' AND direction='out'),0)
      - COALESCE((SELECT SUM(amount_rial) FROM payment_checks WHERE party_id=? AND direction='issued' AND status='cleared' AND invoice_id IS NOT NULL),0)
      + COALESCE((SELECT SUM(r.amount_rial) FROM purchase_return_refunds r JOIN purchase_returns pr ON pr.id=r.return_id WHERE pr.party_id=?),0) payable''',
      (party_id, party_id, party_id, party_id, party_id, party_id, party_id, party_id, party_id, party_id)).fetchone()
    opening_rial = remaining_rial(db, party_id)
    opening_settlements, settlements_pager = paginate_query(db, '''SELECT * FROM opening_settlements
        WHERE party_id=? ORDER BY id DESC''', (party_id,), param='page_settlements')
    return render_template('party_detail.html', party=party, invoices=invoices, movements=movements,
                           receivable=balances['receivable'], payable=balances['payable'],
                           opening_rial=opening_rial, opening_settlements=opening_settlements,
                           invoices_pager=invoices_pager, movements_pager=movements_pager,
                           settlements_pager=settlements_pager)
