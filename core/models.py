"""Data access layer. All business rules and queries live here — views call
these functions and never write SQL themselves."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2

from core.constants import STATUS_CLOSED, STATUS_DISMISSED, STATUS_DONE, STATUS_NEW, STATUS_IN_PROGRESS, RISK_RED, RISK_AMBER, RISK_GREEN, RISK_GREY
from core.db import execute, execute_returning, query, query_one

STALL_HOURS = 48
DUE_SOON_DAYS = 3


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
def list_clients(active_only: bool = False) -> list:
    sql = "SELECT * FROM client"
    if active_only:
        sql += " WHERE status = 'active'"
    sql += " ORDER BY name"
    return query(sql)


def get_client(client_id: int):
    return query_one("SELECT * FROM client WHERE id = %s", (client_id,))


def list_staff(role: str | None = None, active_only: bool = True) -> list:
    sql = "SELECT * FROM staff WHERE 1=1"
    params = []
    if active_only:
        sql += " AND active = TRUE"
    if role:
        sql += " AND role = %s"
        params.append(role)
    sql += " ORDER BY name"
    return query(sql, tuple(params))


def list_service_catalogue() -> list:
    return query("SELECT * FROM service_catalogue WHERE active = TRUE ORDER BY sort_order")


def get_service(code: str):
    return query_one("SELECT * FROM service_catalogue WHERE code = %s", (code,))


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------
def _temp_code(prefix: str) -> str:
    return f"{prefix}-TMP-{uuid.uuid4().hex[:10]}"


def create_job(
    *,
    client_id: int,
    category: str,
    service_type: str | None,
    title: str,
    description: str | None,
    owner_id: int,
    source: str,
    created_by: int,
    sla_date: date | None,
    attributes: dict | None = None,
    waiting_on_client: str | None = None,
) -> dict:
    row = execute_returning(
        """
        INSERT INTO job (job_id, client_id, category, service_type, title, description,
                          owner_id, status, source, created_by, sla_date, waiting_on_client)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'new', %s, %s, %s, %s)
        RETURNING id, job_id
        """,
        (
            _temp_code("JOB"), client_id, category, service_type, title, description,
            owner_id, source, created_by, sla_date, waiting_on_client,
        ),
    )
    job_id_human = f"JOB-{date.today().year}-{row['id']:04d}"
    execute("UPDATE job SET job_id = %s WHERE id = %s", (job_id_human, row["id"]))

    if attributes:
        execute(
            "INSERT INTO job_extension (job_id, attributes) VALUES (%s, %s::jsonb) "
            "ON CONFLICT (job_id) DO UPDATE SET attributes = EXCLUDED.attributes",
            (row["id"], _json(attributes)),
        )

    return get_job(row["id"])


def create_dismissed_job(
    *,
    client_id: int | None,
    title: str,
    description: str | None,
    source: str,
    created_by: int,
    dismissed_reason: str,
) -> dict:
    row = execute_returning(
        """
        INSERT INTO job (job_id, client_id, category, service_type, title, description,
                          owner_id, status, source, created_by, dismissed_reason)
        VALUES (%s, %s, 'front_office', NULL, %s, %s, %s, 'dismissed', %s, %s, %s)
        RETURNING id
        """,
        (_temp_code("JOB"), client_id, title, description, created_by, source, created_by, dismissed_reason),
    )
    job_id_human = f"JOB-{date.today().year}-{row['id']:04d}"
    execute("UPDATE job SET job_id = %s WHERE id = %s", (job_id_human, row["id"]))
    return get_job(row["id"])


def _json(value) -> str:
    import json

    return json.dumps(value)


# ---------------------------------------------------------------------------
# Job reads
# ---------------------------------------------------------------------------
_JOB_SELECT = """
    SELECT j.*, c.name AS client_name, s.name AS owner_name,
           sc.name AS service_name, sc.pillar AS service_pillar,
           b.job_id AS blocked_by_job_code, b.status AS blocked_by_status, b.title AS blocked_by_title,
           i.invoice_code AS invoice_code, i.status AS invoice_status
    FROM job j
    LEFT JOIN client c ON c.id = j.client_id
    LEFT JOIN staff s ON s.id = j.owner_id
    LEFT JOIN service_catalogue sc ON sc.code = j.service_type
    LEFT JOIN job b ON b.id = j.blocked_by
    LEFT JOIN invoice i ON i.id = j.invoice_id
