"""Data access layer. All business rules and queries live here — views call
these functions and never write SQL themselves."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2

from core.constants import (
    RISK_AMBER,
    RISK_GREEN,
    RISK_GREY,
    RISK_RED,
    ROLE_SPECIALIST,
    STATUS_BLOCKED,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_NEW,
)
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

    job = get_job(row["id"])
    owner = query_one("SELECT role FROM staff WHERE id = %s", (owner_id,))
    if owner and owner["role"] == ROLE_SPECIALIST:
        _create_notification(
            owner_id, "assigned", "job", job["id"],
            f"New job {job['job_id']}: {job['client_name'] or '—'} — {job['service_name'] or job['title']}",
        )
    return job


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
           i.invoice_code AS invoice_code, i.status AS invoice_status,
           (SELECT COUNT(*) FROM job d WHERE d.blocked_by = j.id AND d.status = 'blocked') AS blocking_count
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


def list_done_unbilled_jobs() -> list:
    """Every done, not-yet-invoiced job across all clients — the Billing
    page's ready-to-invoice register."""
    return query(_JOB_SELECT + " WHERE j.status = 'done' AND j.invoice_id IS NULL ORDER BY j.status_changed_at")


def list_jobs_available_for_invoice(client_id: int, invoice_id: int | None = None) -> list:
    """Done jobs for this client not yet on any invoice, plus — when revising
    an existing invoice — the jobs already on THAT invoice, so the admin can
    review and adjust its composition."""
    sql = _JOB_SELECT + " WHERE j.client_id = %s AND j.status = 'done' AND (j.invoice_id IS NULL"
    params: list = [client_id]
    if invoice_id:
        sql += " OR j.invoice_id = %s"
        params.append(invoice_id)
    sql += ") ORDER BY j.status_changed_at"
    return query(sql, tuple(params))


def get_job_extension(job_pk: int) -> dict:
    row = query_one("SELECT attributes FROM job_extension WHERE job_id = %s", (job_pk,))
    return row["attributes"] if row else {}


def list_blocked_dependents(job_pk: int) -> list:
    """Jobs currently blocked_by this one — used to surface, on the blocking
    job itself, that it's holding up someone else's work."""
    return query(_JOB_SELECT + " WHERE j.blocked_by = %s AND j.status = 'blocked'", (job_pk,))


# ---------------------------------------------------------------------------
# Job status changes
# ---------------------------------------------------------------------------
class JobRuleError(Exception):
    pass


def set_status(job_pk: int, new_status: str, reason: str | None = None, actor_id: int | None = None) -> None:
    try:
        execute(
            "UPDATE job SET status = %s, status_reason = %s WHERE id = %s",
            (new_status, reason, job_pk),
        )
    except psycopg2.errors.RaiseException as e:
        raise JobRuleError(str(e).split("\n")[0]) from e

    if new_status in (STATUS_DONE, STATUS_BLOCKED):
        _notify_status_change(job_pk, new_status, reason, actor_id)
    if new_status in (STATUS_DONE, STATUS_CLOSED):
        _unblock_dependents(job_pk, actor_id)


def set_blocked_by(job_pk: int, blocked_by_pk: int | None, actor_id: int | None = None) -> None:
    try:
        execute("UPDATE job SET blocked_by = %s WHERE id = %s", (blocked_by_pk, job_pk))
    except psycopg2.errors.RaiseException as e:
        raise JobRuleError(str(e).split("\n")[0]) from e
    if blocked_by_pk:
        _notify_blocker_owner(job_pk, blocked_by_pk, actor_id)


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
# Invoicing — a real invoice: client, line items (one per job), amounts,
# code, date. Admin builds and submits it; principal reviews it as a
# document and can edit amounts/descriptions, approve, or reject it back
# to admin with a reason. Only admin can change which jobs are on it.
# ---------------------------------------------------------------------------
class InvoiceRuleError(Exception):
    pass


