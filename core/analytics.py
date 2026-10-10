"""Performance dashboard aggregates — every query here is a GROUP BY run in
Neon, never a pull of raw rows into Python for Python-side aggregation, and
every one is wrapped in st.cache_data so the three-tab dashboard doesn't
re-hit Postgres on every filter tweak or widget rerun. The view layer gates
who can even reach this page (EC/super_admin); nothing here re-checks that
itself — these are plain aggregate reads with no side effects.

Recurring jobs are prepaid annually and never individually invoiced, so
every query that touches job-level "project" metrics (revenue, leakage,
cycle time, stalls, reopens) excludes a job whose service recurs
(service_catalogue.recurring_frequency IS NOT NULL) — that's what keeps a
shared recurring-checklist job (one job covering many clients, no single
owner, never invoiced by design) from ever being counted as a project's
throughput or miscounted as leaked/unbilled. Recurring revenue is handled
on its own, entirely separately, from each client's annual_retainer_amount
— see recurring_revenue_monthly.

"Revenue" everywhere here means invoice_line amounts on an invoice that has
actually been approved or paid — a pending_approval invoice isn't yet a
validated billing, and a rejected one was sent back, so neither counts."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.db import query, query_one

CACHE_TTL = 300

_REVENUE_STATUSES = ("approved", "paid")

# Jobs whose service recurs are excluded from every "project" job metric —
# see module docstring. Joined once, reused by every query below that reads
# from `job j`.
_SC_JOIN = "LEFT JOIN service_catalogue sc ON sc.code = j.service_type"
_PROJECT_ONLY = "sc.recurring_frequency IS NULL AND j.client_id IS NOT NULL"


def shifted_range(date_from: date, date_to: date) -> tuple[date, date]:
    """The immediately preceding period of the same length in days — what a
    KPI tile's delta compares the current period against. Not meaningful
    for a snapshot metric (open jobs right now, current receivables), which
    is why those tiles carry no delta at all rather than a comparison
    against a snapshot we never took."""
    span = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span - 1)
    return prev_from, prev_to


def _floatify(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """psycopg2 returns a NUMERIC column as decimal.Decimal, which doesn't
    mix with a plain float anywhere downstream (Altair encoding, a KPI
    delta, a column_config format) — cast right after the query, once,
    instead of re-discovering this at every call site. NaN-safe: a NULL
    stays NULL rather than becoming 0.0."""
    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


# ---------------------------------------------------------------------------
# MONEY
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def revenue_by_month(date_from: date, date_to: date, pillar: str | None, owner_id: int | None) -> pd.DataFrame:
    """Project (episodic) revenue only, by invoice issue date — recurring
    revenue is a separate, amortised line (see recurring_revenue_monthly),
    never a lump sum from a recurring job's own invoice, because a
    recurring job never has one."""
    sql = f"""
        SELECT date_trunc('month', i.invoice_date)::date AS month, SUM(il.amount) AS revenue
        FROM invoice_line il
        JOIN invoice i ON i.id = il.invoice_id
        JOIN job j ON j.id = il.job_id
        {_SC_JOIN}
        WHERE i.status = ANY(%s) AND i.invoice_date BETWEEN %s AND %s AND {_PROJECT_ONLY}
    """
    params: list = [list(_REVENUE_STATUSES), date_from, date_to]
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " GROUP BY 1 ORDER BY 1"
    rows = query(sql, tuple(params))
    df = pd.DataFrame(rows, columns=["month", "revenue"]) if rows else pd.DataFrame(columns=["month", "revenue"])
    return _floatify(df, ["revenue"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def recurring_revenue_monthly() -> dict:
    """Every active client's annual retainer, divided by 12 — flat, because
    there's no tracked history of when a retainer started or changed, so
    nothing here reconstructs a trend that was never recorded. Applied as
    the same flat figure to every month a trend chart shows. clients_set
    lets the view caveat the figure when few/no clients have one on file."""
    row = query_one(
        "SELECT COALESCE(SUM(annual_retainer_amount), 0) AS total, "
        "COUNT(*) FILTER (WHERE annual_retainer_amount IS NOT NULL AND annual_retainer_amount > 0) AS clients_set, "
        "COUNT(*) AS clients_total "
        "FROM client WHERE status = 'active'"
    )
    total = float(row["total"]) if row else 0.0
    return {
        "monthly": total / 12,
        "clients_set": row["clients_set"] if row else 0,
        "clients_total": row["clients_total"] if row else 0,
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def revenue_by_pillar(date_from: date, date_to: date, owner_id: int | None) -> pd.DataFrame:
    sql = f"""
        SELECT j.category AS pillar, SUM(il.amount) AS revenue
        FROM invoice_line il
        JOIN invoice i ON i.id = il.invoice_id
        JOIN job j ON j.id = il.job_id
        {_SC_JOIN}
        WHERE i.status = ANY(%s) AND i.invoice_date BETWEEN %s AND %s AND {_PROJECT_ONLY}
    """
    params: list = [list(_REVENUE_STATUSES), date_from, date_to]
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " GROUP BY 1 ORDER BY 2 DESC"
    rows = query(sql, tuple(params))
    df = pd.DataFrame(rows, columns=["pillar", "revenue"]) if rows else pd.DataFrame(columns=["pillar", "revenue"])
    return _floatify(df, ["revenue"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def top_clients(date_from: date, date_to: date, pillar: str | None, owner_id: int | None, limit: int = 10) -> pd.DataFrame:
    sql = f"""
        SELECT c.name AS client, SUM(il.amount) AS revenue
        FROM invoice_line il
        JOIN invoice i ON i.id = il.invoice_id
        JOIN client c ON c.id = i.client_id
        JOIN job j ON j.id = il.job_id
        {_SC_JOIN}
        WHERE i.status = ANY(%s) AND i.invoice_date BETWEEN %s AND %s AND {_PROJECT_ONLY}
    """
    params: list = [list(_REVENUE_STATUSES), date_from, date_to]
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " GROUP BY 1 ORDER BY 2 DESC LIMIT %s"
    params.append(limit)
    rows = query(sql, tuple(params))
    df = pd.DataFrame(rows, columns=["client", "revenue"]) if rows else pd.DataFrame(columns=["client", "revenue"])
    return _floatify(df, ["revenue"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def leakage_jobs(date_from: date, date_to: date, pillar: str | None, owner_id: int | None) -> pd.DataFrame:
    """Project jobs marked done/closed with no invoice attached — the
    bypass (an EC/super_admin/admin invoice override, or simply nobody
    having invoiced it yet) stays visible here until it's reconciled.
    est_value comes from that job's own costing sheet (what the work was
    priced at), when one was entered — never invented when it wasn't."""
    sql = f"""
        SELECT j.id, j.job_id AS job_code, j.title, j.status, j.category AS pillar,
               c.name AS client_name, s.name AS owner_name, j.completed_at,
               cl.total_price AS est_value
        FROM job j
        LEFT JOIN client c ON c.id = j.client_id
        LEFT JOIN staff s ON s.id = j.owner_id
        {_SC_JOIN}
        LEFT JOIN (SELECT job_id, SUM(price) AS total_price FROM job_costing_line GROUP BY job_id) cl
            ON cl.job_id = j.id
        WHERE j.hidden = FALSE AND j.status IN ('done', 'closed') AND j.invoice_id IS NULL
          AND {_PROJECT_ONLY} AND j.completed_at BETWEEN %s AND %s
    """
    params: list = [date_from, date_to]
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " ORDER BY j.completed_at DESC NULLS LAST"
    rows = query(sql, tuple(params))
    cols = ["id", "job_code", "title", "status", "pillar", "client_name", "owner_name", "completed_at", "est_value"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return _floatify(df, ["est_value"])


def leakage_summary(leaked: pd.DataFrame) -> dict:
    """count always meaningful; amount is None (never 0) when not one of
    the leaked jobs has a costing price on file — the KPI tile falls back
    to the count alone rather than implying "no money at risk."""
    priced = leaked["est_value"].dropna()
    return {
        "count": len(leaked),
        "amount": float(priced.sum()) if len(priced) else None,
        "priced_count": len(priced),
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def receivables_detail(pillar: str | None, owner_id: int | None) -> pd.DataFrame:
    """Every approved-but-unpaid invoice line, scoped to pillar/owner at
    the line's own job — so a multi-job invoice only contributes the lines
    that actually match the filter, not the whole invoice."""
    sql = f"""
        SELECT i.id AS invoice_id, i.invoice_code, c.name AS client_name, i.invoice_date,
               il.amount, (CURRENT_DATE - i.invoice_date) AS age_days
        FROM invoice i
        JOIN client c ON c.id = i.client_id
        JOIN invoice_line il ON il.invoice_id = i.id
        JOIN job j ON j.id = il.job_id
        {_SC_JOIN}
        WHERE i.status = 'approved' AND {_PROJECT_ONLY}
    """
    params: list = []
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " ORDER BY i.invoice_date ASC"
    rows = query(sql, tuple(params))
    cols = ["invoice_id", "invoice_code", "client_name", "invoice_date", "amount", "age_days"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return _floatify(df, ["amount", "age_days"])


def receivables_aging_summary(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(columns=["bucket", "amount", "count"])

    def bucket_of(age: int) -> str:
        if age <= 30:
            return "0-30 days"
        if age <= 60:
            return "30-60 days"
        return "60+ days"

    d = detail.copy()
    d["bucket"] = d["age_days"].apply(bucket_of)
    out = d.groupby("bucket", as_index=False)["amount"].agg(amount="sum", count="count")
    order = {"0-30 days": 0, "30-60 days": 1, "60+ days": 2}
    out["_order"] = out["bucket"].map(order)
    return out.sort_values("_order").drop(columns="_order").reset_index(drop=True)


# ---------------------------------------------------------------------------
# JOBS
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def open_jobs_by_status(pillar: str | None, owner_id: int | None) -> pd.DataFrame:
    sql = f"""
        SELECT j.status, COUNT(*) AS n
        FROM job j
        {_SC_JOIN}
        WHERE j.hidden = FALSE AND {_PROJECT_ONLY} AND j.status IN ('new', 'in_progress', 'blocked', 'done')
    """
    params: list = []
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " GROUP BY 1"
    rows = query(sql, tuple(params))
    return pd.DataFrame(rows, columns=["status", "n"]) if rows else pd.DataFrame(columns=["status", "n"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def cycle_time_by_pillar(date_from: date, date_to: date, owner_id: int | None) -> pd.DataFrame:
    sql = f"""
        SELECT j.category AS pillar,
               AVG(EXTRACT(EPOCH FROM (j.completed_at - j.created_at)) / 86400.0) AS avg_days,
               COUNT(*) AS n
        FROM job j
        {_SC_JOIN}
        WHERE j.hidden = FALSE AND {_PROJECT_ONLY}
          AND j.completed_at IS NOT NULL AND j.completed_at BETWEEN %s AND %s
    """
    params: list = [date_from, date_to]
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " GROUP BY 1 ORDER BY 2 DESC"
    rows = query(sql, tuple(params))
    cols = ["pillar", "avg_days", "n"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return _floatify(df, ["avg_days"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def cycle_time_trend(date_from: date, date_to: date, pillar: str | None, owner_id: int | None) -> pd.DataFrame:
    sql = f"""
        SELECT date_trunc('month', j.completed_at)::date AS month, j.category AS pillar,
               AVG(EXTRACT(EPOCH FROM (j.completed_at - j.created_at)) / 86400.0) AS avg_days
        FROM job j
        {_SC_JOIN}
        WHERE j.hidden = FALSE AND {_PROJECT_ONLY}
          AND j.completed_at IS NOT NULL AND j.completed_at BETWEEN %s AND %s
    """
    params: list = [date_from, date_to]
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " GROUP BY 1, 2 ORDER BY 1"
    rows = query(sql, tuple(params))
    cols = ["month", "pillar", "avg_days"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return _floatify(df, ["avg_days"])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def stalled_jobs(pillar: str | None, owner_id: int | None, stall_days: int = 3) -> pd.DataFrame:
    sql = f"""
        SELECT j.id, j.job_id AS job_code, j.title, j.status, j.category AS pillar,
               c.name AS client_name, s.name AS owner_name, j.status_changed_at,
               EXTRACT(EPOCH FROM (now() - j.status_changed_at)) / 86400.0 AS age_days
        FROM job j
        LEFT JOIN client c ON c.id = j.client_id
        LEFT JOIN staff s ON s.id = j.owner_id
        {_SC_JOIN}
        WHERE j.hidden = FALSE AND {_PROJECT_ONLY}
          AND j.status IN ('new', 'in_progress', 'blocked')
          AND j.status_changed_at < now() - (%s * interval '1 day')
    """
    params: list = [stall_days]
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    sql += " ORDER BY j.status_changed_at ASC"
    rows = query(sql, tuple(params))
    cols = ["id", "job_code", "title", "status", "pillar", "client_name", "owner_name", "status_changed_at", "age_days"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return _floatify(df, ["age_days"])


# ---------------------------------------------------------------------------
# PEOPLE
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def owner_performance(date_from: date, date_to: date, pillar: str | None) -> pd.DataFrame:
    """Per-owner: open jobs right now, jobs completed in the selected
    period, their average cycle time in that period, and their reopened
    rate — paired deliberately so a fast owner's number means nothing on
    its own; see reopened detection note below.

    A job counts as reopened if it has a completed_at (it was marked done
    at some point) but its current status isn't done/closed/dismissed —
    completed_at is set once and never cleared on a later transition (see
    job's own schema comment), so this is the one signal already in the
    data that survives a job leaving done and coming back, with no new
    tracking table needed."""
    sql = f"""
        SELECT s.id AS owner_id, s.name AS owner_name,
               COUNT(*) FILTER (WHERE j.status IN ('new', 'in_progress', 'blocked')) AS open_jobs,
               COUNT(*) FILTER (WHERE j.completed_at BETWEEN %s AND %s) AS completed_this_period,
               AVG(EXTRACT(EPOCH FROM (j.completed_at - j.created_at)) / 86400.0)
                   FILTER (WHERE j.completed_at BETWEEN %s AND %s) AS avg_cycle_days,
               COUNT(*) FILTER (WHERE j.completed_at IS NOT NULL) AS ever_completed,
               COUNT(*) FILTER (
                   WHERE j.completed_at IS NOT NULL AND j.status NOT IN ('done', 'closed', 'dismissed')
               ) AS reopened
        FROM staff s
        JOIN job j ON j.owner_id = s.id
        {_SC_JOIN}
        WHERE j.hidden = FALSE AND {_PROJECT_ONLY}
    """
    params: list = [date_from, date_to, date_from, date_to]
    if pillar:
        sql += " AND j.category = %s"
        params.append(pillar)
    sql += " GROUP BY s.id, s.name HAVING COUNT(*) > 0 ORDER BY s.name"
    rows = query(sql, tuple(params))
    cols = ["owner_id", "owner_name", "open_jobs", "completed_this_period", "avg_cycle_days", "ever_completed", "reopened"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    df = _floatify(df, ["avg_cycle_days"])
    if not df.empty:
        df["reopened_rate"] = df.apply(
            lambda r: (r["reopened"] / r["ever_completed"] * 100) if r["ever_completed"] else None, axis=1,
        )
    return df