"""


def get_job(job_pk: int):
    return query_one(_JOB_SELECT + " WHERE j.id = %s", (job_pk,))


def get_job_by_job_id(job_id: str):
    return query_one(_JOB_SELECT + " WHERE j.job_id = %s", (job_id,))


def list_jobs(
    *,
    status: str | None = None,
    statuses: list | None = None,
    owner_id: int | None = None,
    client_id: int | None = None,
    category: str | None = None,
    exclude_dismissed: bool = False,
    search: str | None = None,
) -> list:
    sql = _JOB_SELECT + " WHERE 1=1"
    params: list = []
    if status:
        sql += " AND j.status = %s"
        params.append(status)
    if statuses:
        sql += " AND j.status = ANY(%s)"
        params.append(statuses)
    if owner_id:
        sql += " AND j.owner_id = %s"
        params.append(owner_id)
    if client_id:
        sql += " AND j.client_id = %s"
        params.append(client_id)
    if category:
        sql += " AND j.category = %s"
        params.append(category)
    if exclude_dismissed:
        sql += " AND j.status <> 'dismissed'"
    if search:
        sql += " AND (j.title ILIKE %s OR j.job_id ILIKE %s OR c.name ILIKE %s)"
        like = f"%{search}%"
        params += [like, like, like]
    sql += " ORDER BY j.created_at DESC"
    return query(sql, tuple(params))


def list_jobs_for_invoice(invoice_id: int) -> list:
    return query(_JOB_SELECT + " WHERE j.invoice_id = %s ORDER BY j.created_at", (invoice_id,))


def list_jobs_awaiting_invoice(client_id: int | None = None) -> list:
    sql = _JOB_SELECT + " WHERE j.status = 'done' AND j.invoice_id IS NULL"
    params: list = []
    if client_id:
        sql += " AND j.client_id = %s"
        params.append(client_id)
    sql += " ORDER BY j.status_changed_at"
    return query(sql, tuple(params))


def get_job_extension(job_pk: int) -> dict:
    row = query_one("SELECT attributes FROM job_extension WHERE job_id = %s", (job_pk,))
    return row["attributes"] if row else {}


# ---------------------------------------------------------------------------
# Job status changes
# ---------------------------------------------------------------------------
class JobRuleError(Exception):
    pass


def set_status(job_pk: int, new_status: str, reason: str | None = None) -> None:
    try:
        execute(
            "UPDATE job SET status = %s, status_reason = %s WHERE id = %s",
            (new_status, reason, job_pk),
        )
    except psycopg2.errors.RaiseException as e:
        raise JobRuleError(str(e).split("\n")[0]) from e


def set_blocked_by(job_pk: int, blocked_by_pk: int | None) -> None:
    try:
        execute("UPDATE job SET blocked_by = %s WHERE id = %s", (blocked_by_pk, job_pk))
    except psycopg2.errors.RaiseException as e:
        raise JobRuleError(str(e).split("\n")[0]) from e


def update_job_fields(job_pk: int, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [job_pk]
    try:
        execute(f"UPDATE job SET {set_clause} WHERE id = %s", tuple(params))
    except psycopg2.errors.RaiseException as e:
        raise JobRuleError(str(e).split("\n")[0]) from e


def is_actually_blocked(job: dict) -> bool:
    """True if a dependency is genuinely unresolved right now — distinct from
    the stored `status`, which may still read 'blocked' after the blocker
    resolves until someone re-saves the job."""
    return job.get("blocked_by") is not None and job.get("blocked_by_status") not in (STATUS_DONE, STATUS_CLOSED)


# ---------------------------------------------------------------------------
# Invoicing — admin creates (submits for approval); principal approves.
# ---------------------------------------------------------------------------
class InvoiceRuleError(Exception):
    pass


_INVOICE_SELECT = """
    SELECT i.*, c.name AS client_name,
           cb.name AS created_by_name, ab.name AS approved_by_name
    FROM invoice i
    JOIN client c ON c.id = i.client_id
    LEFT JOIN staff cb ON cb.id = i.created_by
    LEFT JOIN staff ab ON ab.id = i.approved_by
"""


def create_invoice(client_id: int, invoice_code: str, created_by: int) -> dict:
    """Admin-only: submit a new invoice for the principal's approval."""
    try:
        row = execute_returning(
            "INSERT INTO invoice (invoice_code, client_id, status, created_by) "
            "VALUES (%s, %s, 'pending_approval', %s) RETURNING id",
            (invoice_code, client_id, created_by),
        )
    except psycopg2.errors.UniqueViolation as e:
        raise InvoiceRuleError(f"Invoice code '{invoice_code}' is already in use.") from e
    return get_invoice(row["id"])


def approve_invoice(invoice_id: int, approved_by: int) -> None:
    """Principal-only: approve an invoice the admin submitted."""
    execute(
        "UPDATE invoice SET status = 'approved', approved_by = %s, approved_at = now() WHERE id = %s",
        (approved_by, invoice_id),
    )


