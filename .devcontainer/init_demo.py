"""Create the isolated Codespaces demo owner from a repository-scoped secret."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.auth import password_hash
from store.db import connect, migrate, transaction


if os.environ.get('CODESPACES') != 'true':
    raise SystemExit('Demo initialization is restricted to GitHub Codespaces.')
password = os.environ.get('QATYAR_DEMO_PASSWORD')
if not password:
    raise SystemExit('QATYAR_DEMO_PASSWORD Codespaces secret is missing.')

migrate()
db = connect()
try:
    with transaction(db):
        if not db.execute("SELECT 1 FROM users WHERE role='owner'").fetchone():
            db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES (?,?,?,?)',
                       ('admin', 'مدیر آزمایشی', password_hash(password), 'owner'))
finally:
    db.close()
