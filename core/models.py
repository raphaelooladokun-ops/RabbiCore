"""Data access layer. All business rules and queries live here — views call
these functions and never write SQL themselves."""

from __future__ import annotations

import difflib
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

import bcrypt
import psycopg2

from core.constants import (
    RISK_AMBER,
    RISK_GREEN,
    RISK_GREY,
    RISK_RED,
    ROLE_ADMIN,
    ROLE_FILE_ROOM_ADMIN,
    ROLE_MANAGER,
    ROLE_PRINCIPAL,
    ROLE_SPECIALIST,
    ROLE_SUPER_ADMIN,
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


def find_client_by_name(name: str):
    """Exact, case-insensitive match — used by bulk import to avoid creating
    a duplicate client for a name that's already on file."""
    return query_one("SELECT * FROM client WHERE lower(name) = lower(%s)", (name,))


_CLIENT_NAME_EQUIVALENTS = {
    "LTD": "LIMITED",
    "CO": "COMPANY",
    "INC": "INCORPORATED",
    "ENT": "ENTERPRISE",
    "ENTERPRISES": "ENTERPRISE",
    "VENTURE": "VENTURES",
    "NIG": "NIGERIA",
    "NGN": "NIGERIA",
    "&": "AND",
}


def _normalize_client_name(name: str) -> str:
    """Collapses common company-suffix spelling variants ('Ltd' <-> 'Limited',
    etc.) and punctuation so two names that are really the same company
    compare equal — used only for duplicate-detection, never for display or
    storage."""
    s = re.sub(r"[^\w\s&]", " ", name.upper())
    tokens = [ _CLIENT_NAME_EQUIVALENTS.get(t, t) for t in s.split() ]
    return " ".join(tokens)


def find_similar_clients(name: str, threshold: float = 0.82) -> list:
    """Fuzzy duplicate check for client creation: normalizes company-suffix
    variants (Ltd/Limited, Co/Company, ...) then compares by similarity
    ratio, so 'Eurochemco Ventures Ltd' flags against 'Eurochemco Ventures
    Limited' even though the raw strings differ. Returns existing clients
    scoring at or above `threshold`, best match first."""
    normalized = _normalize_client_name(name)
    if not normalized:
        return []
    matches = []
    for c in query("SELECT * FROM client"):
        other = _normalize_client_name(c["name"])
        if other == normalized:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, normalized, other).ratio()
        if score >= threshold:
            matches.append((score, c))
    matches.sort(key=lambda pair: -pair[0])
    return [c for _, c in matches]


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
    client = get_client(row["id"])
    if contact_name or contact_email or contact_phone:
        add_client_contact(client["id"], contact_name, contact_email, contact_phone)
    return client


def list_client_contacts(client_id: int) -> list:
    return query(
        "SELECT * FROM client_contact WHERE client_id = %s ORDER BY id", (client_id,)
    )


def add_client_contact(
    client_id: int, name: str | None = None, email: str | None = None, phone: str | None = None
) -> dict:
    row = execute_returning(
        "INSERT INTO client_contact (client_id, name, email, phone) VALUES (%s, %s, %s, %s) RETURNING id",
        (client_id, (name or "").strip() or None, (email or "").strip() or None, (phone or "").strip() or None),
    )
    return query_one("SELECT * FROM client_contact WHERE id = %s", (row["id"],))


def update_client_contact(
    contact_id: int, name: str | None = None, email: str | None = None, phone: str | None = None
) -> None:
    execute(
        "UPDATE client_contact SET name = %s, email = %s, phone = %s WHERE id = %s",
        ((name or "").strip() or None, (email or "").strip() or None, (phone or "").strip() or None, contact_id),
    )


def delete_client_contact(contact_id: int) -> None:
    execute("DELETE FROM client_contact WHERE id = %s", (contact_id,))


class ClientMergeError(Exception):
    pass


def merge_clients(source_id: int, target_id: int) -> None:
    """Super-admin-only: fold a duplicate client record into the surviving
    one — every job, invoice, compliance item, contact and client-role
    login attributed to `source` moves to `target`, then `source` is
    deleted. Mirrors merge_staff's approach for the same reason: a plain
    delete would be blocked by the client's history everywhere it's
    referenced, and the point of a merge is to keep that history intact
    under one record instead of discarding it."""
    if source_id == target_id:
        raise ClientMergeError("Can't merge a client into itself.")
    if get_client(target_id) is None:
        raise ClientMergeError("The surviving client record no longer exists.")
    if get_client(source_id) is None:
        raise ClientMergeError("The duplicate client record no longer exists.")
    execute("UPDATE staff SET client_id = %s WHERE client_id = %s", (target_id, source_id))
    execute("UPDATE invoice SET client_id = %s WHERE client_id = %s", (target_id, source_id))
    execute("UPDATE job SET client_id = %s WHERE client_id = %s", (target_id, source_id))
    execute("UPDATE compliance_item SET client_id = %s WHERE client_id = %s", (target_id, source_id))
    execute("UPDATE client_contact SET client_id = %s WHERE client_id = %s", (target_id, source_id))
    execute("DELETE FROM client WHERE id = %s", (source_id,))


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


def get_staff(staff_id: int):
    return query_one("SELECT * FROM staff WHERE id = %s", (staff_id,))


# ---------------------------------------------------------------------------
# Staff accounts (super_admin only) — creating a real login for a new member
# of staff, with generated credentials shown once, and deactivating one
# without ever hard-deleting the record (their jobs/comments/invoices stay
# attributed to them).
# ---------------------------------------------------------------------------
_USERNAME_DOMAIN = "rabbicore.local"
# Excludes visually ambiguous characters (l/1/I, O/0) so a generated
# password is easy to read back and type correctly by hand.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _slugify_name(name: str) -> str:
    ascii_only = re.sub(r"[^a-zA-Z\s]", "", name).strip().lower()
    parts = ascii_only.split()
    return ".".join(parts) if parts else "user"


