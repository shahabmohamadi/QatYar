"""Start the LAN web server or initialize the first owner account."""
import argparse
import getpass
import json
import os

from waitress import serve

from store.app import create_app
from store.auth import password_hash
from store.backup import create_backup, restore_backup, scheduled_backup
from store.db import connect, migrate, transaction


def main():
    parser = argparse.ArgumentParser(description='فروشگاه قطعات موبایل')
    commands = parser.add_subparsers(dest='command')
    init = commands.add_parser('init', help='ساخت حساب مدیر اولیه')
    init.add_argument('--username', default='admin')
    init.add_argument('--display-name', default='مدیر فروشگاه')
    commands.add_parser('owner-count', help='شمار حساب‌های مالک برای نصب دوباره')
    commands.add_parser('serve', help='اجرای پنل وب')
    backup_cmd = commands.add_parser('backup', help='ساخت بکاپ سازگار با انتقال رایانه')
    backup_cmd.add_argument('--output', required=True)
    restore_cmd = commands.add_parser('restore', help='بازگردانی بکاپ روی رایانه جدید')
    restore_cmd.add_argument('--input', required=True)
    commands.add_parser('scheduled-backup', help='بکاپ روزانه طبق تنظیمات برنامه')
    backup_directory = commands.add_parser('set-backup-directory', help='تنظیم مسیر بکاپ هنگام نصب')
    backup_directory.add_argument('--path', required=True)
    parsian = commands.add_parser('import-parsian', help='ورود آرشیو سال‌های مالی پارسیان')
    parsian.add_argument('--input', required=True)
    commands.add_parser('activate-parsian-catalog', help='نمایش کالا و آخرین قیمت فروش تاریخی پارسیان')
    commands.add_parser('activate-parsian-opening-balances', help='ورود ماندهٔ افتتاحیه طرف‌حساب، صندوق و بانک از پارسیان')
    args = parser.parse_args()
    migrate()
    if args.command == 'owner-count':
        db = connect()
        try:
            print(db.execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0])
        finally:
            db.close()
        return
    if args.command == 'init':
        db = connect()
        try:
            if db.execute("SELECT 1 FROM users WHERE role='owner'").fetchone():
                parser.error('مدیر اولیه قبلاً ساخته شده است.')
            password = os.environ.pop('STORE_INITIAL_ADMIN_PASSWORD', None)
            if password is None:
                password = getpass.getpass('رمز مدیر (حداقل ۱۲ نویسه): ')
            if len(password) < 12:
                parser.error('رمز کوتاه است.')
            with transaction(db):
                db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES (?,?,?,?)',
                           (args.username.strip(), args.display_name.strip(), password_hash(password), 'owner'))
            print('حساب مدیر ساخته شد.')
        finally:
            db.close()
        return
    if args.command == 'backup':
        print(create_backup(args.output))
        return
    if args.command == 'restore':
        restore_backup(args.input)
        print('بکاپ بازگردانی شد. برنامه را دوباره اجرا کنید.')
        return
    if args.command == 'scheduled-backup':
        result = scheduled_backup()
        print(result or 'بکاپ خودکار غیرفعال است.')
        return
    if args.command == 'set-backup-directory':
        from pathlib import Path
        path = Path(args.path).expanduser()
        if not path.is_absolute():
            parser.error('مسیر بکاپ باید کامل باشد.')
        db = connect()
        try:
            with transaction(db):
                db.execute('UPDATE shop_settings SET backup_directory=?,updated_at=CURRENT_TIMESTAMP WHERE id=1',
                           (str(path),))
        finally:
            db.close()
        print('مسیر بکاپ ذخیره شد.')
        return
    if args.command == 'import-parsian':
        from store.legacy_import import import_parsian_archive
        print(json.dumps(import_parsian_archive(args.input), ensure_ascii=False, indent=2))
        return
    if args.command == 'activate-parsian-catalog':
        from store.legacy_import import activate_existing_parsian_catalog
        print(json.dumps(activate_existing_parsian_catalog(), ensure_ascii=False))
        return
    if args.command == 'activate-parsian-opening-balances':
        from store.opening_balances import activate_existing
        print(json.dumps(activate_existing(), ensure_ascii=False))
        return
    if args.command in (None, 'serve'):
        app = create_app()
        host = os.environ.get('STORE_HOST', '127.0.0.1')
        port = int(os.environ.get('STORE_PORT', '8080'))
        print(f'Store web: http://{host}:{port}')
        serve(app, host=host, port=port, threads=8)


if __name__ == '__main__':
    main()
