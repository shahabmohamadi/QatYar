"""Consistent ten-row, numbered pagination for SQLite-backed lists."""
from dataclasses import dataclass
from math import ceil

from flask import request, url_for

PAGE_SIZE = 10


@dataclass(frozen=True)
class Pager:
    param: str
    page: int
    pages: int
    total: int
    numbers: tuple[int, ...]


def _page_number(param):
    raw = request.args.get(param, '1')
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def paginate_query(db, sql, params=(), *, param='page'):
    """Return one page without truncating the underlying list."""
    params = tuple(params)
    total = db.execute(f'SELECT COUNT(*) FROM ({sql}) AS page_source', params).fetchone()[0]
    pages = max(1, ceil(total / PAGE_SIZE))
    page = min(_page_number(param), pages)
    rows = db.execute(sql + ' LIMIT ? OFFSET ?', (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
    candidates = {1, pages, *(range(max(1, page - 2), min(pages, page + 2) + 1))}
    return rows, Pager(param, page, pages, total, tuple(sorted(candidates)))


def paginate_items(items, *, param='page'):
    """Paginate small computed collections whose full values feed page totals."""
    total = len(items)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = min(_page_number(param), pages)
    candidates = {1, pages, *(range(max(1, page - 2), min(pages, page + 2) + 1))}
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], Pager(param, page, pages, total, tuple(sorted(candidates)))


def page_url(pager, number):
    values = request.args.to_dict(flat=True)
    values[pager.param] = str(number)
    return url_for(request.endpoint, **(request.view_args or {}), **values)
