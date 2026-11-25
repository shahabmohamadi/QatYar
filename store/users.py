"""Owner-managed staff accounts."""
import sqlite3

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .auth import password_hash, require_login, require_role
from .db import get_db, transaction
from .pagination import paginate_query
from .utils import audit, required

users = Blueprint('users', __name__)


@users.get('/users')
@require_role('owner')
def list_users():
    rows, pager = paginate_query(get_db(), 'SELECT id,username,display_name,role,is_active,created_at FROM users ORDER BY id')
    return render_template('users.html', users=rows, pager=pager)


@users.post('/users')
@require_role('owner')
def create_user():
    try:
        username = required(request.form.get('username'), 'نام کاربری', 60)
        display_name = required(request.form.get('display_name'), 'نام نمایشی')
        role = request.form.get('role', '')
        password = request.form.get('password', '')
        if role not in ('manager', 'clerk'):
            raise ValueError('نقش نامعتبر است.')
        if len(password) < 12:
            raise ValueError('رمز باید حداقل ۱۲ نویسه باشد.')
        with transaction(get_db()) as db:
            cur = db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES (?,?,?,?)',
                             (username, display_name, password_hash(password), role))
            audit(db, 'user.create', 'user', cur.lastrowid, {'role': role})
        flash('حساب کارمند ساخته شد.', 'success')
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc), 'error')
    return redirect(url_for('users.list_users'))


@users.post('/users/<int:user_id>/toggle')
@require_role('owner')
def toggle_user(user_id):
    if user_id == g.user['id']:
        flash('حساب خودتان را نمی‌توانید غیرفعال کنید.', 'error')
        return redirect(url_for('users.list_users'))
    with transaction(get_db()) as db:
        row = db.execute('SELECT role,is_active FROM users WHERE id=?', (user_id,)).fetchone()
        if row and row['role'] != 'owner':
            db.execute('UPDATE users SET is_active=? WHERE id=?', (1 - row['is_active'], user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            audit(db, 'user.toggle', 'user', user_id)
    return redirect(url_for('users.list_users'))


@users.post('/my-password')
@require_login
def change_password():
    current = request.form.get('current_password', '')
    new = request.form.get('new_password', '')
    row = get_db().execute('SELECT password_hash FROM users WHERE id=?', (g.user['id'],)).fetchone()
    if not check_password_hash(row['password_hash'], current) or len(new) < 12:
        flash('رمز فعلی درست نیست یا رمز جدید کمتر از ۱۲ نویسه است.', 'error')
        return redirect(url_for('users.profile'))
    with transaction(get_db()) as db:
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash(new), g.user['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?', (g.user['id'],))
        audit(db, 'password.change', 'user', g.user['id'])
    response = redirect(url_for('auth.login'))
    response.delete_cookie('store_session')
    flash('رمز تغییر کرد. دوباره وارد شوید.', 'success')
    return response


@users.get('/profile')
@require_login
def profile():
    return render_template('profile.html')
