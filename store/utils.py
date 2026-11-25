import json
import re

from flask import g

PERSIAN_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
MAX_MONEY = 10 ** 15


def integer(raw, label, *, minimum=0, maximum=MAX_MONEY):
    value = str(raw or '').translate(PERSIAN_DIGITS).replace(',', '').replace('٬', '').replace(' ', '')
    if not re.fullmatch(r'-?\d+', value):
        raise ValueError(f'{label} باید عدد صحیح باشد.')
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f'{label} خارج از محدوده مجاز است.')
    return parsed


def required(raw, label, length=120):
    value = str(raw or '').strip()
    if not value or len(value) > length:
        raise ValueError(f'{label} لازم است و باید حداکثر {length} نویسه باشد.')
    return value


def audit(db, action, entity_type, entity_id=None, detail=None):
    db.execute('INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,detail) VALUES (?,?,?,?,?)',
               (g.user['id'] if g.user else None, action, entity_type, entity_id,
                json.dumps(detail or {}, ensure_ascii=False)))
