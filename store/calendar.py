"""Gregorian storage with selectable Jalali or Gregorian input and display."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import re

TEHRAN = timezone(timedelta(hours=3, minutes=30))
_MONTH_LENGTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_DATE = re.compile(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\Z')
_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def gregorian_to_jalali(value: date) -> tuple[int, int, int]:
    gy, gm, gd = value.year, value.month, value.day
    gy2 = gy + (gm > 2)
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    days += gd + sum(_MONTH_LENGTHS[:gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def jalali_to_gregorian(year: int, month: int, day: int) -> date:
    if not 1 <= month <= 12 or not 1 <= day <= (31 if month <= 6 else 30):
        raise ValueError('تاریخ شمسی نامعتبر است.')
    jy = year + 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + day
    days += (month - 1) * 31 if month <= 6 else 186 + (month - 7) * 30
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    result = date(gy, 1, 1) + timedelta(days=days)
    if gregorian_to_jalali(result) != (year, month, day):
        raise ValueError('تاریخ شمسی نامعتبر است.')
    return result


def parse_date(raw: str, calendar: str) -> date:
    match = _DATE.fullmatch((raw or '').strip().translate(_DIGITS))
    if not match:
        raise ValueError('تاریخ را به صورت سال/ماه/روز وارد کنید.')
    year, month, day = map(int, match.groups())
    if calendar not in ('jalali', 'gregorian'):
        raise ValueError('نوع تقویم نامعتبر است.')
    if year < 1700:
        return jalali_to_gregorian(year, month, day)
    return date(year, month, day)


def format_date(value: date, calendar: str) -> str:
    if calendar == 'jalali':
        return '/'.join((str(x).zfill(4 if i == 0 else 2)
                         for i, x in enumerate(gregorian_to_jalali(value))))
    return value.isoformat()


def display_date(value: str | None, calendar: str) -> str:
    """Format UTC database timestamps and imported Jalali dates for the selected calendar."""
    if not value:
        return '—'
    raw = str(value).strip()
    try:
        match = _DATE.fullmatch(raw[:10])
        if not match:
            return raw
        year, month, day = map(int, match.groups())
        is_jalali = year < 1700
        source = jalali_to_gregorian(year, month, day) if is_jalali else date(year, month, day)
        has_time = len(raw) > 10 and (' ' in raw or 'T' in raw)
        if has_time and not is_jalali:
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamp = stamp.astimezone(TEHRAN)
            source = stamp.date()
            return format_date(source, calendar) + ' ' + stamp.strftime('%H:%M')
        return format_date(source, calendar)
    except (ValueError, OverflowError):
        return raw