def generate_username(name: str) -> str:
    """A unique login username derived from the person's name — this app has
    no separate 'email' concept for staff, so the generated username is
    stored in the same `email` column verify_login() checks against."""
    base = _slugify_name(name)
    candidate = f"{base}@{_USERNAME_DOMAIN}"
    n = 2
    while query_one("SELECT 1 FROM staff WHERE lower(email) = lower(%s)", (candidate,)):
        candidate = f"{base}{n}@{_USERNAME_DOMAIN}"
        n += 1
    return candidate


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_staff_account(name: str, role: str) -> dict:
    """Super-admin-only: create a new staff login with a generated username
    and password. Returns the new staff row plus the plaintext credentials
    for one-time display — only the bcrypt hash is ever stored; the
    plaintext never touches the database."""
    username = generate_username(name)
    password = generate_password()
    row = execute_returning(
        "INSERT INTO staff (name, email, password_hash, role) VALUES (%s, %s, %s, %s) RETURNING id",
        (name, username, hash_password(password), role),
    )
    return {"staff": get_staff(row["id"]), "username": username, "password": password}


def set_staff_active(staff_id: int, active: bool) -> None:
    execute("UPDATE staff SET active = %s WHERE id = %s", (active, staff_id))


class StaffDeleteError(Exception):
    pass


def delete_staff(staff_id: int, force: bool = False) -> None:
    """Super-admin-only real delete — permanent, unlike deactivate. Refuses
    if this person has any history attached (owned or created jobs, an
    invoice touch, a logged expense, a posted comment, an invoice
    unapproval, a job/invoice code edit, a name/detail field edit, or a
    compliance item they logged): that's real audit trail, not something a
    cleanup action should silently erase — mirrors delete_job's own
    invoice-history guard. Deactivate instead for anyone who's actually
    done work; delete is for a mistakenly-created account with nothing on
    it yet.

    `force=True` is the PIN-gated escape hatch (super admin only, checked
    in the view layer): it detaches every reference it can null out (job
    ownership/authorship, invoice actor fields, expense authorship) and
    deletes the comments this person posted outright, since author_id can't
    be null — a deliberately destructive path, not the everyday one."""
    row = query_one(
        """
        SELECT (
            EXISTS(SELECT 1 FROM job WHERE owner_id = %s OR created_by = %s
                                        OR hidden_by = %s OR start_override_by = %s)
            OR EXISTS(SELECT 1 FROM invoice WHERE created_by = %s OR approved_by = %s
                                              OR rejected_by = %s OR sent_by = %s)
            OR EXISTS(SELECT 1 FROM job_expense WHERE created_by = %s)
            OR EXISTS(SELECT 1 FROM job_comment WHERE author_id = %s)
            OR EXISTS(SELECT 1 FROM invoice_unapproval_log WHERE actor_id = %s)
            OR EXISTS(SELECT 1 FROM code_edit_log WHERE changed_by = %s)
            OR EXISTS(SELECT 1 FROM field_edit_log WHERE changed_by = %s)
            OR EXISTS(SELECT 1 FROM compliance_item WHERE created_by = %s)
        ) AS has_history
        """,
        (staff_id,) * 14,
    )
    if row and row["has_history"]:
        if not force:
            raise StaffDeleteError(
                "This user has jobs, invoices, expenses, comments or edit history on file and can't be "
                "permanently deleted — deactivate instead to keep the record but block their login."
            )
        for col in ("owner_id", "created_by", "hidden_by", "start_override_by"):
            execute(f"UPDATE job SET {col} = NULL WHERE {col} = %s", (staff_id,))
        for col in ("created_by", "approved_by", "rejected_by", "sent_by"):
            execute(f"UPDATE invoice SET {col} = NULL WHERE {col} = %s", (staff_id,))
        execute("UPDATE job_expense SET created_by = NULL WHERE created_by = %s", (staff_id,))
        execute("UPDATE invoice_unapproval_log SET actor_id = NULL WHERE actor_id = %s", (staff_id,))
        execute("UPDATE code_edit_log SET changed_by = NULL WHERE changed_by = %s", (staff_id,))
        execute("UPDATE field_edit_log SET changed_by = NULL WHERE changed_by = %s", (staff_id,))
        execute("UPDATE compliance_item SET created_by = NULL WHERE created_by = %s", (staff_id,))
        execute("DELETE FROM job_comment WHERE author_id = %s", (staff_id,))
    execute("DELETE FROM staff WHERE id = %s", (staff_id,))


def find_active_staff_by_name(name: str):
    """Exact, case-insensitive match against active staff — used at
    creation time to warn about a likely duplicate before a second
    identical-looking account gets made."""
    return query_one("SELECT * FROM staff WHERE lower(name) = lower(%s) AND active = TRUE", (name,))


def merge_staff(source_id: int, target_id: int) -> None:
    """Super-admin-only: fold a duplicate account into another — every
    job, invoice, expense, comment and specialist assignment attributed
    to `source` moves to `target`, then `source` is deleted. This is how
    the two identical-looking staff rows from before duplicate-prevention
    existed actually get resolved, since a plain delete refuses any
    account with history and a real person usually has some by now."""
    if source_id == target_id:
        raise StaffDeleteError("Can't merge a user into themselves.")
    for col in ("owner_id", "created_by", "hidden_by", "start_override_by"):
        execute(f"UPDATE job SET {col} = %s WHERE {col} = %s", (target_id, source_id))
    for col in ("created_by", "approved_by", "rejected_by", "sent_by"):
        execute(f"UPDATE invoice SET {col} = %s WHERE {col} = %s", (target_id, source_id))
    execute("UPDATE job_expense SET created_by = %s WHERE created_by = %s", (target_id, source_id))
    execute("UPDATE job_comment SET author_id = %s WHERE author_id = %s", (target_id, source_id))
    execute(
        "UPDATE module_specialist SET staff_id = %s WHERE staff_id = %s "
        "AND category NOT IN (SELECT category FROM module_specialist WHERE staff_id = %s)",
        (target_id, source_id, target_id),
    )
    execute("DELETE FROM module_specialist WHERE staff_id = %s", (source_id,))
    execute("DELETE FROM notification WHERE staff_id = %s", (source_id,))
    execute("DELETE FROM staff WHERE id = %s", (source_id,))


