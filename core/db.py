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


@contextmanager
def get_cursor(commit: bool = False):
    """Yield a RealDictCursor from the pool; commits on success if requested."""
    pool = _pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def query(sql: str, params: tuple = ()) -> list:
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple = ()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(sql, params)


def execute_returning(sql: str, params: tuple = ()):
    with get_cursor(commit=True) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row
