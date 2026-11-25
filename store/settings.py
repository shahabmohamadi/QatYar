"""Owner-controlled settings that are applied by the working modules."""
from io import BytesIO
from pathlib import Path, PureWindowsPath
import os
import re
import secrets
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for

from .auth import require_role
from .db import data_dir, db_path, get_db, transaction
from .utils import audit, integer, required

settings_bp = Blueprint('settings', __name__)


def current_settings(db=None):
    connection = db or get_db()
    row = connection.execute('''SELECT id,app_name,app_tagline,shop_name,shop_phone,shop_address,
      default_min_stock,default_price_type_id,sale_prefix,purchase_prefix,invoice_digits,
      default_payment_method,cash_label,bank_label,session_hours,login_max_attempts,
      login_lock_minutes,backup_enabled,backup_retention_days,backup_directory,
      logo_revision,updated_at FROM shop_settings WHERE id=1''').fetchone()
    if not row:
        raise RuntimeError('تنظیمات برنامه پیدا نشد.')
    return row


def settings_for_request():
    if 'shop_settings' not in g:
        g.shop_settings = current_settings()
    return g.shop_settings


@settings_bp.get('/settings')
@require_role('owner')
def page():
    db = get_db()
    tab = request.args.get('tab', 'identity')
    if tab not in ('identity', 'inventory', 'sales', 'payments', 'backup', 'security', 'system', 'updates'):
        tab = 'identity'
    tiers = db.execute('SELECT * FROM price_types ORDER BY id').fetchall()
    accounts = db.execute('SELECT * FROM financial_accounts ORDER BY kind,id').fetchall()
    branch = db.execute('SELECT id,name FROM branches WHERE id=1').fetchone()
    runs = db.execute('SELECT * FROM backup_runs ORDER BY id DESC LIMIT 5').fetchall()
    users = db.execute('SELECT id,username,display_name,role,is_active FROM users ORDER BY id').fetchall()
    version = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
    update_info = db.execute('SELECT * FROM update_settings WHERE id=1').fetchone()
    from .updates import version as app_version, version_key
    update_status = data_dir() / 'update-status.txt'
    return render_template('settings.html', cfg=settings_for_request(), tiers=tiers,
                           accounts=accounts, active_tab=tab, update_info=update_info,
                           app_version=app_version(), version_key=version_key,
                           update_status=update_status.read_text(encoding='utf-8') if update_status.exists() else '',
                           branch=branch, runs=runs, users=users,
                           backup_default=str(data_dir() / 'backups'),
                           database_path=str(db_path()), schema_version=version,
                           web_address=request.host_url.rstrip('/'))


