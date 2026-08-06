"""Database backend switch: DATA_MODE=sqlite (default) or postgres.

serve/app.py connects through here, so the same SQL — written with sqlite's
own placeholder styles (`?` positional, `:name` named) — runs unchanged on
either backend. connect() translates placeholders and normalises row access
(dict-by-column-name and list-by-position, like sqlite3.Row) so callers never
branch on backend. process/mapper.py always writes SQLite directly and does
not use this module.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

DATA_MODE = os.getenv('DATA_MODE', 'sqlite')
if DATA_MODE not in ('sqlite', 'postgres'):
    raise RuntimeError(f"Unknown DATA_MODE {DATA_MODE!r} — must be 'sqlite' or 'postgres'")

# Relative SQLITE_PATH is read from the project root, so the app runs the same
# whatever directory it was started from.
SQLITE_PATH = ROOT_DIR / os.getenv('SQLITE_PATH', 'data/database.sqlite')
DATABASE_URL = os.getenv('DATABASE_URL', '')

_NAMED_PLACEHOLDER = re.compile(r':(\w+)')


def _to_pyformat(query: str) -> str:
    """Translate sqlite-style placeholders to psycopg's (?, :name -> %s, %(name)s)."""
    query = _NAMED_PLACEHOLDER.sub(r'%(\1)s', query)
    return query.replace('?', '%s')


@lru_cache(maxsize=256)
def _row_class(columns: tuple) -> type:
    """Build (and cache) a tuple subclass for one column layout.

    tuple is a variable-length builtin, so CPython refuses per-instance
    __slots__ on subclasses — the column names have to live on the class
    instead. One class per distinct column tuple, cached and reused for
    every row of a query, mimics sqlite3.Row: addressable by position (like
    a tuple) or column name (``row['code']``, ``dict(row)``, ``list(row)``).
    """

    class _Row(tuple):
        _columns = columns

        def __getitem__(self, key):
            if isinstance(key, str):
                return tuple.__getitem__(self, self._columns.index(key))
            return tuple.__getitem__(self, key)

        def keys(self):
            return self._columns

    return _Row


def _pg_row_factory(cursor):
    row_class = _row_class(tuple(d.name for d in cursor.description))
    return lambda values: row_class(values)


def _connect_sqlite(readonly: bool):
    import sqlite3

    if not SQLITE_PATH.exists():
        raise FileNotFoundError(f'Database not found at {SQLITE_PATH}')
    mode = 'ro' if readonly else 'rwc'
    conn = sqlite3.connect(f'file:{SQLITE_PATH.as_posix()}?mode={mode}', uri=True)
    conn.row_factory = sqlite3.Row
    if readonly:
        conn.execute('PRAGMA query_only = 1')
    return conn


def _connect_postgres(readonly: bool):
    import psycopg

    if not DATABASE_URL:
        raise RuntimeError('DATA_MODE=postgres requires DATABASE_URL to be set')
    options = '-c default_transaction_read_only=on' if readonly else ''
    return psycopg.connect(DATABASE_URL, row_factory=_pg_row_factory, options=options)


def connect(readonly: bool = True):
    """Open a connection to whichever backend DATA_MODE selects.

    The returned object exposes ``execute``/``executemany`` with sqlite-style
    placeholders regardless of backend, plus ``commit``/``close`` and context-
    manager support that always closes the connection on exit.
    """
    if DATA_MODE == 'sqlite':
        return _SqliteConnection(_connect_sqlite(readonly))
    return _PgConnection(_connect_postgres(readonly))


class _SqliteConnection:
    """Thin wrapper so sqlite's context manager closes the connection on exit,
    matching Postgres — plain sqlite3.Connection commits/rolls back but leaves
    the connection open, which would leak real TCP connections on Postgres."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        return self._conn.execute(query, params)

    def executemany(self, query, seq):
        return self._conn.executemany(query, seq)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()


class _PgConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        return self._conn.execute(_to_pyformat(query), params)

    def executemany(self, query, seq):
        cursor = self._conn.cursor()
        cursor.executemany(_to_pyformat(query), seq)
        return cursor

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