def reset_staff_password(staff_id: int) -> dict:
    """Super-admin-only: issue a brand-new generated password for an
    existing user (their username/email is unchanged) — the answer to
    'I need to hand out working credentials again' without ever storing a
    recoverable password. The old password stops working immediately;
    only the new bcrypt hash is stored, same as at account creation."""
    password = generate_password()
    execute("UPDATE staff SET password_hash = %s WHERE id = %s", (hash_password(password), staff_id))
    return {"staff": get_staff(staff_id), "password": password}


def list_staff_categories() -> dict:
    """staff_id -> the list of module categories they're assigned to
    (module_specialist), across every module — used to show a specialist's
    speciality in the user-management list and sidebar."""
    rows = query("SELECT staff_id, category FROM module_specialist")
    result: dict = {}
    for r in rows:
        result.setdefault(r["staff_id"], []).append(r["category"])
    return result


def list_service_catalogue() -> list:
    return query("SELECT * FROM service_catalogue WHERE active = TRUE ORDER BY sort_order")


def get_service(code: str):
    return query_one("SELECT * FROM service_catalogue WHERE code = %s", (code,))


_PILLAR_CODE_PREFIX = {"CAC": "CAC", "Immigration": "IMM", "CIT": "CIT", "State": "STATE", "Other": "OTHER"}


class ServiceCreateError(Exception):
    pass


def create_service(name: str, pillar: str) -> dict:
    """Admin/super_admin-only: add a new service to the catalogue. A
    service outside the 4 locked pillars goes under 'Other' (job.category
    'other') — selectable in Capture immediately, exactly like any other
    service, since Capture always reads the live catalogue rather than a
    hardcoded list. The code is auto-generated from the name (never
    typed), with a numeric suffix appended if it would otherwise collide."""
    name = name.strip()
    if not name:
        raise ServiceCreateError("Enter a service name.")
    if query_one("SELECT 1 FROM service_catalogue WHERE lower(name) = lower(%s)", (name,)):
        raise ServiceCreateError(f"A service named '{name}' already exists.")

    prefix = _PILLAR_CODE_PREFIX.get(pillar, "SVC")
    slug = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")
    base_code = f"{prefix}-{slug}"[:60]
    code = base_code
    n = 2
    while query_one("SELECT 1 FROM service_catalogue WHERE code = %s", (code,)):
        code = f"{base_code}-{n}"[:60]
        n += 1

    max_sort = query_one("SELECT COALESCE(MAX(sort_order), 0) AS m FROM service_catalogue")["m"]
    execute(
        "INSERT INTO service_catalogue (code, pillar, name, fields, active, sort_order) "
        "VALUES (%s, %s, %s, '[]'::jsonb, TRUE, %s)",
        (code, pillar, name, max_sort + 1),
    )
    return get_service(code)


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------
def _temp_code(prefix: str) -> str:
    return f"{prefix}-TMP-{uuid.uuid4().hex[:10]}"


