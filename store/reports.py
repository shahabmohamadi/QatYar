"""Dashboard, receivables/payables, and cash/bank journals."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .auth import require_login, require_role
from .db import get_db, transaction
from .pagination import paginate_query
from .opening_balances import summary as opening_summary
from .utils import audit, integer, required

reports = Blueprint('reports', __name__)


@reports.get('/')
@require_login
def dashboard():
    db = get_db()
    counts = db.execute('''SELECT
      (SELECT COUNT(*) FROM products WHERE is_active=1) products,
      (SELECT COUNT(*) FROM stock_balances sb JOIN products p ON p.id=sb.product_id
       WHERE sb.branch_id=1 AND p.is_active=1 AND sb.quantity<=p.min_stock) low_stock,
      (SELECT COALESCE(SUM(total_rial),0) FROM invoices
       WHERE kind='sale' AND date(created_at,'+3 hours','+30 minutes')=date('now','+3 hours','+30 minutes')) sales_today,
      (SELECT COALESCE(SUM(total_rial),0) FROM invoices
       WHERE kind='purchase' AND date(created_at,'+3 hours','+30 minutes')=date('now','+3 hours','+30 minutes')) purchases_today''').fetchone()
    recent, pager = paginate_query(db, '''SELECT i.*,p.name party_name FROM invoices i
      JOIN parties p ON p.id=i.party_id ORDER BY i.id DESC''')
    legacy_import = db.execute('SELECT source_created_at FROM legacy_imports ORDER BY id DESC LIMIT 1').fetchone()
    return render_template('dashboard.html', counts=counts, recent=recent, legacy_import=legacy_import, pager=pager)


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
    active_accounts = db.execute('SELECT id,name FROM financial_accounts WHERE is_active=1 ORDER BY kind,id').fetchall()
    return render_template('accounts.html', summary=summary, movements=movements,
                           opening=opening_summary(db), financial_accounts=financial_accounts,
                           active_accounts=active_accounts, movements_pager=movements_pager,
                           accounts_pager=accounts_pager)


@reports.get('/profit-loss')
@require_role('owner', 'manager')
def profit_loss():
    from datetime import date
    db = get_db()
    start = request.args.get('from', '').strip()
    end = request.args.get('to', '').strip()
    try:
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
        if start and end and start > end:
            raise ValueError()
    except ValueError:
        flash('بازه تاریخ نامعتبر است.', 'error')
        start = end = ''
    def total(table, expression):
        return db.execute(f'''SELECT COALESCE(SUM({expression}),0) FROM {table}
            WHERE (?='' OR date(created_at,'+3 hours','+30 minutes')>=?)
              AND (?='' OR date(created_at,'+3 hours','+30 minutes')<=?)''',
            (start, start, end, end)).fetchone()[0]
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
      (start, start, end, end)).fetchone()
    values['sold_cost'], values['provisional_lines'] = line
    values['net_sales'] = values['gross_sales'] - values['returns']
    values['cost_of_goods'] = values['sold_cost'] - values['returned_cost']
    values['gross_profit'] = values['net_sales'] - values['cost_of_goods']
    values['net_profit'] = values['gross_profit'] + values['purchase_return_variance'] + values['other_income'] - values['expenses']
    return render_template('profit_loss.html', values=values, start=start, end=end)


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
