"""Read-only views of imported Parsian fiscal years and their exact rial amounts."""
import json
from flask import Blueprint, abort, render_template, request

from .auth import require_role
from .db import get_db
from .pagination import paginate_items, paginate_query

legacy = Blueprint('legacy', __name__)
KIND_LABELS = {
    1: 'فروش', 2: 'خرید', 3: 'برگشت از خرید', 4: 'برگشت از فروش',
    5: 'موجودی اول دوره', 6: 'اضافات', 7: 'کسری انبار',
    8: 'امانات پرداختنی', 9: 'امانات دریافتی', 10: 'انتقال انبار',
    11: 'پیش‌فاکتور', 12: 'تولید', 13: 'مصرف', 14: 'حواله انبار', 15: 'رسید انبار',
}


def year_arg():
    year = request.args.get('year', 'db_1405')
    return year if year in ('db_1404', 'db_1405') else 'db_1405'


@legacy.get('/legacy')
@require_role('owner', 'manager')
def overview():
    db = get_db()
    latest = db.execute('SELECT * FROM legacy_imports ORDER BY id DESC LIMIT 1').fetchone()
    if not latest:
        return render_template('legacy.html', latest=None)
    counts = db.execute('''SELECT source_db,
      (SELECT COUNT(*) FROM legacy_invoices i WHERE i.source_db=r.source_db) invoices,
      (SELECT COUNT(*) FROM legacy_journal j WHERE j.source_db=r.source_db) journal,
      (SELECT COUNT(*) FROM legacy_checks c WHERE c.source_db=r.source_db) checks,
      (SELECT COUNT(*) FROM legacy_stock s WHERE s.source_db=r.source_db AND s.quantity<0) negative_stock
      FROM legacy_records r GROUP BY source_db ORDER BY source_db''').fetchall()
    warnings = json.loads(latest['warning_json'])
    counts_page, counts_pager = paginate_items(counts, param='page_years')
    warnings_page, warnings_pager = paginate_items(list(warnings.items()), param='page_warnings')
    return render_template('legacy.html', latest=latest, counts=counts_page,
                           warnings=warnings_page, counts_pager=counts_pager,
                           warnings_pager=warnings_pager)


@legacy.get('/legacy/invoices')
@require_role('owner', 'manager')
def invoices():
    db = get_db()
    year = year_arg()
    kind = request.args.get('kind', '')
    if kind and (not kind.isdigit() or int(kind) not in KIND_LABELS):
        kind = ''
    q = request.args.get('q', '').strip()[:80]
    rows, pager = paginate_query(db, '''SELECT * FROM legacy_invoices
      WHERE source_db=? AND (?='' OR title_code=?)
      AND (?='' OR CAST(factor_num AS TEXT) LIKE ? OR party_name LIKE ?)
      ORDER BY date_jalali DESC,id DESC''',
      (year, kind, int(kind or 0), q, '%' + q + '%', '%' + q + '%'))
    return render_template('legacy_invoices.html', rows=rows, year=year, kind=kind, q=q, kinds=KIND_LABELS, pager=pager)


@legacy.get('/legacy/invoices/<int:invoice_id>')
@require_role('owner', 'manager')
def invoice_detail(invoice_id):
    db = get_db()
    row = db.execute('SELECT * FROM legacy_invoices WHERE id=?', (invoice_id,)).fetchone()
    if not row:
        abort(404)
    lines, pager = paginate_query(db, 'SELECT * FROM legacy_invoice_lines WHERE legacy_invoice_id=? ORDER BY id',
                                  (invoice_id,))
    return render_template('legacy_invoice_detail.html', invoice=row, lines=lines, kinds=KIND_LABELS, pager=pager)


@legacy.get('/legacy/stock')
@require_role('owner', 'manager')
def stock():
    db = get_db()
    year = year_arg()
    q = request.args.get('q', '').strip()[:80]
    only_negative = request.args.get('negative') == '1'
    rows, pager = paginate_query(db, '''SELECT s.*,COALESCE(p.name,'') product_name,p.category,p.min_stock,p.cost_confirmed
      FROM legacy_stock s LEFT JOIN products p ON p.sku=s.product_code
      WHERE s.source_db=? AND (?=0 OR s.quantity<0)
      AND (?='' OR s.product_code LIKE ? OR p.name LIKE ?)
      ORDER BY CASE WHEN s.quantity<0 THEN 0 ELSE 1 END,s.quantity,p.name''',
      (year, int(only_negative), q, '%' + q + '%', '%' + q + '%'))
    summary = db.execute('''SELECT COUNT(*) products,SUM(quantity) total_units,
      SUM(CASE WHEN quantity<0 THEN 1 ELSE 0 END) negative_products
      FROM legacy_stock WHERE source_db=?''', (year,)).fetchone()
    return render_template('legacy_stock.html', rows=rows, summary=summary, year=year,
                           q=q, only_negative=only_negative, pager=pager)


@legacy.get('/legacy/checks')
@require_role('owner', 'manager')
def checks():
    year = year_arg()
    rows, pager = paginate_query(get_db(), '''SELECT * FROM legacy_checks WHERE source_db=?
      ORDER BY due_date_jalali DESC,id DESC''', (year,))
    return render_template('legacy_checks.html', rows=rows, year=year, pager=pager)


@legacy.get('/legacy/accounts')
@require_role('owner', 'manager')
def accounts():
    db = get_db()
    year = year_arg()
    groups = db.execute('''SELECT j.kol_code,SUM(j.debit_rial) debit_rial,SUM(j.credit_rial) credit_rial,
      (SELECT json_extract(r.payload_json,'$.CName') FROM legacy_records r
       WHERE r.source_db=j.source_db AND r.table_name='tbl_Kol'
       AND CAST(json_extract(r.payload_json,'$.Code') AS INTEGER)=j.kol_code LIMIT 1) account_name
      FROM legacy_journal j WHERE j.source_db=? GROUP BY j.kol_code ORDER BY j.kol_code''', (year,)).fetchall()
    balances = {row['kol_code']: row for row in groups}
    def credit(code):
        row = balances.get(code)
        return row['credit_rial'] - row['debit_rial'] if row else 0
    def debit(code):
        return -credit(code)
    # Parsian's actual chart on this installation uses 60/61 for sales,
    # 62/63 for purchases, and 50 for expenses. Purchases are not COGS:
    # without verified opening/closing inventory, this is a trading
    # difference, not a defensible net-profit figure.
    sales = credit(60)
    sales_returns = debit(61)
    purchases = debit(62)
    purchase_returns = credit(63)
    expenses = debit(50)
    trading_difference = sales - sales_returns - purchases + purchase_returns - expenses
    totals = {'sales': sales, 'sales_returns': sales_returns,
              'purchases': purchases, 'purchase_returns': purchase_returns,
              'expenses': expenses, 'trading_difference': trading_difference,
              'debit': sum(row['debit_rial'] for row in groups),
              'credit': sum(row['credit_rial'] for row in groups)}
    groups_page, groups_pager = paginate_items(groups, param='page_groups')
    recent, recent_pager = paginate_query(db, '''SELECT * FROM legacy_journal WHERE source_db=?
      ORDER BY date_jalali DESC,document_num DESC,id DESC''', (year,), param='page_journal')
    return render_template('legacy_accounts.html', groups=groups_page, totals=totals, recent=recent,
                           year=year, groups_pager=groups_pager, recent_pager=recent_pager)
