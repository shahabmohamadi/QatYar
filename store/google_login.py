"""Optional Google account sign-in for staff explicitly linked by the owner."""
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import Blueprint, flash, redirect, request, session, url_for

from .auth import issue_session
from .db import get_db, transaction
from .utils import audit

google_login = Blueprint('google_login', __name__)
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'


def google_config():
    client_id = os.environ.get('QATYAR_GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.environ.get('QATYAR_GOOGLE_CLIENT_SECRET', '').strip()
    redirect_uri = os.environ.get('QATYAR_GOOGLE_REDIRECT_URI', '').strip()
    parsed = urlsplit(redirect_uri)
    local = parsed.hostname in ('localhost', '127.0.0.1') and parsed.scheme == 'http'
    if not client_id or not client_secret or not redirect_uri or not (parsed.scheme == 'https' or local):
        return None
    if parsed.path != '/login/google/callback' or parsed.query or parsed.fragment:
        return None
    return client_id, client_secret, redirect_uri


def _read_json(url, data=None, headers=None):
    req = Request(url, data=data, headers=headers or {})
    with urlopen(req, timeout=12) as response:
        payload = response.read(100_001)
    if len(payload) > 100_000:
        raise ValueError('پاسخ گوگل بیش از حد بزرگ است.')
    return json.loads(payload)


@google_login.get('/login/google')
def start():
    config = google_config()
    if not config:
        flash('ورود با گوگل هنوز برای این نصب تنظیم نشده است.', 'error')
        return redirect(url_for('auth.login'))
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    session['google_oauth'] = {'state': state, 'verifier': verifier, 'created': int(datetime.now(timezone.utc).timestamp())}
    query = urlencode({'client_id': config[0], 'redirect_uri': config[2], 'response_type': 'code',
                       'scope': 'openid email profile', 'state': state,
                       'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account'})
    return redirect(AUTH_URL + '?' + query)


@google_login.get('/login/google/callback')
def callback():
    flow = session.pop('google_oauth', None)
    config = google_config()
    now = int(datetime.now(timezone.utc).timestamp())
    if not config or not flow or now - flow.get('created', 0) > 600 or now < flow.get('created', 0):
        flash('نشست ورود با گوگل معتبر نیست؛ دوباره تلاش کنید.', 'error')
        return redirect(url_for('auth.login'))
    if not hmac.compare_digest(request.args.get('state', ''), flow['state']) or not request.args.get('code'):
        flash('پاسخ ورود با گوگل معتبر نیست.', 'error')
        return redirect(url_for('auth.login'))
    try:
        token = _read_json(TOKEN_URL, urlencode({'code': request.args['code'], 'client_id': config[0],
            'client_secret': config[1], 'redirect_uri': config[2], 'grant_type': 'authorization_code',
            'code_verifier': flow['verifier']}).encode(), {'Content-Type': 'application/x-www-form-urlencoded'})
        access_token = token.get('access_token', '')
        if not access_token:
            raise ValueError('توکن ورود دریافت نشد.')
        info = _read_json(USERINFO_URL, headers={'Authorization': 'Bearer ' + access_token})
        email = str(info.get('email', '')).strip().lower()
        subject = str(info.get('sub', ''))
        if info.get('email_verified') is not True or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or not subject:
            raise ValueError('ایمیل گوگل تأیید نشده است.')
        db = get_db()
        user = db.execute('SELECT id FROM users WHERE google_sub=? AND is_active=1', (subject,)).fetchone()
        if not user:
            user = db.execute('SELECT id FROM users WHERE lower(google_email)=? AND google_sub IS NULL AND is_active=1', (email,)).fetchone()
            if user:
                with transaction(db):
                    db.execute('UPDATE users SET google_sub=? WHERE id=?', (subject, user['id']))
                    audit(db, 'user.google.link', 'user', user['id'])
        if not user:
            raise ValueError('این حساب گوگل به کاربر فعال برنامه متصل نشده است.')
        return issue_session(user['id'], 'login.google')
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
        flash('ورود با گوگل انجام نشد. ایمیل باید در بخش کاربران ثبت و تأیید شده باشد.', 'error')
        return redirect(url_for('auth.login'))