def create_job(
    *,
    client_id: int | None,
    category: str,
    service_type: str | None,
    title: str,
    description: str | None,
    owner_id: int | None,
    source: str,
    created_by: int,
    sla_date: date | None,
    attributes: dict | None = None,
    waiting_on_client: str | None = None,
    internal_notes: str | None = None,
) -> dict:
    row = execute_returning(
        """
        INSERT INTO job (job_id, client_id, category, service_type, title, description,
                          owner_id, status, source, created_by, sla_date, waiting_on_client, internal_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'new', %s, %s, %s, %s, %s)
        RETURNING id, job_id
        """,
        (
            _temp_code("JOB"), client_id, category, service_type, title, description,
            owner_id, source, created_by, sla_date, waiting_on_client, internal_notes,
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
#
# _JOB_SELECT_BASE has no visibility filter — only the dedicated hidden-jobs
# functions query it directly. _JOB_SELECT (every normal read path) appends
# "WHERE j.hidden = FALSE", so hiding a job is a single-point-of-truth cut:
# every call site below appends "AND ..." to it rather than "WHERE ...",
# which is what actually keeps a hidden job out of every list, count, and
# detail page for every role without each function re-implementing the
# filter. The blocked_by join and blocking_count subquery are also guarded
# so a hidden job never leaks into another job's "Depends on" line or
# blocking count either.
# ---------------------------------------------------------------------------
_JOB_SELECT_BASE = """
    SELECT j.*, c.name AS client_name, s.name AS owner_name,
           sc.name AS service_name, sc.pillar AS service_pillar,
           b.job_id AS blocked_by_job_code, b.status AS blocked_by_status, b.title AS blocked_by_title,
           i.invoice_code AS invoice_code, i.status AS invoice_status,
           (SELECT COUNT(*) FROM job d WHERE d.blocked_by = j.id AND d.status = 'blocked' AND d.hidden = FALSE)
               AS blocking_count
    FROM job j
    LEFT JOIN client c ON c.id = j.client_id
    LEFT JOIN staff s ON s.id = j.owner_id
    LEFT JOIN service_catalogue sc ON sc.code = j.service_type
    LEFT JOIN job b ON b.id = j.blocked_by AND b.hidden = FALSE
    LEFT JOIN invoice i ON i.id = j.invoice_id
"""
_JOB_SELECT = _JOB_SELECT_BASE + " WHERE j.hidden = FALSE"


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
          AND j.hidden = FALSE AND b.hidden = FALSE
          AND NOT (COALESCE(je.attributes, '{}'::jsonb) ? 'active_gate')
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
    return query_one(_JOB_SELECT + " AND j.id = %s", (job_pk,))


def get_job_by_job_id(job_id: str):
    _resync_stale_blocked()
    return query_one(_JOB_SELECT + " AND j.job_id = %s", (job_id,))


def list_jobs(
    *,
    status: str | None = None,
    statuses: list | None = None,
    owner_id: int | None = None,
    client_id: int | None = None,
    category: str | None = None,
    exclude_dismissed: bool = False,
    search: str | None = None,
    include_recurring_pending: bool = False,
) -> list:
    _resync_stale_blocked()
    sql = _JOB_SELECT + " AND 1=1"
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
    jobs = query(sql, tuple(params))
    # A recurring job's next cycle is spawned the moment the current one is
    # marked done (see create_next_cycle_job) so the job_extension linkage
    # exists right away — but a job not due until next month showing up as
    # "New" today would overstate what's currently outstanding. Hidden here
    # by default everywhere jobs are listed; list_recurring_jobs()'s own
    # forward-looking panel is the one deliberate exception.
    if not include_recurring_pending:
        jobs = [j for j in jobs if not is_recurring_job_pending(j)]
    return jobs


def list_jobs_for_invoice(invoice_id: int) -> list:
    return query(_JOB_SELECT + " AND j.invoice_id = %s ORDER BY j.created_at", (invoice_id,))


def list_unbilled_jobs() -> list:
    """Every not-yet-invoiced job across all clients, any status — Rabbi
    invoices up front, so a freshly logged job is exactly as invoiceable as
    a finished one. Newest first."""
    return query(
        _JOB_SELECT + " AND j.invoice_id IS NULL AND j.status <> 'dismissed' "
        "ORDER BY j.created_at DESC"
    )


def find_potential_duplicate(client_id: int | None, service_type: str | None):
    """The most recent still-open job for the same client + service, if any
    — surfaced as a 'this may already exist' warning before logging another
    one. Closed and dismissed jobs don't count as live duplicates."""
    if not client_id or not service_type:
        return None
    return query_one(
        _JOB_SELECT + " AND j.client_id = %s AND j.service_type = %s "
        "AND j.status NOT IN ('dismissed', 'closed') ORDER BY j.created_at DESC LIMIT 1",
        (client_id, service_type),
    )


def list_jobs_available_for_invoice(client_id: int, invoice_id: int | None = None) -> list:
    """Not-yet-invoiced jobs for this client, any status — Rabbi invoices up
    front, so most of these will still be 'new' — plus, when revising an
    existing invoice, the jobs already on THAT invoice, so the admin can
    review and adjust its composition."""
    sql = _JOB_SELECT + " AND j.client_id = %s AND j.status <> 'dismissed' AND (j.invoice_id IS NULL"
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
    return query(_JOB_SELECT + " AND j.blocked_by = %s AND j.status = 'blocked'", (job_pk,))


# ---------------------------------------------------------------------------
# Hiding & deleting jobs — super_admin only. Hiding is the soft, reversible
# cut (the job stays in the database, just excluded by _JOB_SELECT — see
# above); deleting is real and permanent, kept deliberately separate and
# harder to reach. Neither is exposed to any other role.
# ---------------------------------------------------------------------------
def hide_jobs(job_pks: list, actor_id: int) -> int:
    """Hide one or many jobs in a single statement (the bulk-hide action) —
    a single hide_job(pk) is just this called with a one-item list."""
    if not job_pks:
        return 0
    execute(
        "UPDATE job SET hidden = TRUE, hidden_at = now(), hidden_by = %s WHERE id = ANY(%s)",
        (actor_id, list(job_pks)),
    )
    return len(job_pks)


def unhide_job(job_pk: int) -> None:
    execute("UPDATE job SET hidden = FALSE, hidden_at = NULL, hidden_by = NULL WHERE id = %s", (job_pk,))


def list_hidden_jobs() -> list:
    return query(_JOB_SELECT_BASE + " WHERE j.hidden = TRUE ORDER BY j.hidden_at DESC NULLS LAST, j.created_at DESC")


class JobDeleteError(Exception):
    pass


def delete_job(job_pk: int, force: bool = False) -> None:
    """Permanently remove a job — distinct from hiding, and deliberately
    harder to undo. Refuses if the job has ever been on an invoice
    (invoice_line references it): that's real accounting history, not
    something a cleanup action should silently erase — hide it instead, or
    take it off the invoice first. job_extension, job_expense, job_comment
    and job_document all cascade automatically.

    `force=True` is the PIN-gated escape hatch (super admin only, checked
    in the view layer): it removes the job's own invoice_line rows instead
    of refusing — the invoice record itself survives with fewer lines.

    Any other job depending on this one (blocked_by) is resolved first —
    same as when a blocker is marked done, not just a dangling reference
    nulled out: a dependent left with blocked_by cleared but status still
    'blocked' would never self-heal (the resync sweep only considers jobs
    that still have a blocker to check)."""
    on_invoice = query_one("SELECT 1 FROM invoice_line WHERE job_id = %s", (job_pk,))
    if on_invoice:
        if not force:
            raise JobDeleteError(
                "This job is on an invoice and can't be permanently deleted — hide it instead, "
                "or remove it from the invoice first."
            )
        execute("DELETE FROM invoice_line WHERE job_id = %s", (job_pk,))
    dependents = query("SELECT id, owner_id, status, title, job_id FROM job WHERE blocked_by = %s", (job_pk,))
    for dep in dependents:
        if dep["status"] == STATUS_BLOCKED:
            execute(
                "UPDATE job SET blocked_by = NULL, status = %s, status_reason = NULL WHERE id = %s",
                (STATUS_IN_PROGRESS, dep["id"]),
            )
            if dep["owner_id"]:
                create_notification(
                    dep["owner_id"], "unblocked", "job", dep["id"],
                    f"Unblocked: the job {dep['job_id']} depended on was deleted — {dep['title']} can proceed",
                )
        else:
            execute("UPDATE job SET blocked_by = NULL WHERE id = %s", (dep["id"],))
    execute("DELETE FROM job WHERE id = %s", (job_pk,))


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

    # started_at/completed_at are set ONCE, on the first-ever crossing into
    # in_progress/done — the "WHERE ... IS NULL" is what keeps a later
    # blocked-then-resumed or done-then-closed transition from overwriting
    # the original timestamp. Powers both the workload report and each
    # job's own elapsed-time badge.
    if new_status == STATUS_IN_PROGRESS:
        execute("UPDATE job SET started_at = now() WHERE id = %s AND started_at IS NULL", (job_pk,))
    if new_status == STATUS_DONE:
        execute("UPDATE job SET completed_at = now() WHERE id = %s AND completed_at IS NULL", (job_pk,))

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


def force_block(job_pk: int, blocker_pk: int | None, reason: str) -> None:
    """Used by a module's own custom gate (immigration's quota validity,
    CIT's TCC obligations, and any future module's) to force a job blocked
    on a condition the generic job_status_guard trigger can't express —
    directly, bypassing the trigger's own blocked_by resolution, which only
    understands "blocker is done/closed." Pair with force_unblock; the
    caller is responsible for setting job_extension.attributes['active_gate']
    so _resync_stale_blocked() leaves this block alone until the gate itself
    clears it."""
    execute(
        "UPDATE job SET blocked_by = %s, status = 'blocked', status_reason = %s WHERE id = %s",
        (blocker_pk, reason, job_pk),
    )


def force_unblock(job_pk: int) -> None:
    """The other half of force_block — resumes the job once the module's own
    gate has cleared. Matches the generic _unblock_dependents/
    _resync_stale_blocked convention of always resuming to in_progress."""
    execute(
        "UPDATE job SET blocked_by = NULL, status = %s, status_reason = NULL WHERE id = %s",
        (STATUS_IN_PROGRESS, job_pk),
    )


def reassign_owner(job_pk: int, new_owner_id: int, actor_id: int | None = None) -> bool:
    """Change who owns a job after creation — the shared primitive behind
    both the job detail page's own reassign control and the register's
    bulk-assign action. Returns False (a no-op) if the job is already
    owned by this person. Notifies the new owner that the job is now
    theirs; the previous owner isn't notified — the ask is "tell them",
    not "announce every handoff to everyone with a stake in the job"."""
    job = get_job(job_pk)
    if not job or job["owner_id"] == new_owner_id:
        return False
    execute("UPDATE job SET owner_id = %s WHERE id = %s", (new_owner_id, job_pk))
    create_notification(
        new_owner_id, "reassigned", "job", job_pk,
        f"{job['job_id']} assigned to you — {job['client_name'] or '—'} — {job['title']}",
    )
    return True


def bulk_reassign_owner(job_pks: list, new_owner_id: int, actor_id: int | None = None) -> int:
    """Bulk version of reassign_owner — one notification per job actually
    changed, exactly as if each had been reassigned individually. Returns
    how many jobs actually changed owner (already-owned-by-them rows are
    skipped, not double-counted or double-notified)."""
    return sum(1 for pk in job_pks if reassign_owner(pk, new_owner_id, actor_id))


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
    resolves until someone re-saves the job. Checks `blocked_by_job_code`
    (from the join in _JOB_SELECT_BASE, which excludes a hidden blocker)
    rather than the raw `blocked_by` column, so a hidden blocker counts as
    resolved too — hidden means "as if it doesn't exist," including as a
    dependency, and the existing STATUS_BLOCKED "Resume" path already
    handles a dependency that's resolved but hasn't been re-saved yet."""
    return job.get("blocked_by_job_code") is not None and job.get("blocked_by_status") not in (
        STATUS_DONE, STATUS_CLOSED,
    )


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


class InvoiceApprovalError(Exception):
    pass


def unapprove_invoice(invoice_id: int, actor_id: int, reason: str) -> None:
    """EC (principal)/super_admin only: remove an already-granted approval,
    sending the invoice back to pending_approval. A reason is always
    required and every un-approval is logged (who, when, why) — this is a
    records-integrity action, never a silent status flip. If the invoice
    had already moved on to sent/paid, those steps are no longer valid
    once approval itself is revoked, so they're cleared too; the caller
    (the UI) is responsible for warning loudly and getting explicit
    confirmation before calling this in that case."""
    if not reason or not reason.strip():
        raise InvoiceApprovalError("A reason is required to remove an invoice's approval.")
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise InvoiceApprovalError("Invoice not found.")
    if invoice["status"] not in ("approved", "paid"):
        raise InvoiceApprovalError("This invoice isn't currently approved.")

    execute(
        "UPDATE invoice SET status = 'pending_approval', approved_by = NULL, approved_at = NULL, "
        "sent_at = NULL, sent_by = NULL, paid_at = NULL, payment_reference = NULL, payment_date = NULL "
        "WHERE id = %s",
        (invoice_id,),
    )
    execute(
        "INSERT INTO invoice_unapproval_log (invoice_id, reason, actor_id) VALUES (%s, %s, %s)",
        (invoice_id, reason.strip(), actor_id),
    )
    if invoice["created_by"] and invoice["created_by"] != actor_id:
        create_notification(
            invoice["created_by"], "invoice_unapproved", "invoice", invoice_id,
            f"Invoice {invoice['invoice_code']} approval removed — {reason.strip()}",
        )


def list_invoice_unapprovals(invoice_id: int) -> list:
    return query(
        "SELECT u.*, s.name AS actor_name FROM invoice_unapproval_log u "
        "LEFT JOIN staff s ON s.id = u.actor_id "
        "WHERE u.invoice_id = %s ORDER BY u.created_at DESC",
        (invoice_id,),
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
# Code corrections — EC/admin/super_admin only. job_id and invoice_code are
# normally auto-generated once and never touched again; this is purely for
# the rare "this was set wrong, fix it" case, logged every time so a manual
# correction is never silent.
# ---------------------------------------------------------------------------
class CodeEditError(Exception):
    pass


def _log_code_edit(entity_type: str, entity_id: int, old_code: str, new_code: str, actor_id: int | None) -> None:
    execute(
        "INSERT INTO code_edit_log (entity_type, entity_id, old_code, new_code, changed_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (entity_type, entity_id, old_code, new_code, actor_id),
    )


def update_job_code(job_pk: int, new_code: str, actor_id: int | None = None) -> None:
    new_code = new_code.strip()
    if not new_code:
        raise CodeEditError("Job ID can't be blank.")
    job = get_job(job_pk)
    if not job:
        raise CodeEditError("Job not found.")
    if new_code == job["job_id"]:
        return
    if query_one("SELECT 1 FROM job WHERE job_id = %s AND id <> %s", (new_code, job_pk)):
        raise CodeEditError(f"Job ID '{new_code}' is already in use.")
    execute("UPDATE job SET job_id = %s WHERE id = %s", (new_code, job_pk))
    _log_code_edit("job", job_pk, job["job_id"], new_code, actor_id)


def update_invoice_code(invoice_pk: int, new_code: str, actor_id: int | None = None) -> None:
    new_code = new_code.strip()
    if not new_code:
        raise CodeEditError("Invoice code can't be blank.")
    invoice = get_invoice(invoice_pk)
    if not invoice:
        raise CodeEditError("Invoice not found.")
    if new_code == invoice["invoice_code"]:
        return
    if query_one("SELECT 1 FROM invoice WHERE invoice_code = %s AND id <> %s", (new_code, invoice_pk)):
        raise CodeEditError(f"Invoice code '{new_code}' is already in use.")
    execute("UPDATE invoice SET invoice_code = %s WHERE id = %s", (new_code, invoice_pk))
    _log_code_edit("invoice", invoice_pk, invoice["invoice_code"], new_code, actor_id)


def list_code_edits(entity_type: str, entity_id: int) -> list:
    return query(
        "SELECT l.*, s.name AS changed_by_name FROM code_edit_log l "
        "LEFT JOIN staff s ON s.id = l.changed_by "
        "WHERE l.entity_type = %s AND l.entity_id = %s ORDER BY l.changed_at DESC",
        (entity_type, entity_id),
    )


class FieldEditError(Exception):
    pass


def _log_field_edit(
    entity_type: str, entity_id: int, field: str, old_value: str | None, new_value: str | None,
    actor_id: int | None,
) -> None:
    execute(
        "INSERT INTO field_edit_log (entity_type, entity_id, field, old_value, new_value, changed_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (entity_type, entity_id, field, old_value, new_value, actor_id),
    )


def update_job_details(
    job_pk: int, *, title: str | None = None, description: str | None = None, actor_id: int | None = None,
) -> None:
    """EC/admin/manager/super_admin: correct a job's title and/or
    description. Only fields actually passed are touched; each changed
    field gets its own field_edit_log row so the trail reads as plain
    English (title changed from X to Y) rather than one opaque row."""
    job = get_job(job_pk)
    if not job:
        raise FieldEditError("Job not found.")

    if title is not None:
        title = title.strip()
        if not title:
            raise FieldEditError("Job title can't be blank.")
        if title != job["title"]:
            execute("UPDATE job SET title = %s WHERE id = %s", (title, job_pk))
            _log_field_edit("job", job_pk, "title", job["title"], title, actor_id)

    if description is not None:
        description = description.strip() or None
        if description != job["description"]:
            execute("UPDATE job SET description = %s WHERE id = %s", (description, job_pk))
            _log_field_edit("job", job_pk, "description", job["description"], description, actor_id)


def update_staff_name(staff_pk: int, new_name: str, actor_id: int | None = None) -> None:
    """EC/admin/manager/super_admin: correct a staff member's display
    name (e.g. a typo or a legal name change) — their login username is
    untouched, so this never affects how they sign in."""
    new_name = new_name.strip()
    if not new_name:
        raise FieldEditError("Name can't be blank.")
    staff = get_staff(staff_pk)
    if not staff:
        raise FieldEditError("User not found.")
    if new_name == staff["name"]:
        return
    execute("UPDATE staff SET name = %s WHERE id = %s", (new_name, staff_pk))
    _log_field_edit("staff", staff_pk, "name", staff["name"], new_name, actor_id)


def update_client_name(client_pk: int, new_name: str, actor_id: int | None = None) -> None:
    """EC/admin/manager/super_admin: correct a company/client's name. The
    client table is the single source of truth — every job, invoice and
    register row reads it live via client_id — so this one update is all
    it takes to change the name everywhere it's shown."""
    new_name = new_name.strip()
    if not new_name:
        raise FieldEditError("Company name can't be blank.")
    client = get_client(client_pk)
    if not client:
        raise FieldEditError("Client not found.")
    if new_name == client["name"]:
        return
    if query_one("SELECT 1 FROM client WHERE lower(name) = lower(%s) AND id <> %s", (new_name, client_pk)):
        raise FieldEditError(f"A client named '{new_name}' already exists.")
    execute("UPDATE client SET name = %s WHERE id = %s", (new_name, client_pk))
    _log_field_edit("client", client_pk, "name", client["name"], new_name, actor_id)


def list_field_edits(entity_type: str, entity_id: int) -> list:
    return query(
        "SELECT l.*, s.name AS changed_by_name FROM field_edit_log l "
        "LEFT JOIN staff s ON s.id = l.changed_by "
        "WHERE l.entity_type = %s AND l.entity_id = %s ORDER BY l.changed_at DESC",
        (entity_type, entity_id),
    )


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
    """Everyone with a stake in the job — its owner, every admin, manager,
    principal and super_admin — gets told about a new comment, so a note
    posted by one role doesn't sit unseen by the others."""
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
    for s in (
        list_staff(role=ROLE_ADMIN) + list_staff(role=ROLE_PRINCIPAL) + list_staff(role=ROLE_MANAGER)
        + list_staff(role=ROLE_SUPER_ADMIN)
    ):
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
    lets the same mechanism serve every future module unchanged. Only
    string-valued attributes are candidate variants — quantity/subject-count
    counts and subject-label lists never select a document requirement."""
    variants = [v for v in (attributes or {}).values() if isinstance(v, str)] or ["__none__"]
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
          AND j.hidden = FALSE
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
# Recurring jobs — generic across every module: a service_catalogue row can
# carry a recurring_frequency ('monthly' / 'yearly'). CIT is the first
# module with recurring obligations (VAT returns, Annual Return); the
# mechanism itself has no CIT-specific knowledge, so State's own recurring
# filings reuse it unchanged once their catalogue rows set the same flag.
# ---------------------------------------------------------------------------
def is_recurring_service(service_code: str) -> str | None:
    row = query_one("SELECT recurring_frequency FROM service_catalogue WHERE code = %s", (service_code,))
    return row["recurring_frequency"] if row else None


def is_recurring_job_pending(job: dict) -> bool:
    """True for a recurring job's next cycle before its actual due month
    arrives. create_next_cycle_job spawns it as soon as the current cycle
    is marked done, so the job_extension bookkeeping (next_cycle_job_id)
    is in place right away — but a job due next month showing up as 'New'
    today would overstate what's currently outstanding. The date gate for
    list_jobs()'s default hide-until-due behaviour."""
    if job.get("status") != STATUS_NEW or not job.get("sla_date") or not job.get("service_type"):
        return False
    if not is_recurring_service(job["service_type"]):
        return False
    today = date.today()
    due = job["sla_date"]
    return (due.year, due.month) > (today.year, today.month)


def _add_months(d: date, months: int) -> date:
    import calendar

    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def create_next_cycle_job(completed_job_pk: int, actor_id: int | None = None) -> dict | None:
    """If this job's service recurs on a schedule, spawn the next cycle's
    job automatically the moment this one is marked done — so a recurring
    obligation (this month's VAT return, this year's Annual Return) is
    never left to memory. Idempotent per completed job: calling this twice
    for the same job never creates two next-cycle jobs."""
    job = get_job(completed_job_pk)
    if not job or not job["service_type"]:
        return None
    frequency = is_recurring_service(job["service_type"])
    if not frequency:
        return None

    attrs = get_job_extension(job["id"])
    if attrs.get("next_cycle_job_id"):
        return None

    base = job["sla_date"] or date.today()
    next_due = _add_months(base, 1 if frequency == "monthly" else 12)

    carried_attrs = {
        k: v for k, v in attrs.items()
        if k not in ("next_cycle_job_id", "previous_cycle_job_id", "active_gate", "desk_exam_job_id")
    }
    next_job = create_job(
        client_id=job["client_id"], category=job["category"], service_type=job["service_type"],
        title=job["title"], description=job["description"], owner_id=job["owner_id"],
        source=job["source"], created_by=actor_id or job["created_by"], sla_date=next_due,
        attributes=carried_attrs,
    )

    next_attrs = get_job_extension(next_job["id"])
    next_attrs["previous_cycle_job_id"] = job["id"]
    set_job_extension(next_job["id"], next_attrs)

    attrs["next_cycle_job_id"] = next_job["id"]
    set_job_extension(job["id"], attrs)

    if next_job["owner_id"]:
        create_notification(
            next_job["owner_id"], "recurring_job_created", "job", next_job["id"],
            f"{next_job['job_id']} auto-created — next {frequency} cycle for "
            f"{next_job['client_name'] or '—'} (due {next_due.isoformat()})",
        )
    return next_job


def list_recurring_jobs(category: str | None = None) -> list:
    """Every not-yet-finished job whose service recurs on a schedule,
    ordered by due date — powers each module's 'upcoming recurring
    obligations' panel so a cycle is never simply forgotten."""
    recurring_codes = {
        r["code"] for r in query("SELECT code FROM service_catalogue WHERE recurring_frequency IS NOT NULL")
    }
    if not recurring_codes:
        return []
    jobs = list_jobs(category=category, exclude_dismissed=True, include_recurring_pending=True)
    out = [
        j for j in jobs
        if j["service_type"] in recurring_codes and j["status"] not in (STATUS_DONE, STATUS_CLOSED)
    ]
    out.sort(key=lambda j: j["sla_date"] or date.max)
    return out


# ---------------------------------------------------------------------------
# Workload reporting + elapsed-time tracking — both built on the same
# started_at/completed_at pair set once in set_status() above.
# ---------------------------------------------------------------------------
def workload_report(date_from: date, date_to: date) -> list:
    """Per-specialist count of jobs "carried out" — started and/or
    completed — within [date_from, date_to] (inclusive, whole days). A job
    counts once even if it was both started and completed in the same
    window. Ordered busiest first."""
    rows = query(
        """
        SELECT s.id AS staff_id, s.name AS staff_name, s.email AS staff_email, COUNT(DISTINCT j.id) AS job_count
        FROM staff s
        JOIN job j ON j.owner_id = s.id AND j.hidden = FALSE
        WHERE (j.started_at::date BETWEEN %s AND %s) OR (j.completed_at::date BETWEEN %s AND %s)
        GROUP BY s.id, s.name, s.email
        ORDER BY job_count DESC, s.name
        """,
        (date_from, date_to, date_from, date_to),
    )
    return rows


def workload_report_jobs(staff_id: int, date_from: date, date_to: date) -> list:
    """The actual jobs behind one specialist's workload_report count —
    powers the "click the number" detail popup so the EC gets an overview
    of what was actually worked on, not just a bare count."""
    return query(
        _JOB_SELECT + """
        AND j.owner_id = %s
        AND ((j.started_at::date BETWEEN %s AND %s) OR (j.completed_at::date BETWEEN %s AND %s))
        ORDER BY COALESCE(j.completed_at, j.started_at) DESC
        """,
        (staff_id, date_from, date_to, date_from, date_to),
    )


def format_duration(start, end) -> str:
    """Human-readable elapsed time between two datetimes, coarsest-unit-
    first ("2 weeks 1 day", "3 days", "5 hours", "12 minutes") — exactly
    the granularity a "how long has this been running" badge needs, never
    down to the second."""
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return "just now"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    weeks, rem_days = divmod(days, 7)
    if weeks == 0:
        return f"{days} day{'s' if days != 1 else ''}"
    parts = [f"{weeks} week{'s' if weeks != 1 else ''}"]
    if rem_days:
        parts.append(f"{rem_days} day{'s' if rem_days != 1 else ''}")
    return " ".join(parts)


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


# ---------------------------------------------------------------------------
# COMPLIANCE TRACKER — per-client compliance items (CERPAC cards, quota
# approvals, TCCs, ...), independent of any specific job. Urgency is always
# derived from expiry_date at read time, never stored.
# ---------------------------------------------------------------------------
COMPLIANCE_EXPIRED = "expired"
COMPLIANCE_URGENT = "urgent"
COMPLIANCE_UPCOMING = "upcoming"
COMPLIANCE_OK = "ok"

COMPLIANCE_STATUS_LABELS = {
    COMPLIANCE_EXPIRED: "Expired",
    COMPLIANCE_URGENT: "Urgent",
    COMPLIANCE_UPCOMING: "Upcoming",
    COMPLIANCE_OK: "OK",
}

# Same 30-day "act now" and 90-day "plan ahead" thresholds already used for
# job-document expiries (see expiry_urgency above) — one mental model for
# what "urgent" means everywhere in the app. The one difference: an item
# with no expiry_date on file at all is "ok" here rather than treated as
# missing, since a compliance item can legitimately be issue-date-only.
def compliance_item_status(expiry_date: date | None) -> str:
    if not expiry_date:
        return COMPLIANCE_OK
    days = (expiry_date - date.today()).days
    if days < 0:
        return COMPLIANCE_EXPIRED
    if days <= 30:
        return COMPLIANCE_URGENT
    if days <= 90:
        return COMPLIANCE_UPCOMING
    return COMPLIANCE_OK


def list_compliance_items(client_id: int | None = None) -> list:
    sql = (
        "SELECT ci.*, c.name AS client_name FROM compliance_item ci "
        "JOIN client c ON c.id = ci.client_id WHERE 1=1"
    )
    params: list = []
    if client_id:
        sql += " AND ci.client_id = %s"
        params.append(client_id)
    sql += " ORDER BY ci.expiry_date ASC NULLS LAST, c.name"
    return query(sql, tuple(params))


def compliance_summary() -> dict:
    """Across every client: total counts by urgency (for the tracker's
    top section) plus each client's own items with status attached (for
    the card grid and per-client detail) — one query, two views on it."""
    items = list_compliance_items()
    totals = {COMPLIANCE_EXPIRED: 0, COMPLIANCE_URGENT: 0, COMPLIANCE_UPCOMING: 0}
    by_client: dict = {}
    for item in items:
        status = compliance_item_status(item["expiry_date"])
        item = dict(item)
        item["status"] = status
        if status in totals:
            totals[status] += 1
        by_client.setdefault(item["client_id"], []).append(item)
    return {"totals": totals, "by_client": by_client}


def create_compliance_item(
    client_id: int,
    document_type: str,
    *,
    position: str | None = None,
    subject_name: str | None = None,
    issue_date: date | None = None,
    expiry_date: date | None = None,
    created_by: int | None = None,
) -> dict:
    row = execute_returning(
        "INSERT INTO compliance_item "
        "(client_id, document_type, position, subject_name, issue_date, expiry_date, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (client_id, document_type.strip(), (position or "").strip() or None,
         (subject_name or "").strip() or None, issue_date, expiry_date, created_by),
    )
    return query_one("SELECT * FROM compliance_item WHERE id = %s", (row["id"],))


def delete_compliance_item(item_id: int) -> None:
    execute("DELETE FROM compliance_item WHERE id = %s", (item_id,))


def staff_sees_compliance(user: dict) -> bool:
    """EC, manager, admin, super_admin always; a specialist only if
    they're assigned to the immigration module — everyone else (other
    specialists, clients) never. Used both for the Compliance nav item
    (app.py) and the compliance section on a client's own page, so the
    two can never disagree about who's allowed to see this."""
    if user["role"] in (ROLE_PRINCIPAL, ROLE_MANAGER, ROLE_ADMIN, ROLE_SUPER_ADMIN):
        return True
    if user["role"] == ROLE_SPECIALIST:
        return "immigration" in list_staff_categories().get(user["id"], [])
    return False


# ---------------------------------------------------------------------------
# FILE REGISTER — the file office's own environment: who has which client's
# file, for which job, and the full in/out history. Only file_room_admin
# makes entries; EC/manager/admin/super_admin see the same dashboard
# read-only (core.constants.ROLE_FILE_ROOM_ADMIN).
# ---------------------------------------------------------------------------
FILE_STATUS_OUT = "out"
FILE_STATUS_RETURNED = "returned"

_FILE_REGISTER_SELECT = (
    "SELECT fr.*, c.name AS client_name, j.job_id AS job_code, j.title AS job_title, "
    "col.name AS collected_by_name, log.name AS logged_by_name "
    "FROM file_register fr "
    "JOIN client c ON c.id = fr.client_id "
    "JOIN job j ON j.id = fr.job_id "
    "JOIN staff col ON col.id = fr.collected_by "
    "LEFT JOIN staff log ON log.id = fr.logged_by "
)


def file_entry_status(entry: dict) -> str:
    return FILE_STATUS_RETURNED if entry.get("returned_at") else FILE_STATUS_OUT


def can_write_file_register(user: dict) -> bool:
    return user["role"] == ROLE_FILE_ROOM_ADMIN


def checkout_file(
    client_id: int, job_id: int, collected_by: int, *, out_at=None, logged_by: int | None = None
) -> dict:
    row = execute_returning(
        "INSERT INTO file_register (client_id, job_id, collected_by, logged_by, out_at) "
        "VALUES (%s, %s, %s, %s, COALESCE(%s, now())) RETURNING id",
        (client_id, job_id, collected_by, logged_by, out_at),
    )
    return query_one(_FILE_REGISTER_SELECT + "WHERE fr.id = %s", (row["id"],))


def mark_file_returned(entry_id: int, *, returned_at=None) -> None:
    execute(
        "UPDATE file_register SET returned_at = COALESCE(%s, now()) WHERE id = %s",
        (returned_at, entry_id),
    )


def list_currently_out_files() -> list:
    return query(_FILE_REGISTER_SELECT + "WHERE fr.returned_at IS NULL ORDER BY fr.out_at ASC")


def list_file_register(client_id: int | None = None) -> list:
    sql = _FILE_REGISTER_SELECT + "WHERE 1=1"
    params: list = []
    if client_id:
        sql += " AND fr.client_id = %s"
        params.append(client_id)
    sql += " ORDER BY fr.out_at DESC"
    return query(sql, tuple(params))
