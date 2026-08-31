"""Neon Postgres connection handling.

Streamlit is a thin presentation layer — this module is the only place that
talks to the database driver. Connection string comes from Streamlit secrets
only, never from code or the repo.
"""

from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import streamlit as st
from psycopg2.pool import ThreadedConnectionPool


def _connection_string() -> str:
    try:
        return st.secrets["neon"]["url"]
    except (KeyError, FileNotFoundError):
        st.error(
            "Database connection is not configured. Add a `[neon]` section with a "
            "`url` to `.streamlit/secrets.toml` (locally) or to the app's Secrets "
            "(on Streamlit Community Cloud)."
        )
        st.stop()


@st.cache_resource(show_spinner=False)
def _pool() -> ThreadedConnectionPool:
    return ThreadedConnectionPool(1, 10, dsn=_connection_string())


_CONNECTION_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)


@contextmanager
def get_cursor(commit: bool = False):
    """Yield a RealDictCursor from the pool; commits on success if requested.

    Neon's pooled endpoint closes connections that sit idle between Streamlit
    reruns, so a connection handed back by the pool can already be dead. When
    that happens we discard it (close=True) instead of returning it to the
    pool, so the next getconn() opens a fresh one rather than handing back
    the same broken connection.
    """
    pool = _pool()
    conn = pool.getconn()
    stale = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception as e:
        stale = isinstance(e, _CONNECTION_ERRORS)
        try:
            conn.rollback()
        except Exception:
            pass  # the connection is already dead; nothing to roll back
        raise
    finally:
        pool.putconn(conn, close=stale)


def _with_retry(fn):
    """Run fn() once, retrying a single time if the pooled connection was stale."""
    try:
        return fn()
    except _CONNECTION_ERRORS:
        return fn()


def query(sql: str, params: tuple = ()) -> list:
    def _do():
        with get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    return _with_retry(_do)


def query_one(sql: str, params: tuple = ()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    def _do():
        with get_cursor(commit=True) as cur:
            cur.execute(sql, params)

    _with_retry(_do)


def execute_returning(sql: str, params: tuple = ()):
    def _do():
        with get_cursor(commit=True) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    return _with_retry(_do)
