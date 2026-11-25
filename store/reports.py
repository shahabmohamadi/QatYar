"""Dashboard, receivables/payables, and cash/bank journals."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .auth import require_login, require_role
from .calendar import TEHRAN, format_date, parse_date
from .db import get_db, transaction
from .invoices import UNIFIED_INVOICES_SQL
from .pagination import paginate_items, paginate_query
from .opening_balances import summary as opening_summary
from .utils import audit, integer, required

reports = Blueprint('reports', __name__)


def _chart_data(db, start, end, calendar):
    """Daily net sales/purchases and profit, grouped by week for long ranges."""
    values = defaultdict(lambda: {'sales': 0, 'purchases': 0, 'profit': 0})

    def add(sql, params, column, factor=1, profit_factor=0):
        for row in db.execute(sql, params):
            day = date.fromisoformat(row['day'])
            amount = row['amount'] * factor
            values[day][column] += amount
            values[day]['profit'] += row['amount'] * profit_factor

    date_sql = "date(created_at,'+3 hours','+30 minutes')"
    for kind, column, profit_factor in (('sale', 'sales', 1), ('purchase', 'purchases', 0)):
        add(f'''SELECT {date_sql} day,SUM(total_rial) amount FROM invoices
            WHERE kind=? AND {date_sql} BETWEEN ? AND ? GROUP BY day''',
            (kind, start.isoformat(), end.isoformat()), column, profit_factor=profit_factor)
    add(f'''SELECT {date_sql} day,SUM(total_rial) amount FROM sales_returns
        WHERE {date_sql} BETWEEN ? AND ? GROUP BY day''',
        (start.isoformat(), end.isoformat()), 'sales', -1, -1)
    add(f'''SELECT {date_sql} day,SUM(total_rial) amount FROM purchase_returns
        WHERE {date_sql} BETWEEN ? AND ? GROUP BY day''',
        (start.isoformat(), end.isoformat()), 'purchases', -1)
    for row in db.execute(f'''SELECT {date_sql} day,
        SUM(total_rial-stock_cost_rial) amount FROM purchase_returns
        WHERE {date_sql} BETWEEN ? AND ? GROUP BY day''', (start.isoformat(), end.isoformat())):
        if row['day']:
            values[date.fromisoformat(row['day'])]['profit'] += row['amount'] or 0
    add('''SELECT date(i.created_at,'+3 hours','+30 minutes') day,
        SUM(l.quantity*l.unit_cost_rial) amount FROM invoice_lines l
        JOIN invoices i ON i.id=l.invoice_id WHERE i.kind='sale'
        AND date(i.created_at,'+3 hours','+30 minutes') BETWEEN ? AND ? GROUP BY day''',
        (start.isoformat(), end.isoformat()), 'profit', 0, -1)
    add(f'''SELECT {date_sql} day,SUM(cost_total_rial) amount FROM sales_returns
        WHERE {date_sql} BETWEEN ? AND ? GROUP BY day''',
        (start.isoformat(), end.isoformat()), 'profit', 0, 1)
    for category, factor in (('income', 1), ('expense', -1)):
        add(f'''SELECT {date_sql} day,SUM(amount_rial) amount FROM money_movements
            WHERE category=? AND {date_sql} BETWEEN ? AND ? GROUP BY day''',
            (category, start.isoformat(), end.isoformat()), 'profit', 0, factor)

    imported = 0
    for row in db.execute('''SELECT date_jalali,title_code,total_rial FROM legacy_invoices
        WHERE title_code IN (1,2,3,4)'''):
        try:
            day = parse_date(row['date_jalali'], 'jalali')
        except ValueError:
            continue
        if not start <= day <= end:
            continue
        imported += 1
        column, factor = {1: ('sales', 1), 2: ('purchases', 1),
                          3: ('purchases', -1), 4: ('sales', -1)}[row['title_code']]
        values[day][column] += factor * row['total_rial']

    weekly = (end - start).days > 45
    buckets = defaultdict(lambda: {'sales': 0, 'purchases': 0, 'profit': 0})
    day = start
    while day <= end:
        key = start + timedelta(days=((day - start).days // 7) * 7) if weekly else day
        for name in ('sales', 'purchases', 'profit'):
            buckets[key][name] += values[day][name]
        day += timedelta(days=1)
    rows = [{'date': key, 'label': format_date(key, calendar), **buckets[key]}
            for key in sorted(buckets)]
    minimum = min((row[name] for row in rows for name in ('sales', 'purchases', 'profit')), default=0)
    maximum = max((row[name] for row in rows for name in ('sales', 'purchases', 'profit')), default=0)
    minimum, maximum = min(0, minimum), max(0, maximum)
    if minimum == maximum:
        minimum, maximum = -1, 1
    span = maximum - minimum
    def point(index, amount):
        x = 44 + index * 710 / max(1, len(rows) - 1)
        y = 20 + (maximum - amount) * 195 / span
        return f'{x:.1f},{y:.1f}'
    paths = {}
    markers = {}
    for name in ('sales', 'purchases', 'profit'):
        markers[name] = [{'xy': point(i, row[name]), 'label': row['label'], 'amount': row[name]}
                         for i, row in enumerate(rows)]
        if len(rows) == 1:
            y = markers[name][0]['xy'].split(',')[1]
            paths[name] = f'44.0,{y} 754.0,{y}'
        else:
            paths[name] = ' '.join(marker['xy'] for marker in markers[name])
    return {'rows': rows, 'paths': paths, 'zero_y': 20 + maximum * 195 / span,
            'markers': markers,
            'totals': {name: sum(row[name] for row in rows) for name in ('sales', 'purchases', 'profit')},
            'weekly': weekly, 'imported_count': imported,
            'first_label': rows[0]['label'], 'last_label': rows[-1]['label']}


@reports.get('/')
@require_login
def dashboard():
    db = get_db()
    calendar = db.execute('SELECT calendar FROM shop_settings WHERE id=1').fetchone()[0]
    today = datetime.now(TEHRAN).date()
    start_text = request.args.get('from', '').strip() or format_date(today - timedelta(days=29), calendar)
    end_text = request.args.get('to', '').strip() or format_date(today, calendar)
    try:
        start = parse_date(start_text, calendar)
        end = parse_date(end_text, calendar)
        if start > end or (end - start).days > 366:
            raise ValueError()
    except ValueError:
        flash('بازه نمودار باید معتبر و حداکثر یک سال باشد.', 'error')
        start, end = today - timedelta(days=29), today
        start_text, end_text = format_date(start, calendar), format_date(end, calendar)
    counts = db.execute('''SELECT
      (SELECT COUNT(*) FROM products WHERE is_active=1) products,
      (SELECT COUNT(*) FROM stock_balances sb JOIN products p ON p.id=sb.product_id
       WHERE sb.branch_id=1 AND p.is_active=1 AND sb.quantity<=p.min_stock) low_stock,
      (SELECT COALESCE(SUM(total_rial),0) FROM invoices
       WHERE kind='sale' AND date(created_at,'+3 hours','+30 minutes')=date('now','+3 hours','+30 minutes')) sales_today,
      (SELECT COALESCE(SUM(total_rial),0) FROM invoices
      WHERE kind='purchase' AND date(created_at,'+3 hours','+30 minutes')=date('now','+3 hours','+30 minutes')) purchases_today''').fetchone()
    counts = dict(counts)
    transferred_today = db.execute('''SELECT
        COALESCE(SUM(CASE title_code WHEN 1 THEN total_rial WHEN 4 THEN -total_rial ELSE 0 END),0),
        COALESCE(SUM(CASE title_code WHEN 2 THEN total_rial WHEN 3 THEN -total_rial ELSE 0 END),0)
        FROM legacy_invoices WHERE date_jalali=?''', (format_date(today, 'jalali'),)).fetchone()
    counts['sales_today'] += transferred_today[0]
    counts['purchases_today'] += transferred_today[1]
    recent, pager = paginate_query(db, UNIFIED_INVOICES_SQL, ('','','',''))
    chart = _chart_data(db, start, end, calendar)
    chart_rows, chart_pager = paginate_items(chart['rows'], param='page_chart')
    return render_template('dashboard.html', counts=counts, recent=recent, chart=chart,
                           chart_rows=chart_rows, chart_pager=chart_pager,
                           start_text=start_text, end_text=end_text, pager=pager)


@reports.get('/accounts')
@require_role('owner', 'manager')
def accounts():
    db = get_db()
    summary = db.execute('''SELECT
      (SELECT COALESCE(SUM(total_rial),0) FROM invoices WHERE kind='sale')
      - (SELECT COALESCE(SUM(total_rial),0) FROM sales_returns)
      - (SELECT COALESCE(SUM(amount_rial),0) FROM money_movements WHERE category='invoice' AND direction='in') receivable,
      (SELECT COALESCE(SUM(total_rial),0) FROM invoices WHERE kind='purchase')
      - (SELECT COALESCE(SUM(total_rial),0) FROM purchase_returns)
      - (SELECT COALESCE(SUM(amount_rial),0) FROM money_movements WHERE category='invoice' AND direction='out')
      - (SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE status='cleared' AND direction='issued') payable,
      (SELECT COALESCE(SUM(CASE WHEN direction='in' THEN amount_rial ELSE -amount_rial END),0)
       FROM money_movements WHERE method='cash')
      - (SELECT COALESCE(SUM(amount_rial),0) FROM sales_return_refunds WHERE method='cash') cash_balance,
      (SELECT COALESCE(SUM(CASE WHEN direction='in' THEN amount_rial ELSE -amount_rial END),0)
       FROM money_movements WHERE method='bank')
      - (SELECT COALESCE(SUM(amount_rial),0) FROM sales_return_refunds WHERE method='bank')
      + (SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE status='cleared' AND direction='received')
      - (SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE status='cleared' AND direction='issued') bank_balance''').fetchone()
    check_received = db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM payment_checks WHERE status='cleared' AND direction='received' AND invoice_id IS NOT NULL").fetchone()[0]
    refund_total = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM sales_return_refunds').fetchone()[0]
    summary = dict(summary)
    summary['receivable'] = summary['receivable'] - check_received + refund_total
    supplier_refunds = db.execute('SELECT COALESCE(SUM(amount_rial),0) FROM purchase_return_refunds').fetchone()[0]
    summary['payable'] += supplier_refunds
    summary['cash_balance'] += db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM purchase_return_refunds WHERE method='cash'").fetchone()[0]
    summary['bank_balance'] += db.execute("SELECT COALESCE(SUM(amount_rial),0) FROM purchase_return_refunds WHERE method='bank'").fetchone()[0]
    opening = opening_summary(db)
    summary['receivable'] += opening['receivable_rial']
    summary['payable'] += opening['payable_rial']
    summary['cash_balance'] += opening['cash_rial']
    summary['bank_balance'] += opening['bank_rial']
    movements, movements_pager = paginate_query(db, '''SELECT m.*,p.name party_name,i.number invoice_number,u.display_name,
      a.name account_name
      FROM money_movements m LEFT JOIN parties p ON p.id=m.party_id
      LEFT JOIN invoices i ON i.id=m.invoice_id JOIN users u ON u.id=m.created_by
      LEFT JOIN financial_accounts a ON a.id=m.account_id
      ORDER BY m.id DESC''', param='page_movements')
    financial_accounts, accounts_pager = paginate_query(db, '''SELECT a.id,a.kind,a.name,a.is_active,
      COALESCE(SUM(CASE WHEN m.direction='in' THEN m.amount_rial ELSE -m.amount_rial END),0) balance_rial
      FROM financial_accounts a LEFT JOIN money_movements m ON m.account_id=a.id
      GROUP BY a.id ORDER BY a.kind,a.id''', param='page_accounts')
    financial_accounts = [dict(account) for account in financial_accounts]
    for account in financial_accounts:
        if account['id'] == 1:
            account['balance_rial'] += opening['cash_rial']
        elif account['id'] == 2:
            account['balance_rial'] += opening['bank_rial']
    active_accounts = db.execute('SELECT id,name FROM financial_accounts WHERE is_active=1 ORDER BY kind,id').fetchall()
    return render_template('accounts.html', summary=summary, movements=movements,
                           opening=opening, financial_accounts=financial_accounts,
                           active_accounts=active_accounts, movements_pager=movements_pager,
                           accounts_pager=accounts_pager)


@reports.get('/profit-loss')
@require_role('owner', 'manager')
def profit_loss():
    db = get_db()
    start = request.args.get('from', '').strip()
    end = request.args.get('to', '').strip()
    try:
        calendar = db.execute('SELECT calendar FROM shop_settings WHERE id=1').fetchone()[0]
        start_iso = parse_date(start, calendar).isoformat() if start else ''
        end_iso = parse_date(end, calendar).isoformat() if end else ''
        if start_iso and end_iso and start_iso > end_iso:
            raise ValueError()
    except ValueError:
        flash('بازه تاریخ نامعتبر است.', 'error')
        start = end = ''
        start_iso = end_iso = ''
    def total(table, expression):
        return db.execute(f'''SELECT COALESCE(SUM({expression}),0) FROM {table}
            WHERE (?='' OR date(created_at,'+3 hours','+30 minutes')>=?)
              AND (?='' OR date(created_at,'+3 hours','+30 minutes')<=?)''',
            (start_iso, start_iso, end_iso, end_iso)).fetchone()[0]
    values = {
        'gross_sales': total('invoices', "CASE WHEN kind='sale' THEN total_rial ELSE 0 END"),
        'returns': total('sales_returns', 'total_rial'),
        'returned_cost': total('sales_returns', 'cost_total_rial'),
        'purchase_return_variance': total('purchase_returns', 'total_rial-stock_cost_rial'),
        'other_income': total('money_movements', "CASE WHEN category='income' THEN amount_rial ELSE 0 END"),
        'expenses': total('money_movements', "CASE WHEN category='expense' THEN amount_rial ELSE 0 END"),
    }
    line = db.execute('''SELECT COALESCE(SUM(l.quantity*l.unit_cost_rial),0),
      COALESCE(SUM(l.cost_provisional),0) FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id
      WHERE i.kind='sale' AND (?='' OR date(i.created_at,'+3 hours','+30 minutes')>=?)
      AND (?='' OR date(i.created_at,'+3 hours','+30 minutes')<=?)''',
      (start_iso, start_iso, end_iso, end_iso)).fetchone()
    values['sold_cost'], values['provisional_lines'] = line
    values['net_sales'] = values['gross_sales'] - values['returns']
    values['cost_of_goods'] = values['sold_cost'] - values['returned_cost']
    values['gross_profit'] = values['net_sales'] - values['cost_of_goods']
    values['net_profit'] = values['gross_profit'] + values['purchase_return_variance'] + values['other_income'] - values['expenses']
    imported_count = db.execute('SELECT COUNT(*) FROM legacy_invoices').fetchone()[0]
    return render_template('profit_loss.html', values=values, start=start, end=end,
                           imported_count=imported_count)


@reports.post('/accounts/entries')
@require_role('owner', 'manager')
def manual_entry():
    try:
        category = request.form.get('category', '')
        account_id = integer(request.form.get('account_id'), 'حساب', minimum=1)
        if category not in ('income', 'expense'):
            raise ValueError('نوع گردش نامعتبر است.')
        amount = integer(request.form.get('amount_rial'), 'مبلغ', minimum=1)
        note = required(request.form.get('note'), 'شرح گردش', 240)
        direction = 'in' if category == 'income' else 'out'
        with transaction(get_db()) as db:
            account = db.execute('SELECT kind FROM financial_accounts WHERE id=? AND is_active=1',
                                 (account_id,)).fetchone()
            if not account:
                raise ValueError('حساب انتخابی فعال نیست.')
            cur = db.execute('''INSERT INTO money_movements(branch_id,direction,method,category,amount_rial,note,created_by,account_id)
                VALUES (1,?,?,?,?,?,?,?)''', (direction, account['kind'], category, amount, note, g.user['id'], account_id))
            audit(db, 'money.manual', 'money_movement', cur.lastrowid, {'amount_rial': amount, 'category': category})
        flash('گردش مالی ثبت شد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('reports.accounts'))
