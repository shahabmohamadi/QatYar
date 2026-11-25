"""Owner-approved GitHub Release checks and Windows in-place updates."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from .auth import require_role
from .db import data_dir, get_db, transaction
from .utils import audit

updates = Blueprint('updates', __name__)
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
ASSET = 'QatYar-Windows.zip'


def github_token():
    """Read an optional host-local token; never accept it through the LAN UI."""
    token_file = data_dir() / 'github-token.txt'
    value = os.environ.get('STORE_GITHUB_TOKEN', '')
    if not value and token_file.exists():
        value = token_file.read_text(encoding='utf-8').strip()
    return value[:512]


def version():
    path = Path(__file__).resolve().parent.parent / 'VERSION'
    return path.read_text(encoding='utf-8').strip() if path.exists() else '1.0.0'


def version_key(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value or '')
    return tuple(map(int, match.groups())) if match else None


def release(repo):
    if not REPO.fullmatch(repo):
        raise ValueError('نشانی مخزن را به صورت owner/repo وارد کنید.')
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'QatYar-Updater/1.1'}
    if github_token():
        headers['Authorization'] = 'Bearer ' + github_token()
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/releases/latest', headers=headers)
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = response.read(1_000_001)
    if len(payload) > 1_000_000:
        raise ValueError('پاسخ GitHub بیش از حد بزرگ است.')
    info = json.loads(payload)
    tag = info.get('tag_name', '')
    if not version_key(tag):
        raise ValueError('شماره نسخه Release باید مانند v1.2.3 باشد.')
    if info.get('draft') or info.get('prerelease'):
        raise ValueError('نسخه آزمایشی قابل نصب نیست.')
    asset = next((x for x in info.get('assets', []) if x.get('name') == ASSET), None)
    if not asset or not DIGEST.fullmatch(asset.get('digest', '')) or not 0 < asset.get('size', 0) < 200_000_000:
        raise ValueError('فایل انتشار یا اثرانگشت SHA256 آن معتبر نیست.')
    url = asset.get('url', '')
    if not re.fullmatch(r'https://api\.github\.com/repos/' + re.escape(repo) + r'/releases/assets/\d+', url):
        raise ValueError('نشانی فایل انتشار معتبر نیست.')
    return {'tag': tag, 'notes': (info.get('body') or '')[:20000],
            'url': url, 'digest': asset['digest']}


@updates.post('/settings/updates/repo')
@require_role('owner')
def set_repo():
    repo = request.form.get('github_repo', '').strip()
    if repo and not REPO.fullmatch(repo):
        flash('مخزن باید به شکل owner/repo باشد.', 'error')
    else:
        with transaction(get_db()) as db:
            db.execute('''UPDATE update_settings SET github_repo=?,release_tag='',release_notes='',
                asset_url='',asset_digest='',checked_at=NULL WHERE id=1''', (repo,))
            audit(db, 'updates.repo', 'update_settings', 1, {'repo': repo})
        flash('مخزن به‌روزرسانی ذخیره شد.', 'success')
    return redirect(url_for('settings.page', tab='updates'))


@updates.post('/settings/updates/check')
@require_role('owner')
def check():
    repo = get_db().execute('SELECT github_repo FROM update_settings WHERE id=1').fetchone()[0]
    try:
        candidate = release(repo)
        with transaction(get_db()) as db:
            db.execute('''UPDATE update_settings SET release_tag=?,release_notes=?,asset_url=?,
                asset_digest=?,checked_at=CURRENT_TIMESTAMP WHERE id=1''',
                (candidate['tag'],candidate['notes'],candidate['url'],candidate['digest']))
            audit(db, 'updates.check', 'update_settings', 1, {'tag': candidate['tag']})
        flash('اطلاعات نسخه از GitHub دریافت شد.', 'success')
    except Exception as exc:
        flash('بررسی نسخه انجام نشد: ' + str(exc)[:180], 'error')
    return redirect(url_for('settings.page', tab='updates'))


@updates.post('/settings/updates/apply')
@require_role('owner')
def apply():
    row = get_db().execute('SELECT * FROM update_settings WHERE id=1').fetchone()
    try:
        if os.name != 'nt':
            raise ValueError('نصب خودکار فقط روی ویندوز مرکزی در دسترس است.')
        if not row['github_repo'] or not row['release_tag'] or not version_key(row['release_tag']):
            raise ValueError('ابتدا نسخه را بررسی کنید.')
        if version_key(row['release_tag']) <= (version_key(version()) or (0,0,0)):
            raise ValueError('نسخهٔ جدیدتری موجود نیست.')
        candidate = release(row['github_repo'])
        if (candidate['tag'],candidate['url'],candidate['digest']) != (row['release_tag'],row['asset_url'],row['asset_digest']):
            raise ValueError('نسخه تغییر کرده است؛ تغییرات جدید را دوباره بررسی کنید.')
        script = Path(__file__).resolve().parent.parent / 'windows' / 'update.ps1'
        subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                          '-AssetApiUrl',candidate['url'],'-ExpectedSha256',candidate['digest'][7:],
                          '-VersionTag',candidate['tag']],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        with transaction(get_db()) as db:
            audit(db, 'updates.apply', 'update_settings', 1, {'tag': candidate['tag']})
        flash('به‌روزرسانی شروع شد. چند دقیقه دیگر صفحه را تازه‌سازی کنید.', 'success')
    except Exception as exc:
        flash(str(exc)[:220], 'error')
    return redirect(url_for('settings.page', tab='updates'))
