"""Server-side login sessions, role checks, and CSRF protection."""
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import hmac
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, transaction

auth = Blueprint('auth', __name__)
_failed = {}


def password_hash(password: str) -> str:
    return generate_password_hash(password, method='pbkdf2:sha256:600000')


def load_user() -> None:
    g.user = None
    g.csrf_token = None
    raw = request.cookies.get('store_session', '')
    if not raw:
        return
    digest = hashlib.sha256(raw.encode()).hexdigest()
    row = get_db().execute(
        '''SELECT u.id,u.username,u.display_name,u.role,s.csrf_token
           FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at>CURRENT_TIMESTAMP AND u.is_active=1''',
        (digest,)).fetchone()
    if row:
        g.user = row
        g.csrf_token = row['csrf_token']


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for('auth.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def require_role(*roles):
    def decorator(view):
        @wraps(view)
        @require_login
        def wrapped(*args, **kwargs):
            if g.user['role'] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def check_csrf() -> None:
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return
    if request.endpoint == 'auth.login':
        return
    supplied = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
    if not g.csrf_token or not hmac.compare_digest(supplied, g.csrf_token):
        abort(400, description='درخواست نامعتبر است. صفحه را تازه‌سازی کنید.')


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('web.dashboard'))
    login_csrf = session.setdefault('login_csrf', secrets.token_urlsafe(24))
    if request.method == 'POST':
        from .settings import current_settings
        if not hmac.compare_digest(request.form.get('login_csrf', ''), login_csrf):
            abort(400)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        login_settings = current_settings()
        key = (request.remote_addr or '', username.casefold())
        count, until = _failed.get(key, (0, datetime.min.replace(tzinfo=timezone.utc)))
        now = datetime.now(timezone.utc)
        if now >= until:
            count = 0
        if count >= login_settings['login_max_attempts'] and now < until:
            flash('ورود برای چند دقیقه محدود شده است.', 'error')
        else:
            row = get_db().execute('SELECT id,password_hash FROM users WHERE username=? AND is_active=1', (username,)).fetchone()
            if row and check_password_hash(row['password_hash'], password):
                _failed.pop(key, None)
                token = secrets.token_urlsafe(40)
                csrf = secrets.token_urlsafe(32)
                session_hours = login_settings['session_hours']
                expiry = (now + timedelta(hours=session_hours)).strftime('%Y-%m-%d %H:%M:%S')
                with transaction(get_db()) as db:
                    db.execute('DELETE FROM sessions WHERE expires_at<=CURRENT_TIMESTAMP')
                    db.execute('INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES (?,?,?,?)',
                               (hashlib.sha256(token.encode()).hexdigest(), row['id'], csrf, expiry))
                    db.execute('INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id) VALUES (?,?,?,?)',
                               (row['id'], 'login', 'user', row['id']))
                target = request.args.get('next', '')
                if not target.startswith('/') or target.startswith('//') or '\\' in target:
                    target = url_for('web.dashboard')
                response = redirect(target)
                response.set_cookie('store_session', token, httponly=True, samesite='Lax',
                                    secure=request.is_secure, max_age=session_hours * 3600)
                return response
            _failed[key] = (count + 1, now + timedelta(minutes=login_settings['login_lock_minutes']))
            flash('نام کاربری یا رمز عبور درست نیست.', 'error')
    return render_template('login.html', login_csrf=login_csrf)


@auth.post('/logout')
@require_login
def logout():
    token = request.cookies.get('store_session', '')
    with transaction(get_db()) as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    response = redirect(url_for('auth.login'))
    response.delete_cookie('store_session')
    return response