_INVOICE_SELECT = """
    SELECT i.*, c.name AS client_name, c.contact_name AS client_contact_name,
           c.contact_email AS client_contact_email,
           cb.name AS created_by_name, ab.name AS approved_by_name, rb.name AS rejected_by_name
    FROM invoice i
    JOIN client c ON c.id = i.client_id
    LEFT JOIN staff cb ON cb.id = i.created_by
    LEFT JOIN staff ab ON ab.id = i.approved_by
    LEFT JOIN staff rb ON rb.id = i.rejected_by
"""


def create_invoice(client_id: int, invoice_code: str, invoice_date: date, created_by: int, lines: list) -> dict:
    """Admin-only: build and submit a new invoice for the principal's
    approval. `lines` is a list of {job_id, description, amount}."""
    try:
        row = execute_returning(
            "INSERT INTO invoice (invoice_code, client_id, status, created_by, invoice_date) "
            "VALUES (%s, %s, 'pending_approval', %s, %s) RETURNING id",
            (invoice_code, client_id, created_by, invoice_date),
        )
    except psycopg2.errors.UniqueViolation as e:
        raise InvoiceRuleError(f"Invoice code '{invoice_code}' is already in use.") from e
    invoice_id = row["id"]
    for line in lines:
        execute(
            "INSERT INTO invoice_line (invoice_id, job_id, description, amount) VALUES (%s, %s, %s, %s)",
            (invoice_id, line["job_id"], line["description"], line["amount"]),
        )
        execute("UPDATE job SET invoice_id = %s WHERE id = %s", (invoice_id, line["job_id"]))

    result = get_invoice(invoice_id)
    _notify_principals(
        "invoice_submitted", "invoice", invoice_id,
        f"Invoice {result['invoice_code']} submitted for approval — {result['client_name']}",
        exclude_staff_id=created_by,
    )
    return result


def revise_invoice(invoice_id: int, invoice_code: str, invoice_date: date, lines: list) -> dict:
    """Admin-only: rework a rejected invoice's jobs/line items/code/date and
    resubmit it. This is the only path that can change which jobs are on an
    invoice once it's been created."""
    old_job_ids = {r["job_id"] for r in query("SELECT job_id FROM invoice_line WHERE invoice_id = %s", (invoice_id,))}
    new_job_ids = {line["job_id"] for line in lines}

    for job_id in old_job_ids - new_job_ids:
        execute("UPDATE job SET invoice_id = NULL WHERE id = %s", (job_id,))

    execute("DELETE FROM invoice_line WHERE invoice_id = %s", (invoice_id,))
    for line in lines:
        execute(
            "INSERT INTO invoice_line (invoice_id, job_id, description, amount) VALUES (%s, %s, %s, %s)",
            (invoice_id, line["job_id"], line["description"], line["amount"]),
        )
        execute("UPDATE job SET invoice_id = %s WHERE id = %s", (invoice_id, line["job_id"]))

    try:
        execute(
            "UPDATE invoice SET invoice_code = %s, invoice_date = %s, status = 'pending_approval', "
            "rejection_reason = NULL, rejected_by = NULL, rejected_at = NULL WHERE id = %s",
            (invoice_code, invoice_date, invoice_id),
        )
    except psycopg2.errors.UniqueViolation as e:
        raise InvoiceRuleError(f"Invoice code '{invoice_code}' is already in use.") from e

    result = get_invoice(invoice_id)
    _notify_principals(
        "invoice_submitted", "invoice", invoice_id,
        f"Invoice {result['invoice_code']} resubmitted for approval — {result['client_name']}",
        exclude_staff_id=result["created_by"],
    )
    return result


def update_invoice_lines(invoice_id: int, lines: list) -> None:
    """Principal-only: edit descriptions/amounts on the EXISTING line items.
    Never inserts or deletes a line — that would change job composition,
    which stays admin-only."""
    for line in lines:
        execute(
            "UPDATE invoice_line SET description = %s, amount = %s WHERE id = %s AND invoice_id = %s",
            (line["description"], line["amount"], line["id"], invoice_id),
        )


def approve_invoice(invoice_id: int, approved_by: int) -> None:
    execute(
        "UPDATE invoice SET status = 'approved', approved_by = %s, approved_at = now() WHERE id = %s",
        (approved_by, invoice_id),
    )
    inv = get_invoice(invoice_id)
    if inv["created_by"] and inv["created_by"] != approved_by:
        _create_notification(
            inv["created_by"], "invoice_approved", "invoice", invoice_id,
            f"Invoice {inv['invoice_code']} approved — {inv['client_name']}",
        )