def get_invoice(invoice_id: int):
    return query_one(_INVOICE_SELECT + " WHERE i.id = %s", (invoice_id,))


def list_invoices(client_id: int | None = None, status: str | None = None) -> list:
    sql = _INVOICE_SELECT + " WHERE 1=1"
    params: list = []
    if client_id:
        sql += " AND i.client_id = %s"
        params.append(client_id)
    if status:
        sql += " AND i.status = %s"
        params.append(status)
    sql += " ORDER BY i.created_at DESC"
    return query(sql, tuple(params))


def attach_job_to_invoice(job_pk: int, invoice_id: int) -> None:
    execute("UPDATE job SET invoice_id = %s WHERE id = %s", (invoice_id, job_pk))


def detach_job_from_invoice(job_pk: int) -> None:
    execute("UPDATE job SET invoice_id = %s WHERE id = %s", (None, job_pk))


def set_invoice_status(invoice_id: int, status: str) -> None:
    """Admin bookkeeping step (currently just 'paid') — approval has its own
    function above since it also records who approved it and when."""
    extra = ", paid_at = now()" if status == "paid" else ""
    execute(f"UPDATE invoice SET status = %s{extra} WHERE id = %s", (status, invoice_id))


def close_job(job_pk: int) -> None:
    """Mark a job closed. Requires an approved (or paid) invoice — enforced by the DB."""
    set_status(job_pk, STATUS_CLOSED)


# ---------------------------------------------------------------------------
# Per-job expense log — admin adds, principal can view. A running list, not
# accounting.
# ---------------------------------------------------------------------------
def add_job_expense(job_id: int, description: str, amount: float, expense_date: date, created_by: int) -> None:
    execute(
        "INSERT INTO job_expense (job_id, description, amount, expense_date, created_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (job_id, description, amount, expense_date, created_by),
    )


def list_job_expenses(job_id: int) -> list:
    return query(
        """
        SELECT e.*, s.name AS created_by_name
        FROM job_expense e
        LEFT JOIN staff s ON s.id = e.created_by
        WHERE e.job_id = %s
        ORDER BY e.expense_date DESC, e.id DESC
        """,
        (job_id,),
    )


# ---------------------------------------------------------------------------
# Per-job comment thread — lightweight, no edits or threading.
# ---------------------------------------------------------------------------
def add_job_comment(job_id: int, author_id: int, body: str) -> None:
    execute(
        "INSERT INTO job_comment (job_id, author_id, body) VALUES (%s, %s, %s)",
        (job_id, author_id, body),
    )


def list_job_comments(job_id: int) -> list:
    return query(
        """
        SELECT c.*, s.name AS author_name
        FROM job_comment c
        JOIN staff s ON s.id = c.author_id
        WHERE c.job_id = %s
        ORDER BY c.created_at DESC
        """,
        (job_id,),
    )


# ---------------------------------------------------------------------------
# Risk / urgency colour + stall flag — computed, not stored, so the rule
# lives in one place and can never drift screen to screen.
# ---------------------------------------------------------------------------
def compute_risk(job: dict) -> str:
    status = job["status"]
    if status in (STATUS_DONE, STATUS_CLOSED):
        return RISK_GREEN
    if status == STATUS_DISMISSED:
        return RISK_GREY
    if is_actually_blocked(job):
        return RISK_AMBER
    sla = job.get("sla_date")
    if sla:
        today = date.today()
        if sla < today:
            return RISK_RED
        if sla <= today + timedelta(days=DUE_SOON_DAYS):
            return RISK_AMBER
    return RISK_GREY


def is_stalled(job: dict) -> bool:
    if job["status"] not in (STATUS_NEW, STATUS_IN_PROGRESS, "blocked"):
        return False
    changed = job.get("status_changed_at")
    if not changed:
        return False
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - changed > timedelta(hours=STALL_HOURS)


# ---------------------------------------------------------------------------
# Firm-wide summary (principal overview)
# ---------------------------------------------------------------------------
def firm_summary() -> dict:
    jobs = list_jobs(exclude_dismissed=True)
    by_status: dict = {}
    stalled = 0
    done_unbilled = 0
    red_flags = 0
    for j in jobs:
        by_status[j["status"]] = by_status.get(j["status"], 0) + 1
        if is_stalled(j):
            stalled += 1
        if j["status"] == STATUS_DONE and j["invoice_id"] is None:
            done_unbilled += 1
        if compute_risk(j) == RISK_RED:
            red_flags += 1
    return {
        "total": len(jobs),
        "by_status": by_status,
        "stalled": stalled,
        "done_unbilled": done_unbilled,
        "red_flags": red_flags,
    }
