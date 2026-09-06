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
    ROLE_ADMIN,
    ROLE_PRINCIPAL,
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


def create_client(
    name: str,
    rc_number: str | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
) -> dict:
    """Log a new client inline (e.g. from the Capture flow) without a
    separate admin screen — the same 'active' client any other query sees."""
    row = execute_returning(
        "INSERT INTO client (name, rc_number, contact_name, contact_email, contact_phone, status) "
        "VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id",
        (name, rc_number or None, contact_name or None, contact_email or None, contact_phone or None),
    )
    return get_client(row["id"])


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

    if service_type:
        materialize_job_documents(row["id"], service_type, attributes)

    job = get_job(row["id"])
    owner = query_one("SELECT role FROM staff WHERE id = %s", (owner_id,))
    if owner and owner["role"] == ROLE_SPECIALIST:
        create_notification(
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


def _resync_stale_blocked() -> None:
    """Safety net: a job stays showing 'blocked' until something re-saves it
    even after its blocker resolves (_unblock_dependents does that eagerly
    the moment the blocker is marked done/closed) — but if that ever gets
    missed, a job could be stuck reading 'blocked' forever with no dependency
    left to actually wait on. Self-heal on every read instead of trusting a
    single write path: idempotent, and a no-op once nothing is stale."""
    stale = query(
        """
        SELECT j.id, j.owner_id, j.title, c.name AS client_name, b.job_id AS blocker_job_id
        FROM job j
        JOIN job b ON b.id = j.blocked_by
        LEFT JOIN client c ON c.id = j.client_id
        LEFT JOIN job_extension je ON je.job_id = j.id
        WHERE j.status = 'blocked' AND b.status IN ('done', 'closed')
          AND COALESCE((je.attributes->>'linked_quota_job_id')::int, -1) IS DISTINCT FROM j.blocked_by
        """
    )
    for row in stale:
        execute(
            "UPDATE job SET status = %s, status_reason = NULL WHERE id = %s",
            (STATUS_IN_PROGRESS, row["id"]),
        )
        if row["owner_id"]:
            create_notification(
                row["owner_id"], "unblocked", "job", row["id"],
                f"Unblocked: {row['blocker_job_id']} is done — {row['client_name'] or '—'} — "
                f"{row['title']} can proceed",
            )


def get_job(job_pk: int):
    _resync_stale_blocked()
    return query_one(_JOB_SELECT + " WHERE j.id = %s", (job_pk,))


def get_job_by_job_id(job_id: str):
    _resync_stale_blocked()
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
    _resync_stale_blocked()
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


def list_unbilled_jobs() -> list:
    """Every not-yet-invoiced job across all clients, any status — Rabbi
    invoices up front, so a freshly logged job is exactly as invoiceable as
    a finished one. Newest first."""
    return query(
        _JOB_SELECT + " WHERE j.invoice_id IS NULL AND j.status <> 'dismissed' "
        "ORDER BY j.created_at DESC"
    )


def find_potential_duplicate(client_id: int | None, service_type: str | None):
    """The most recent still-open job for the same client + service, if any
    — surfaced as a 'this may already exist' warning before logging another
    one. Closed and dismissed jobs don't count as live duplicates."""
    if not client_id or not service_type:
        return None
    return query_one(
        _JOB_SELECT + " WHERE j.client_id = %s AND j.service_type = %s "
        "AND j.status NOT IN ('dismissed', 'closed') ORDER BY j.created_at DESC LIMIT 1",
        (client_id, service_type),
    )


def list_jobs_available_for_invoice(client_id: int, invoice_id: int | None = None) -> list:
    """Not-yet-invoiced jobs for this client, any status — Rabbi invoices up
    front, so most of these will still be 'new' — plus, when revising an
    existing invoice, the jobs already on THAT invoice, so the admin can
    review and adjust its composition."""
    sql = _JOB_SELECT + " WHERE j.client_id = %s AND j.status <> 'dismissed' AND (j.invoice_id IS NULL"
    params: list = [client_id]
    if invoice_id:
        sql += " OR j.invoice_id = %s"
        params.append(invoice_id)
    sql += ") ORDER BY j.status_changed_at"
    return query(sql, tuple(params))


def get_job_extension(job_pk: int) -> dict:
    row = query_one("SELECT attributes FROM job_extension WHERE job_id = %s", (job_pk,))
    return row["attributes"] if row else {}


def set_job_extension(job_pk: int, attributes: dict) -> None:
    """Replace a job's extension attributes wholesale — used both for
    capture-time Type-field values and for module-specific bookkeeping a
    module keeps in the same JSON (e.g. immigration's quota link)."""
    execute(
        "INSERT INTO job_extension (job_id, attributes) VALUES (%s, %s::jsonb) "
        "ON CONFLICT (job_id) DO UPDATE SET attributes = EXCLUDED.attributes",
        (job_pk, _json(attributes)),
    )


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


def can_start_work(job: dict) -> tuple[bool, str | None]:
    """Rabbi invoices up front: whether this job is allowed to move
    new -> in_progress right now, and if not, why. Mirrors the DB trigger's
    own rule so the UI can explain the block instead of just failing on
    submit. A principal's start_override always lifts the gate."""
    if job.get("start_override_by"):
        return True, None
    if not job.get("invoice_id"):
        return False, "This job hasn't been invoiced yet."
    if job.get("invoice_status") not in ("approved", "paid"):
        return False, "Invoiced, but waiting on the principal to approve the invoice."
    return True, None


def set_start_override(job_pk: int, principal_id: int, reason: str) -> None:
    """Principal-only: let a specialist start work on this job even though it
    hasn't been invoiced yet, or the invoice isn't approved yet. Recorded
    with who/when/why for audit — the specialist still has to click Start
    work themselves; this only lifts the gate."""
    execute(
        "UPDATE job SET start_override_by = %s, start_override_at = now(), start_override_reason = %s WHERE id = %s",
        (principal_id, reason, job_pk),
    )


# ---------------------------------------------------------------------------
# Invoicing — a real invoice: client, line items (one per job), amounts,
# code, date. Admin builds and submits it; principal reviews it as a
# document and can edit amounts/descriptions, approve, or reject it back
# to admin with a reason. Only admin can change which jobs are on it.
# ---------------------------------------------------------------------------
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


def create_invoice(client_id: int, invoice_date: date, created_by: int, lines: list) -> dict:
    """Admin-only: build and submit a new invoice for the principal's
    approval. `lines` is a list of {job_id, description, amount}. The
    invoice code is auto-generated (INV-{year}-{id:04d}) — never typed."""
    row = execute_returning(
        "INSERT INTO invoice (invoice_code, client_id, status, created_by, invoice_date) "
        "VALUES (%s, %s, 'pending_approval', %s, %s) RETURNING id",
        (_temp_code("INV"), client_id, created_by, invoice_date),
    )
    invoice_id = row["id"]
    invoice_code = f"INV-{date.today().year}-{invoice_id:04d}"
    execute("UPDATE invoice SET invoice_code = %s WHERE id = %s", (invoice_code, invoice_id))
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


def revise_invoice(invoice_id: int, invoice_date: date, lines: list) -> dict:
    """Admin-only: rework a rejected invoice's jobs/line items/date and
    resubmit it. This is the only path that can change which jobs are on an
    invoice once it's been created. The invoice code is kept as-is — a
    revision is still the same invoice, just corrected."""
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

    execute(
        "UPDATE invoice SET invoice_date = %s, status = 'pending_approval', "
        "rejection_reason = NULL, rejected_by = NULL, rejected_at = NULL WHERE id = %s",
        (invoice_date, invoice_id),
    )

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
        create_notification(
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
        create_notification(
            inv["created_by"], "invoice_rejected", "invoice", invoice_id,
            f"Invoice {inv['invoice_code']} rejected — {reason}",
        )


def mark_invoice_sent(invoice_id: int, sent_by: int) -> None:
    """Admin bookkeeping: records that the (already-approved) invoice has
    actually been sent to the client. Orthogonal to status — sent-ness
    doesn't gate approval or payment."""
    execute("UPDATE invoice SET sent_at = now(), sent_by = %s WHERE id = %s", (sent_by, invoice_id))


class InvoicePaymentError(Exception):
    pass


def mark_invoice_paid(invoice_id: int, payment_reference: str, payment_date: date) -> None:
    """Admin-only: close out an invoice as paid. A payment reference and
    date are required — this is the actual accounting record of the
    payment, not just a status flip."""
    if not payment_reference or not payment_reference.strip():
        raise InvoicePaymentError("A payment reference is required to mark an invoice paid.")
    if not payment_date:
        raise InvoicePaymentError("A payment date is required to mark an invoice paid.")
    execute(
        "UPDATE invoice SET status = 'paid', paid_at = now(), payment_reference = %s, payment_date = %s "
        "WHERE id = %s",
        (payment_reference.strip(), payment_date, invoice_id),
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
    _notify_comment(job_id, author_id)


def _notify_comment(job_pk: int, author_id: int) -> None:
    """Everyone with a stake in the job — its owner, every admin, every
    principal — gets told about a new comment, so a note posted by one role
    doesn't sit unseen by the others."""
    job = query_one(
        "SELECT j.owner_id, j.job_id, j.title, c.name AS client_name, s.name AS author_name "
        "FROM job j LEFT JOIN client c ON c.id = j.client_id JOIN staff s ON s.id = %s "
        "WHERE j.id = %s",
        (author_id, job_pk),
    )
    if not job:
        return
    message = f"{job['author_name']} commented on {job['job_id']}: {job['client_name'] or '—'} — {job['title']}"
    recipients = set()
    if job["owner_id"] and job["owner_id"] != author_id:
        recipients.add(job["owner_id"])
    for s in list_staff(role=ROLE_ADMIN) + list_staff(role=ROLE_PRINCIPAL):
        if s["id"] != author_id:
            recipients.add(s["id"])
    for staff_id in recipients:
        create_notification(staff_id, "comment", "job", job_pk, message)


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
# Document checklist — generic across every module. A service's checklist is
# defined once in service_document_requirement; materialize_job_documents
# copies it onto a specific job at creation time as job_document rows, which
# are then ticked off as received. THE DATA BOUNDARY: job_document never
# stores the file or a sensitive number — only that a document of this type
# was received, and its expiry date if it has one.
# ---------------------------------------------------------------------------
def materialize_job_documents(job_pk: int, service_code: str, attributes: dict | None) -> None:
    """Creates one job_document row per document this job's service — and,
    if the job has one, its Type-field variant — requires. Matches on the
    job's own attribute *values* rather than a hardcoded field key, since
    which field counts as "the variant" differs by service; this is what
    lets the same mechanism serve every future module unchanged."""
    variants = list((attributes or {}).values()) or ["__none__"]
    rows = query(
        "SELECT DISTINCT document_type_code FROM service_document_requirement "
        "WHERE service_code = %s AND (variant = '*' OR variant = ANY(%s))",
        (service_code, variants),
    )
    for row in rows:
        execute(
            "INSERT INTO job_document (job_id, document_type_code) VALUES (%s, %s) "
            "ON CONFLICT (job_id, document_type_code) DO NOTHING",
            (job_pk, row["document_type_code"]),
        )


def list_job_documents(job_pk: int) -> list:
    return query(
        """
        SELECT jd.*, dt.name AS document_name, dt.has_expiry
        FROM job_document jd
        JOIN document_type dt ON dt.code = jd.document_type_code
        WHERE jd.job_id = %s
        ORDER BY dt.name
        """,
        (job_pk,),
    )


def set_job_document_received(job_document_id: int, received: bool) -> None:
    execute(
        "UPDATE job_document SET received = %s, received_at = CASE WHEN %s THEN CURRENT_DATE ELSE NULL END "
        "WHERE id = %s",
        (received, received, job_document_id),
    )


def set_job_document_expiry(job_document_id: int, expiry_date: date | None) -> None:
    execute("UPDATE job_document SET expiry_date = %s WHERE id = %s", (expiry_date, job_document_id))


def job_document_readiness(job_pk: int) -> tuple:
    """(received, required) — a service with no checklist at all (e.g. NIS
    Inspection) reads (0, 0); render that as "no checklist", never as a
    misleading 100%."""
    row = query_one(
        "SELECT COUNT(*) FILTER (WHERE received) AS received, COUNT(*) AS required "
        "FROM job_document WHERE job_id = %s",
        (job_pk,),
    )
    return (row["received"], row["required"]) if row else (0, 0)


# ---------------------------------------------------------------------------
# Expiry tracking — generic across every module: any received job_document
# with an expiry date, on a job that isn't finished, bucketed by urgency so
# nothing lapses silently.
# ---------------------------------------------------------------------------
EXPIRY_EXPIRED = "expired"
EXPIRY_DUE = "due"
EXPIRY_APPROACHING = "approaching"

EXPIRY_URGENCY_LABELS = {
    EXPIRY_EXPIRED: "Expired",
    EXPIRY_DUE: "Due soon",
    EXPIRY_APPROACHING: "Approaching",
}


def expiry_urgency(expiry_date: date) -> str:
    days = (expiry_date - date.today()).days
    if days < 0:
        return EXPIRY_EXPIRED
    if days <= 30:
        return EXPIRY_DUE
    return EXPIRY_APPROACHING


def list_upcoming_expiries(category: str | None = None, within_days: int = 90) -> list:
    """Every received job_document with an expiry date within `within_days`
    (already-expired included, no lower bound) on a job that's still live —
    the cross-job dashboard so nothing lapses silently."""
    sql = f"""
        SELECT jd.id AS job_document_id, jd.expiry_date, dt.name AS document_name,
               j.id AS job_pk, j.job_id AS job_code, j.title, j.category, j.owner_id,
               c.name AS client_name, s.name AS owner_name
        FROM job_document jd
        JOIN document_type dt ON dt.code = jd.document_type_code
        JOIN job j ON j.id = jd.job_id
        LEFT JOIN client c ON c.id = j.client_id
        LEFT JOIN staff s ON s.id = j.owner_id
        WHERE jd.received = TRUE AND jd.expiry_date IS NOT NULL
          AND j.status NOT IN ('{STATUS_DONE}', '{STATUS_CLOSED}', '{STATUS_DISMISSED}')
          AND jd.expiry_date <= CURRENT_DATE + (%s * INTERVAL '1 day')
    """
    params: list = [within_days]
    if category:
        sql += " AND j.category = %s"
        params.append(category)
    sql += " ORDER BY jd.expiry_date"
    return query(sql, tuple(params))


# ---------------------------------------------------------------------------
# Module specialist — which staff handle a given category's jobs, so Capture
# can route/pre-select the right owner. A soft nudge, not a hard filter.
# ---------------------------------------------------------------------------
def list_module_specialists(category: str) -> list:
    return query(
        """
        SELECT ms.*, s.name AS staff_name, s.email AS staff_email
        FROM module_specialist ms
        JOIN staff s ON s.id = ms.staff_id
        WHERE ms.category = %s
        ORDER BY s.name
        """,
        (category,),
    )


def assign_module_specialist(category: str, staff_id: int) -> None:
    execute(
        "INSERT INTO module_specialist (category, staff_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (category, staff_id),
    )


def unassign_module_specialist(category: str, staff_id: int) -> None:
    execute("DELETE FROM module_specialist WHERE category = %s AND staff_id = %s", (category, staff_id))


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
def create_notification(staff_id: int, kind: str, link_type: str, link_id: int, message: str) -> None:
    execute(
        "INSERT INTO notification (staff_id, kind, link_type, link_id, message) VALUES (%s, %s, %s, %s, %s)",
        (staff_id, kind, link_type, link_id, message),
    )


def _notify_principals(kind: str, link_type: str, link_id: int, message: str, exclude_staff_id: int | None = None) -> None:
    for p in list_staff(role="principal"):
        if p["id"] != exclude_staff_id:
            create_notification(p["id"], kind, link_type, link_id, message)


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
        create_notification(job["owner_id"], f"status_{new_status}", "job", job_pk, message)
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
            create_notification(
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
        create_notification(
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
