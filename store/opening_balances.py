"""Exact Rial opening balances from Parsian, separate from new invoices."""
import sqlite3

from flask import Blueprint, flash, g, redirect, request, url_for

from .auth import require_role
from .db import connect, get_db, migrate, transaction
from .utils import audit, integer

opening_balances = Blueprint('opening_balances', __name__)


def populate(db, import_id):
    """Populate all parties and cash/bank from the immutable 1405 journal."""
    if db.execute('SELECT 1 FROM opening_party_balances LIMIT 1').fetchone():
        raise ValueError('مانده‌های انتقالی قبلاً ثبت شده‌اند.')
    mappings = db.execute("SELECT party_id,kol_code,moeen_code,tafsili_code FROM legacy_party_map WHERE source_db='db_1405'").fetchall()
    for row in mappings:
        amount = db.execute('''SELECT COALESCE(SUM(debit_rial-credit_rial),0) FROM legacy_journal
            WHERE import_id=? AND source_db='db_1405' AND kol_code=? AND moeen_code=? AND tafsili_code=?''',
            (import_id, row['kol_code'], row['moeen_code'], row['tafsili_code'])).fetchone()[0]
        db.execute('INSERT INTO opening_party_balances(party_id,import_id,amount_rial) VALUES (?,?,?)',
                   (row['party_id'], import_id, amount))
    for account_code in (10, 11):
        amount = db.execute('''SELECT COALESCE(SUM(debit_rial-credit_rial),0) FROM legacy_journal
            WHERE import_id=? AND source_db='db_1405' AND kol_code=?''', (import_id, account_code)).fetchone()[0]
        db.execute('INSERT INTO opening_account_balances(account_code,import_id,amount_rial) VALUES (?,?,?)',
                   (account_code, import_id, amount))
    return len(mappings)


def activate_existing():
    migrate()
    db = connect()
    try:
        with transaction(db):
            if db.execute('SELECT 1 FROM invoices LIMIT 1').fetchone() or db.execute('SELECT 1 FROM money_movements LIMIT 1').fetchone():
                raise ValueError('ماندهٔ انتقالی پس از شروع عملیات جدید باید با حسابدار تطبیق داده شود.')
            imported = db.execute('SELECT id FROM legacy_imports ORDER BY id DESC LIMIT 1').fetchone()
            if not imported:
                raise ValueError('آرشیو پارسیان وارد نشده است.')
            count = populate(db, imported['id'])
            source_total = db.execute('''SELECT COALESCE(SUM(debit_rial-credit_rial),0) FROM legacy_journal
                WHERE import_id=? AND source_db='db_1405' AND kol_code=12 AND moeen_code=2
                AND tafsili_code<>0''', (imported['id'],)).fetchone()[0]
            target_total = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM opening_party_balances').fetchone()[0]
            if source_total != target_total:
                raise ValueError('ماندهٔ طرف‌حساب‌ها با دفتر پارسیان یکسان نیست.')
            owner = db.execute("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
            db.execute('''INSERT INTO audit_log(actor_user_id,action,entity_type,detail)
                VALUES (?,?,?,?)''', (owner['id'] if owner else None,
                                     'parsian.opening.activate', 'opening_balances',
                                     '{"source_db":"db_1405"}'))
        return {'parties': count, 'net_party_rial': target_total}
    finally:
        db.close()


def remaining_rial(db, party_id):
    row = db.execute('SELECT amount_rial FROM opening_party_balances WHERE party_id=?', (party_id,)).fetchone()
    if not row:
        return None
    settled = db.execute('''SELECT COALESCE(SUM(CASE WHEN direction='in' THEN amount_rial ELSE -amount_rial END),0)
        FROM opening_settlements WHERE party_id=?''', (party_id,)).fetchone()[0]
    return row['amount_rial'] - settled


def summary(db):
    party = db.execute('''SELECT COALESCE(SUM(CASE WHEN remaining>0 THEN remaining ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN remaining<0 THEN -remaining ELSE 0 END),0) FROM (
        SELECT b.amount_rial-COALESCE(SUM(CASE WHEN s.direction='in' THEN s.amount_rial ELSE -s.amount_rial END),0) remaining
        FROM opening_party_balances b LEFT JOIN opening_settlements s ON s.party_id=b.party_id GROUP BY b.party_id)''').fetchone()
    balances = {row['account_code']: row['amount_rial'] for row in db.execute('SELECT * FROM opening_account_balances')}
    cash_flow, bank_flow = db.execute('''SELECT
        COALESCE(SUM(CASE WHEN method='cash' THEN CASE WHEN direction='in' THEN amount_rial ELSE -amount_rial END ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN method='bank' THEN CASE WHEN direction='in' THEN amount_rial ELSE -amount_rial END ELSE 0 END),0)
        FROM opening_settlements''').fetchone()
    return {'receivable_rial': party[0], 'payable_rial': party[1],
            'cash_rial': balances.get(10, 0) + cash_flow,
            'bank_rial': balances.get(11, 0) + bank_flow,
            'party_count': db.execute('SELECT COUNT(*) FROM opening_party_balances').fetchone()[0]}


@opening_balances.post('/parties/<int:party_id>/opening-settlements')
@require_role('owner', 'manager')
def settle(party_id):
    try:
        amount = integer(request.form.get('amount_rial'), 'مبلغ تسویه', minimum=1, maximum=10**15)
        method = request.form.get('method', '')
        if method not in ('cash', 'bank'):
            raise ValueError('روش دریافت یا پرداخت نامعتبر است.')
        note = request.form.get('note', '').strip()[:240]
        with transaction(get_db()) as db:
            remaining = remaining_rial(db, party_id)
            if remaining is None or remaining == 0:
                raise ValueError('ماندهٔ قابل تسویهٔ پارسیان وجود ندارد.')
            if amount > abs(remaining):
                raise ValueError('مبلغ از ماندهٔ پارسیان بیشتر است.')
            direction = 'in' if remaining > 0 else 'out'
            cur = db.execute('''INSERT INTO opening_settlements(party_id,direction,method,amount_rial,note,created_by)
                VALUES (?,?,?,?,?,?)''', (party_id, direction, method, amount, note, g.user['id']))
            audit(db, 'parsian.opening.settle', 'opening_settlement', cur.lastrowid,
                  {'party_id': party_id, 'amount_rial': amount, 'direction': direction})
        flash('تسویهٔ ماندهٔ پارسیان ثبت شد.', 'success')
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc), 'error')
    return redirect(url_for('parties.party_detail', party_id=party_id))