@settings_bp.post('/settings/<section>')
@require_role('owner')
def update(section):
    try:
        if section == 'identity':
            values = (
                required(request.form.get('app_name'), 'نام برنامه', 80),
                required(request.form.get('app_tagline'), 'شعار برنامه', 160),
                required(request.form.get('shop_name'), 'نام فروشگاه', 120),
                request.form.get('shop_phone', '').strip()[:30],
                request.form.get('shop_address', '').strip()[:300],
            )
            query = '''UPDATE shop_settings SET app_name=?,app_tagline=?,shop_name=?,shop_phone=?,shop_address=?,
                updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1'''
        elif section == 'sales':
            tier_id = integer(request.form.get('default_price_type_id'), 'نوع قیمت پیش‌فرض', minimum=1)
            tier = get_db().execute('SELECT 1 FROM price_types WHERE id=? AND is_active=1', (tier_id,)).fetchone()
            if not tier:
                raise ValueError('نوع قیمت پیش‌فرض باید فعال باشد.')
            sale_prefix = required(request.form.get('sale_prefix'), 'پیشوند فاکتور فروش', 8).upper()
            purchase_prefix = required(request.form.get('purchase_prefix'), 'پیشوند فاکتور خرید', 8).upper()
            if not all(re.fullmatch(r'[A-Z0-9_-]{1,8}', prefix) for prefix in (sale_prefix, purchase_prefix)):
                raise ValueError('پیشوند فاکتور فقط می‌تواند حروف انگلیسی، عدد، خط تیره یا زیرخط داشته باشد.')
            if sale_prefix == purchase_prefix:
                raise ValueError('پیشوند خرید و فروش باید متفاوت باشد.')
            digits = integer(request.form.get('invoice_digits'), 'تعداد رقم فاکتور', minimum=4, maximum=12)
            method = request.form.get('default_payment_method', '')
            if method not in ('cash', 'bank'):
                raise ValueError('روش پیش‌فرض پرداخت نامعتبر است.')
            values = (tier_id, sale_prefix, purchase_prefix, digits, method)
            query = '''UPDATE shop_settings SET default_price_type_id=?,sale_prefix=?,purchase_prefix=?,
                invoice_digits=?,default_payment_method=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1'''
        elif section == 'inventory':
            minimum = integer(request.form.get('default_min_stock'), 'حداقل موجودی پیش‌فرض', maximum=10**8)
            branch_name = required(request.form.get('branch_name'), 'نام شعبه', 120)
            with transaction(get_db()) as db:
                db.execute('UPDATE shop_settings SET default_min_stock=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1',
                           (minimum, g.user['id']))
                db.execute('UPDATE branches SET name=? WHERE id=1', (branch_name,))
                audit(db, 'settings.inventory', 'shop_settings', 1, {'default_min_stock': minimum})
            flash('تنظیمات موجودی ذخیره شد.', 'success')
            return redirect(url_for('settings.page', tab='inventory'))
        elif section == 'payments':
            cash_label = required(request.form.get('cash_label'), 'نام صندوق نقدی', 40)
            bank_label = required(request.form.get('bank_label'), 'نام حساب بانکی', 40)
            if cash_label == bank_label:
                raise ValueError('نام دو روش پرداخت باید متفاوت باشد.')
            values = (cash_label, bank_label)
            query = '''UPDATE shop_settings SET cash_label=?,bank_label=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1'''
        elif section == 'security':
            values = (
                integer(request.form.get('session_hours'), 'مدت نشست', minimum=1, maximum=72),
                integer(request.form.get('login_max_attempts'), 'تعداد تلاش ورود', minimum=3, maximum=10),
                integer(request.form.get('login_lock_minutes'), 'مدت محدودیت ورود', minimum=1, maximum=60),
            )
            query = '''UPDATE shop_settings SET session_hours=?,login_max_attempts=?,login_lock_minutes=?,
                updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1'''
        elif section == 'backup':
            enabled = 1 if request.form.get('backup_enabled') == 'on' else 0
            retention = integer(request.form.get('backup_retention_days'), 'مدت نگهداری بکاپ', minimum=1, maximum=365)
            directory = request.form.get('backup_directory', '').strip()
            if len(directory) > 260:
                raise ValueError('مسیر بکاپ بیش از حد بلند است.')
            if directory:
                path = PureWindowsPath(directory) if os.name == 'nt' else Path(directory).expanduser()
                if not path.is_absolute():
                    raise ValueError('مسیر بکاپ باید کامل باشد؛ یا آن را خالی بگذارید.')
            values = (enabled, retention, directory)
            query = '''UPDATE shop_settings SET backup_enabled=?,backup_retention_days=?,backup_directory=?,
                updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1'''
        else:
            abort(404)
        with transaction(get_db()) as db:
            db.execute(query, (*values, g.user['id']))
            audit(db, 'settings.' + section, 'shop_settings', 1)
        flash('تنظیمات ذخیره شد.', 'success')
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc), 'error')
    return redirect(url_for('settings.page', tab=section))


@settings_bp.post('/settings/accounts')
@require_role('owner')
def add_account():
    try:
        kind = request.form.get('kind', '')
        if kind not in ('cash', 'bank'):
            raise ValueError('نوع حساب نامعتبر است.')
        name = required(request.form.get('name'), 'نام حساب', 80)
        with transaction(get_db()) as db:
            cur = db.execute('INSERT INTO financial_accounts(kind,name) VALUES (?,?)', (kind, name))
            audit(db, 'account.create', 'financial_account', cur.lastrowid)
        flash('حساب اضافه شد.', 'success')
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc), 'error')
    return redirect(url_for('settings.page', tab='payments'))


