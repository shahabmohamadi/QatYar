"""Consistent SQLite archives for moving a shop to another computer."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import tempfile
import zipfile

from flask import Blueprint, after_this_request, g, send_file

from .auth import require_role
from .db import connect, data_dir, db_path, get_db, transaction
from .utils import audit

backup = Blueprint('backup', __name__)
FORMAT_VERSION = 1


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def create_backup(output_path):
    """Use SQLite's online backup API so the archive includes committed WAL data."""
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('فایل بکاپ از قبل وجود دارد.')
    with tempfile.TemporaryDirectory(prefix='store-snapshot-') as scratch:
        snapshot = Path(scratch) / 'store.db'
        source = connect()
        target = sqlite3.connect(str(snapshot))
        try:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('پایگاه داده سالم نیست.')
            version = target.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
        finally:
            target.close()
            source.close()
        manifest = {
            'format': 'mobile-parts-shop-backup', 'format_version': FORMAT_VERSION,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'schema_version': version, 'database_sha256': _sha256(snapshot),
        }
        try:
            with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(snapshot, 'store.db')
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            if os.name != 'nt':
                output.chmod(0o600)
        except FileExistsError:
            raise
        except Exception:
            output.unlink(missing_ok=True)
            raise
    return output


def scheduled_backup():
    """Called by the Windows daily task; reads live settings each time."""
    db = connect()
    try:
        row = db.execute('SELECT backup_enabled,backup_retention_days,backup_directory FROM shop_settings WHERE id=1').fetchone()
        if not row or not row['backup_enabled']:
            return None
        destination_dir = Path(row['backup_directory']).expanduser() if row['backup_directory'] else data_dir() / 'backups'
        retention = row['backup_retention_days']
    finally:
        db.close()
    destination = destination_dir / ('store-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip')
    try:
        created = create_backup(destination)
        cutoff = datetime.now().timestamp() - retention * 86400
        for candidate in destination_dir.glob('store-*.zip'):
            if candidate != created and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        db = connect()
        try:
            with transaction(db):
                db.execute('INSERT INTO backup_runs(outcome,destination) VALUES (?,?)', ('success', str(created)))
        finally:
            db.close()
        return created
    except Exception as exc:
        db = connect()
        try:
            with transaction(db):
                db.execute('INSERT INTO backup_runs(outcome,destination,detail) VALUES (?,?,?)',
                           ('error', str(destination), str(exc)[:240]))
        finally:
            db.close()
        raise


def restore_backup(archive_path):
    """Restore a verified archive while the web server is stopped."""
    port = int(os.environ.get('STORE_PORT', '8080'))
    with socket.socket() as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise RuntimeError('برای بازگردانی، ابتدا سرویس وب را متوقف کنید.')
    destination = db_path()
    if destination.exists():
        existing = sqlite3.connect(str(destination))
        try:
            business_rows = sum(existing.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                                for table in ('products', 'invoices', 'money_movements'))
            if business_rows:
                raise RuntimeError('این نصب دادهٔ فروشگاهی دارد؛ بازگردانی روی آن مجاز نیست. ابتدا از دادهٔ فعلی بکاپ بگیرید و نصب خالی آماده کنید.')
        finally:
            existing.close()
    archive_path = Path(archive_path).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix='store-restore-') as scratch:
        candidate = Path(scratch) / 'store.db'
        with zipfile.ZipFile(archive_path) as archive:
            if set(archive.namelist()) != {'store.db', 'manifest.json'}:
                raise ValueError('ساختار فایل بکاپ معتبر نیست.')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('format') != 'mobile-parts-shop-backup' or manifest.get('format_version') != FORMAT_VERSION:
                raise ValueError('نسخه یا نوع بکاپ پشتیبانی نمی‌شود.')
            with archive.open('store.db') as source, candidate.open('wb') as target:
                shutil.copyfileobj(source, target)
        if _sha256(candidate) != manifest.get('database_sha256'):
            raise ValueError('کنترل صحت بکاپ ناموفق بود.')
        db = sqlite3.connect(str(candidate))
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('پایگاه داده داخل بکاپ سالم نیست.')
            version = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
            if version != manifest.get('schema_version'):
                raise ValueError('نسخه ساختار پایگاه داده با فایل بکاپ سازگار نیست.')
            available = [p.stem for p in (Path(__file__).parent / 'migrations').glob('*.sql')]
            if version not in available:
                raise ValueError('این بکاپ به نسخه جدیدتر برنامه نیاز دارد.')
            db.execute('DELETE FROM sessions')
            db.commit()
        finally:
            db.close()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name('store.restore.tmp')
        shutil.copy2(candidate, temporary)
        if os.name != 'nt':
            temporary.chmod(0o600)
        os.replace(temporary, destination)
        for suffix in ('-wal', '-shm'):
            destination.with_name(destination.name + suffix).unlink(missing_ok=True)


@backup.post('/backups/download')
@require_role('owner')
def download_backup():
    filename = 'store-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip'
    temporary = data_dir() / ('download-' + secrets.token_hex(5) + '-' + filename)
    path = create_backup(temporary)
    with transaction(get_db()) as db:
        audit(db, 'backup.download', 'backup', None, {'filename': filename})

    @after_this_request
    def remove_temporary(response):
        response.call_on_close(lambda: path.unlink(missing_ok=True))
        return response

    return send_file(path, as_attachment=True, download_name=filename, mimetype='application/zip')
