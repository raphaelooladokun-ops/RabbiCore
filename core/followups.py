"""Follow-ups aggregates — a single view of what still needs chasing across
every job, for Super Admin. One row per open job by default (current status
+ latest comment), never a dump of every comment ever; the "full comment
log" toggle switches to one row per comment for a complete history export.

The latest comment per job is picked with a ROW_NUMBER() window function in
Neon — never by pulling every comment into Python and sorting there — and
every query is wrapped in st.cache_data so flipping a toggle or filter
doesn't re-hit Postgres more than necessary. Read-only: nothing here writes.

"Open" excludes done/closed (toggleable via include_completed) and always
excludes a dismissed or hidden job — neither has follow-up work left to
chase, the same invariant every other view in the app already holds hidden
jobs to."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import tz
from core.db import query

CACHE_TTL = 300

STALE_DAYS = 3

_BASE_JOIN = """
    FROM job j
    LEFT JOIN client c ON c.id = j.client_id
    LEFT JOIN staff s ON s.id = j.owner_id
    LEFT JOIN service_catalogue sc ON sc.code = j.service_type
"""
_BASE_WHERE = "WHERE j.hidden = FALSE AND j.status <> 'dismissed'"


def _apply_filters(sql: str, params: list, include_completed: bool, owner_id: int | None, service_code: str | None) -> str:
    if not include_completed:
        sql += " AND j.status NOT IN ('done', 'closed')"
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    if service_code:
        sql += " AND j.service_type = %s"
        params.append(service_code)
    return sql


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def job_summary(include_completed: bool, owner_id: int | None, service_code: str | None) -> pd.DataFrame:
    """One row per job: current status, latest comment, and whether it has
    an open EC action point. The latest comment comes from a ranked CTE
    (ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY created_at DESC)),
    joined back to job — the aggregation Neon does, not Python."""
    sql = f"""
        WITH ranked AS (
            SELECT job_id, body, created_at,
                   ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY created_at DESC) AS rn
            FROM job_comment
        )
        SELECT j.id, j.job_id AS job_code, j.status, j.owner_id,
               j.status_changed_at, c.name AS client_name, s.name AS owner_name,
               COALESCE(sc.name, j.service_type) AS service_name,
               r.body AS last_comment, r.created_at AS last_comment_at,
               EXISTS (
                   SELECT 1 FROM job_comment ec WHERE ec.job_id = j.id
                   AND ec.is_ec_action_point = TRUE AND ec.action_resolved = FALSE
               ) AS has_open_ec
        {_BASE_JOIN}
        LEFT JOIN ranked r ON r.job_id = j.id AND r.rn = 1
        {_BASE_WHERE}
    """
    params: list = []
    sql = _apply_filters(sql, params, include_completed, owner_id, service_code)
    rows = query(sql, tuple(params))
    cols = [
        "id", "job_code", "status", "owner_id", "status_changed_at",
        "client_name", "owner_name", "service_name", "last_comment", "last_comment_at", "has_open_ec",
    ]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    return _with_activity(df, latest_col="last_comment_at")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def comment_log(include_completed: bool, owner_id: int | None, service_code: str | None) -> pd.DataFrame:
    """One row per comment, oldest first within each job — the "full
    comment log" export. Each row still carries the job's own current
    status/flag/days-since-activity (the same figures job_summary shows),
    not a per-comment recomputation, so a job's whole block of rows agrees
    with itself."""
    sql = f"""
        SELECT j.id, j.job_id AS job_code, j.status, j.owner_id,
               j.status_changed_at, c.name AS client_name, s.name AS owner_name,
               COALESCE(sc.name, j.service_type) AS service_name,
               jc.body AS comment_body, jc.created_at AS comment_at, au.name AS comment_author,
               EXISTS (
                   SELECT 1 FROM job_comment ec WHERE ec.job_id = j.id
                   AND ec.is_ec_action_point = TRUE AND ec.action_resolved = FALSE
               ) AS has_open_ec
        {_BASE_JOIN}
        JOIN job_comment jc ON jc.job_id = j.id
        LEFT JOIN staff au ON au.id = jc.author_id
        {_BASE_WHERE}
    """
    params: list = []
    sql = _apply_filters(sql, params, include_completed, owner_id, service_code)
    sql += " ORDER BY j.id, jc.created_at ASC"
    rows = query(sql, tuple(params))
    cols = [
        "id", "job_code", "status", "owner_id", "status_changed_at",
        "client_name", "owner_name", "service_name", "comment_body", "comment_at", "comment_author",
        "has_open_ec",
    ]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    if df.empty:
        return _with_activity(df, latest_col="comment_at")
    df["last_comment_at"] = df.groupby("id")["comment_at"].transform("max")
    return _with_activity(df, latest_col="last_comment_at")


def _with_activity(df: pd.DataFrame, latest_col: str) -> pd.DataFrame:
    """Adds last_activity_at (the later of status_changed_at and the
    latest comment), days_since_activity (calendar days in Lagos), and
    flag ("EC", "Stale", "EC, Stale" or "") — the one shared definition
    every view and the CSV export reads from, so they can't drift apart."""
    if df.empty:
        df["last_activity_at"] = pd.Series(dtype="object")
        df["days_since_activity"] = pd.Series(dtype="float")
        df["flag"] = pd.Series(dtype="object")
        return df

    def _latest(row):
        candidates = [row["status_changed_at"]]
        if pd.notna(row[latest_col]):
            candidates.append(row[latest_col])
        return max(candidates)

    today = tz.today_lagos()
    df = df.copy()
    df["last_activity_at"] = df.apply(_latest, axis=1)
    df["days_since_activity"] = df["last_activity_at"].map(lambda dt: (today - tz.to_lagos_date(dt)).days)
    df["flag"] = df.apply(lambda r: _flag_label(r["has_open_ec"], r["days_since_activity"]), axis=1)
    return df


def _flag_label(has_open_ec: bool, days_since: int) -> str:
    stale = days_since > STALE_DAYS
    if has_open_ec and stale:
        return "EC, Stale"
    if has_open_ec:
        return "EC"
    if stale:
        return "Stale"
    return ""


def sort_by_flag_then_staleness(df: pd.DataFrame) -> pd.DataFrame:
    """Flagged rows first; within each group, the longest-unattended job
    first (oldest last-activity on top). A job's own id is the final
    tiebreaker so a job's full comment block stays contiguous in full-log
    mode rather than interleaving with another job's rows."""
    if df.empty:
        return df
    out = df.copy()
    out["_is_flagged"] = out["flag"] != ""
    # mergesort is stable, so rows that tie on all three keys (every
    # comment belonging to the same job, in full-log mode) keep the
    # chronological order the query already gave them.
    out = out.sort_values(
        ["_is_flagged", "days_since_activity", "id"], ascending=[False, False, True], kind="mergesort",
    ).drop(columns="_is_flagged")
    return out