@settings_bp.post('/settings/accounts/<int:account_id>/toggle')
@require_role('owner')
def toggle_account(account_id):
    with transaction(get_db()) as db:
        account = db.execute('SELECT is_active FROM financial_accounts WHERE id=?', (account_id,)).fetchone()
        if not account:
            abort(404)
        db.execute('UPDATE financial_accounts SET is_active=? WHERE id=?', (1-account['is_active'], account_id))
        audit(db, 'account.toggle', 'financial_account', account_id)
    flash('وضعیت حساب تغییر کرد.', 'success')
    return redirect(url_for('settings.page', tab='payments'))


def detect_image(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


@settings_bp.get('/brand-logo')
def logo():
    row = get_db().execute('SELECT logo_content,logo_mime FROM shop_settings WHERE id=1').fetchone()
    if row and row['logo_content']:
        return send_file(BytesIO(row['logo_content']), mimetype=row['logo_mime'],
                         download_name='shop-logo', as_attachment=False)
    return send_file(Path(__file__).parent / 'static' / 'default-logo.svg', mimetype='image/svg+xml')


@settings_bp.post('/settings/logo')
@require_role('owner')
def upload_logo():
    uploaded = request.files.get('logo')
    data = uploaded.read(1_000_001) if uploaded else b''
    mime = detect_image(data)
    if not data or len(data) > 1_000_000 or not mime:
        flash('لوگو باید فایل PNG، JPEG یا WebP معتبر و حداکثر یک مگابایت باشد.', 'error')
    else:
        with transaction(get_db()) as db:
            db.execute('''UPDATE shop_settings SET logo_content=?,logo_mime=?,logo_revision=logo_revision+1,
                updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1''', (data, mime, g.user['id']))
            audit(db, 'settings.logo.upload', 'shop_settings', 1, {'mime': mime, 'bytes': len(data)})
        flash('لوگوی جدید فعال شد.', 'success')
    return redirect(url_for('settings.page', tab='identity'))


@settings_bp.post('/settings/logo/reset')
@require_role('owner')
def reset_logo():
    with transaction(get_db()) as db:
        db.execute('''UPDATE shop_settings SET logo_content=NULL,logo_mime=NULL,logo_revision=logo_revision+1,
            updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1''', (g.user['id'],))
        audit(db, 'settings.logo.reset', 'shop_settings', 1)
    flash('لوگوی پیش‌فرض فعال شد.', 'success')
    return redirect(url_for('settings.page', tab='identity'))


@settings_bp.post('/settings/price-types')
@require_role('owner')
def add_price_type():
    try:
        label = required(request.form.get('label'), 'عنوان نوع قیمت', 50)
        with transaction(get_db()) as db:
            cur = db.execute('INSERT INTO price_types(code,label) VALUES (?,?)',
                             ('custom-' + secrets.token_hex(5), label))
            audit(db, 'price_type.create', 'price_type', cur.lastrowid)
        flash('نوع قیمت اضافه شد.', 'success')
    except (ValueError, sqlite3.IntegrityError):
        flash('عنوان نوع قیمت تکراری یا نامعتبر است.', 'error')
    return redirect(url_for('settings.page', tab='sales'))


@settings_bp.post('/settings/price-types/<int:tier_id>/toggle')
@require_role('owner')
def toggle_price_type(tier_id):
    try:
        with transaction(get_db()) as db:
            tier = db.execute('SELECT is_active FROM price_types WHERE id=?', (tier_id,)).fetchone()
            if not tier:
                abort(404)
            current = current_settings(db)
            if tier['is_active'] and current['default_price_type_id'] == tier_id:
                raise ValueError('ابتدا نوع قیمت پیش‌فرض دیگری انتخاب کنید.')
            db.execute('UPDATE price_types SET is_active=? WHERE id=?', (1 - tier['is_active'], tier_id))
            audit(db, 'price_type.toggle', 'price_type', tier_id)
        flash('وضعیت نوع قیمت تغییر کرد.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('settings.page', tab='sales'))
