"""SQLite connection and forward-only schema migrations."""
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3

from flask import current_app, g


def data_dir() -> Path:
    configured = os.environ.get('STORE_DATA_DIR')
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('PROGRAMDATA', Path.home())) / 'MobilePartsShop'
    return Path.home() / '.mobile-parts-shop'


def db_path() -> Path:
    return data_dir() / 'store.db'


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.parent.chmod(0o700)
    db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('PRAGMA journal_mode=WAL')
    return db


def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        g.db = connect()
    return g.db


def close_db(_error=None) -> None:
    db = g.pop('db', None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db: sqlite3.Connection):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def migrate() -> None:
    db = connect()
    try:
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        migration_dir = Path(__file__).parent / 'migrations'
        for path in sorted(migration_dir.glob('*.sql')):
            if db.execute('SELECT 1 FROM schema_migrations WHERE version=?', (path.stem,)).fetchone():
                continue
            try:
                db.executescript('BEGIN IMMEDIATE;\n' + path.read_text(encoding='utf-8') +
                                 '\nINSERT INTO schema_migrations(version) VALUES (\'' + path.stem + '\');\nCOMMIT;')
            except Exception:
                db.rollback()
                raise
        if db.execute("SELECT 1 FROM pragma_table_info('products') WHERE name='search_name'").fetchone():
            from .search import normalize_code, normalize_name
            stale = db.execute("SELECT id,sku,name,brand,category FROM products WHERE search_name='' OR search_sku=''").fetchall()
            if stale:
                with transaction(db):
                    for row in stale:
                        db.execute('UPDATE products SET search_name=?,search_sku=? WHERE id=?',
                                   (normalize_name(' '.join((row['name'], row['brand'], row['category']))),
                                    normalize_code(row['sku']), row['id']))
    finally:
        db.close()
