"""Exercise the GitHub-hosted test server with isolated demo credentials."""
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, Request
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.auth import password_hash
from store.db import connect, migrate, transaction


def main():
    with tempfile.TemporaryDirectory(prefix='qatyar-smoke-') as scratch:
        os.environ['STORE_DATA_DIR'] = scratch
        migrate()
        db = connect()
        with transaction(db):
            db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES (?,?,?,?)',
                       ('admin', 'مدیر آزمایشی', password_hash('admin'), 'owner'))
        db.close()
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        environment = {**os.environ, 'STORE_HOST': '127.0.0.1', 'STORE_PORT': str(port)}
        server = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / 'run.py'), 'serve'],
                                  env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            for _ in range(60):
                try:
                    if opener.open(base + '/health', timeout=1).status == 200:
                        break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Test server did not start')
            login = opener.open(base + '/login', timeout=5).read().decode()
            assert 'ورود با حساب Google' in login
            csrf = re.search(r'name="login_csrf" value="([^"]+)"', login).group(1)
            credentials = urlencode({'login_csrf': csrf, 'username': 'admin', 'password': 'admin'}).encode()
            opener.open(Request(base + '/login', data=credentials), timeout=5)
            for path, marker in (('/', 'dashboard-stats'), ('/invoices', 'operations-panel'),
                                 ('/invoices/new/sale', 'invoice-editor'), ('/users', 'گوگل')):
                page = opener.open(base + path, timeout=5).read().decode()
                assert marker in page, f'{path}: missing {marker}'
            print('GitHub test server: admin login, dashboard, operations, invoice form and users OK')
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