def reject_invoice(invoice_id: int, rejected_by: int, reason: str) -> None:
    execute(
        "UPDATE invoice SET status = 'rejected', rejected_by = %s, rejected_at = now(), rejection_reason = %s "
        "WHERE id = %s",
        (rejected_by, reason, invoice_id),
    )
    inv = get_invoice(invoice_id)
    if inv["created_by"] and inv["created_by"] != rejected_by:
        _create_notification(
            inv["created_by"], "invoice_rejected", "invoice", invoice_id,
            f"Invoice {inv['invoice_code']} rejected — {reason}",
        )


def set_invoice_status(invoice_id: int, status: str) -> None:
    """Admin bookkeeping step (currently just 'paid') — approval/rejection
    have their own functions above since they also record who and when."""
    extra = ", paid_at = now()" if status == "paid" else ""
    execute(f"UPDATE invoice SET status = %s{extra} WHERE id = %s", (status, invoice_id))


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


def list_invoice_lines(invoice_id: int) -> list:
    return query(
        """
        SELECT il.*, j.job_id AS job_code, j.title AS job_title
        FROM invoice_line il
        JOIN job j ON j.id = il.job_id
        WHERE il.invoice_id = %s
        ORDER BY il.id
        """,
        (invoice_id,),
    )


def invoice_total(invoice_id: int) -> float:
    row = query_one("SELECT COALESCE(SUM(amount), 0) AS total FROM invoice_line WHERE invoice_id = %s", (invoice_id,))
    return float(row["total"]) if row else 0.0


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
    if job.get("blocking_count", 0) > 0:
        return RISK_RED  # this job is holding up someone else's — surface it above everything
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


# ---------------------------------------------------------------------------
# Notifications — each role is alerted when something needs them, instead of
# having to discover it. One row per alert, linked to the job or invoice
# it's about. No push: the bell re-reads on every rerun, same as every other
# read in this app — see sync_sla_notifications() for the one case (a time
# condition, not a write) that needs its own dedupe.
# ---------------------------------------------------------------------------
def _create_notification(staff_id: int, kind: str, link_type: str, link_id: int, message: str) -> None:
    execute(
        "INSERT INTO notification (staff_id, kind, link_type, link_id, message) VALUES (%s, %s, %s, %s, %s)",
        (staff_id, kind, link_type, link_id, message),
    )


def _notify_principals(kind: str, link_type: str, link_id: int, message: str, exclude_staff_id: int | None = None) -> None:
    for p in list_staff(role="principal"):
        if p["id"] != exclude_staff_id:
            _create_notification(p["id"], kind, link_type, link_id, message)


def _notify_status_change(job_pk: int, new_status: str, reason: str | None, actor_id: int | None) -> None:
    job = query_one(
        "SELECT j.owner_id, j.job_id, j.title, c.name AS client_name FROM job j "
        "LEFT JOIN client c ON c.id = j.client_id WHERE j.id = %s",
        (job_pk,),
    )
    if not job:
        return
    label = "done" if new_status == STATUS_DONE else "blocked"
    reason_part = f" — {reason}" if reason else ""
    message = f"{job['job_id']} marked {label}: {job['client_name'] or '—'} — {job['title']}{reason_part}"
    if job["owner_id"] and job["owner_id"] != actor_id:
        _create_notification(job["owner_id"], f"status_{new_status}", "job", job_pk, message)
    _notify_principals(f"status_{new_status}", "job", job_pk, message, exclude_staff_id=actor_id)


