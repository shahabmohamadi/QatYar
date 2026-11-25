"""Application factory; each business area lives in its own module."""
import os

from flask import Flask, g, render_template

from .auth import auth, check_csrf, load_user
from .backup import backup
from .catalog import catalog
from .checks import checks
from .db import close_db, data_dir, get_db, migrate
from .invoices import invoices
from .legacy import legacy
from .opening_balances import opening_balances
from .parties import parties
from .purchase_returns import purchase_returns
from .reports import reports
from .returns import returns
from .settings import settings_bp, settings_for_request
from .users import users
from .updates import updates


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    migrate()
    secret_path = data_dir() / 'flask-secret.bin'
    if not secret_path.exists():
        try:
            with secret_path.open('xb') as target:
                target.write(os.urandom(32))
            if os.name != 'nt':
                secret_path.chmod(0o600)
        except FileExistsError:
            pass
    app.config['SECRET_KEY'] = secret_path.read_bytes()
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
    app.register_blueprint(auth)
    app.register_blueprint(catalog)
    app.register_blueprint(checks)
    app.register_blueprint(invoices)
    app.register_blueprint(legacy)
    app.register_blueprint(opening_balances)
    app.register_blueprint(parties)
    app.register_blueprint(purchase_returns)
    app.register_blueprint(reports, name='web')
    app.register_blueprint(returns)
    app.register_blueprint(users)
    app.register_blueprint(updates)
    app.register_blueprint(backup)
    app.register_blueprint(settings_bp)
    app.before_request(load_user)
    app.before_request(check_csrf)
    app.teardown_appcontext(close_db)

    @app.context_processor
    def template_values():
        branch = get_db().execute('SELECT name FROM branches WHERE id=1').fetchone()
        return {'money': lambda number: f'{int(number or 0):,}',
                'csrf_token': lambda: g.csrf_token or '',
                'cfg': settings_for_request(),
                'branch_name': branch['name'] if branch else ''}

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template('error.html', message='اجازه دسترسی به این بخش را ندارید.'), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template('error.html', message='صفحه موردنظر پیدا نشد.'), 404

    @app.errorhandler(400)
    def bad_request(_error):
        return render_template('error.html', message='درخواست نامعتبر است. صفحه را تازه‌سازی کنید.'), 400

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    return app