def _unblock_dependents(blocker_pk: int, actor_id: int | None) -> None:
    """When a job the guard forced others to wait on finishes, move each
    waiting job back to in_progress automatically and tell its owner."""
    blocker = query_one("SELECT job_id FROM job WHERE id = %s", (blocker_pk,))
    dependents = query(
        "SELECT j.id, j.owner_id, c.name AS client_name, j.title FROM job j "
        "LEFT JOIN client c ON c.id = j.client_id WHERE j.blocked_by = %s AND j.status = %s",
        (blocker_pk, STATUS_BLOCKED),
    )
    for dep in dependents:
        execute("UPDATE job SET status = %s, status_reason = NULL WHERE id = %s", (STATUS_IN_PROGRESS, dep["id"]))
        if dep["owner_id"]:
            _create_notification(
                dep["owner_id"], "unblocked", "job", dep["id"],
                f"Unblocked: {blocker['job_id']} is done — {dep['client_name'] or '—'} — {dep['title']} can proceed",
            )


def _notify_blocker_owner(dependent_pk: int, blocker_pk: int, actor_id: int | None) -> None:
    """The moment a dependency is created, tell the blocking job's owner —
    even across modules/specialists — that their job is holding up another."""
    blocker = query_one("SELECT owner_id, job_id FROM job WHERE id = %s", (blocker_pk,))
    dependent = query_one(
        "SELECT j.job_id, j.title, c.name AS client_name FROM job j "
        "LEFT JOIN client c ON c.id = j.client_id WHERE j.id = %s",
        (dependent_pk,),
    )
    if blocker and blocker["owner_id"] and blocker["owner_id"] != actor_id:
        _create_notification(
            blocker["owner_id"], "blocking", "job", blocker_pk,
            f"Your job {blocker['job_id']} is blocking {dependent['job_id']} — "
            f"{dependent['client_name'] or '—'} — {dependent['title']}",
        )


def list_notifications(staff_id: int, limit: int = 20) -> list:
    return query(
        "SELECT * FROM notification WHERE staff_id = %s ORDER BY created_at DESC LIMIT %s",
        (staff_id, limit),
    )


def count_unread_notifications(staff_id: int) -> int:
    row = query_one("SELECT count(*) AS n FROM notification WHERE staff_id = %s AND NOT read", (staff_id,))
    return row["n"] if row else 0


def mark_notification_read(notification_id: int) -> None:
    execute("UPDATE notification SET read = TRUE WHERE id = %s", (notification_id,))


def mark_all_notifications_read(staff_id: int) -> None:
    execute("UPDATE notification SET read = TRUE WHERE staff_id = %s AND NOT read", (staff_id,))


def sync_sla_notifications() -> None:
    """One set-based sweep, safe to call often: flags jobs whose SLA is due
    soon or already past to their owner. Dedupes against itself (NOT EXISTS)
    so it doesn't re-notify for a condition it already flagged."""
    execute(
        """
        INSERT INTO notification (staff_id, kind, link_type, link_id, message)
        SELECT j.owner_id, 'sla_overdue', 'job', j.id,
               'Overdue: ' || COALESCE(c.name, 'No client') || ' — ' || j.title
        FROM job j
        LEFT JOIN client c ON c.id = j.client_id
        WHERE j.owner_id IS NOT NULL
          AND j.sla_date IS NOT NULL AND j.sla_date < CURRENT_DATE
          AND j.status NOT IN ('done', 'closed', 'dismissed')
          AND NOT EXISTS (
              SELECT 1 FROM notification n
              WHERE n.staff_id = j.owner_id AND n.kind = 'sla_overdue' AND n.link_type = 'job' AND n.link_id = j.id
          )
        """
    )
    execute(
        """
        INSERT INTO notification (staff_id, kind, link_type, link_id, message)
        SELECT j.owner_id, 'sla_due', 'job', j.id,
               'Due soon: ' || COALESCE(c.name, 'No client') || ' — ' || j.title
        FROM job j
        LEFT JOIN client c ON c.id = j.client_id
        WHERE j.owner_id IS NOT NULL
          AND j.sla_date IS NOT NULL
          AND j.sla_date >= CURRENT_DATE AND j.sla_date <= CURRENT_DATE + (%s * INTERVAL '1 day')
          AND j.status NOT IN ('done', 'closed', 'dismissed')
          AND NOT EXISTS (
              SELECT 1 FROM notification n
              WHERE n.staff_id = j.owner_id AND n.kind = 'sla_due' AND n.link_type = 'job' AND n.link_id = j.id
          )
        """,
        (DUE_SOON_DAYS,),
    )
