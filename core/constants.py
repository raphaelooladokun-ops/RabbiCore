"""Roles, statuses, labels and the functional colour system — one place so
every screen agrees on what things are called and what colour they are."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLE_PRINCIPAL = "principal"
ROLE_ADMIN = "admin"
ROLE_SPECIALIST = "specialist"
ROLE_CLIENT = "client"
# Full access across every environment (principal + admin + specialist) —
# for the firm owner. Every existing role-gate in the app is an *addition*
# of this role alongside its existing allowed roles, never a replacement,
# so principal/admin/specialist/client keep exactly the access they had.
ROLE_SUPER_ADMIN = "super_admin"
# Runs the operation day-to-day: near-full operational visibility (every
# job, module, client, specialist), can assign/reassign work, create
# invoices and push them for approval, and edit names/details. Final
# financial and irreversible authority — approving/un-approving an
# invoice, permanent deletion, overriding system rules — stays with
# ROLE_PRINCIPAL (EC) and ROLE_SUPER_ADMIN; every manager role-gate in the
# app is an addition alongside admin's existing access, mirroring admin
# plus the extra oversight views, never a replacement of anyone else's.
ROLE_MANAGER = "manager"

ROLE_LABELS = {
    ROLE_PRINCIPAL: "Principal",
    ROLE_ADMIN: "Admin",
    ROLE_SPECIALIST: "Specialist",
    ROLE_CLIENT: "Client",
    ROLE_SUPER_ADMIN: "Super Admin",
    ROLE_MANAGER: "Manager",
}

# ---------------------------------------------------------------------------
# Job status lifecycle
# ---------------------------------------------------------------------------
STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUS_CLOSED = "closed"
STATUS_DISMISSED = "dismissed"

STATUS_LABELS = {
    STATUS_NEW: "New",
    STATUS_IN_PROGRESS: "In progress",
    STATUS_BLOCKED: "Blocked",
    STATUS_DONE: "Done — awaiting invoice",
    STATUS_CLOSED: "Closed",
    STATUS_DISMISSED: "Dismissed",
}

# Short label variant for tight spaces (register table, chips)
STATUS_LABELS_SHORT = {
    STATUS_NEW: "New",
    STATUS_IN_PROGRESS: "In progress",
    STATUS_BLOCKED: "Blocked",
    STATUS_DONE: "Done",
    STATUS_CLOSED: "Closed",
    STATUS_DISMISSED: "Dismissed",
}

STATUSES_IN_ORDER = [STATUS_NEW, STATUS_IN_PROGRESS, STATUS_BLOCKED, STATUS_DONE, STATUS_CLOSED]

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
SOURCE_CLIENT_EMAIL = "client_email"
SOURCE_TEAM_GROUP_FORWARD = "team_group_forward"
SOURCE_BULK_IMPORT = "bulk_import"

SOURCE_LABELS = {
    SOURCE_CLIENT_EMAIL: "Client email / form",
    SOURCE_TEAM_GROUP_FORWARD: "Team-group forward",
    SOURCE_BULK_IMPORT: "Bulk import",
}

# ---------------------------------------------------------------------------
# Job category (which pillar of the firm owns the work)
# ---------------------------------------------------------------------------
CATEGORY_LABELS = {
    "front_office": "Front office",
    "cac": "CAC",
    "immigration": "Immigration",
    "cit": "CIT",
    "state": "State Matters",
    "other": "Other Services",
}

# ---------------------------------------------------------------------------
# Invoice status
# ---------------------------------------------------------------------------
INVOICE_STATUS_LABELS = {
    "pending_approval": "Pending approval",
    "approved": "Approved",
    "paid": "Paid",
    "rejected": "Rejected",
}

# ---------------------------------------------------------------------------
# The functional colour system — red/amber/green/grey mean the same thing
# everywhere in the app. This is a *risk/urgency* signal computed from a
# job's status + SLA date, not the literal status value.
# ---------------------------------------------------------------------------
NAVY = "#0A2540"
TEAL = "#1ABC9C"

RISK_RED = "red"
RISK_AMBER = "amber"
RISK_GREEN = "green"
RISK_GREY = "grey"

RISK_COLORS = {
    RISK_RED: "#E5484D",
    RISK_AMBER: "#F5A623",
    RISK_GREEN: "#1ABC9C",
    RISK_GREY: "#8A94A6",
}

RISK_LABELS = {
    RISK_RED: "Expired / at risk",
    RISK_AMBER: "Due / needs attention",
    RISK_GREEN: "Done / ready",
    RISK_GREY: "Monitoring",
}

RISK_EMOJI = {
    RISK_RED: "🔴",
    RISK_AMBER: "🟠",
    RISK_GREEN: "🟢",
    RISK_GREY: "⚪",
}


# ---------------------------------------------------------------------------
# Quantity / multi-subject capture — Capture only shows the extra field(s)
# below when the picked service is one of these, so the common case (no
# quantity, no subjects) stays a plain, fast form. Values captured here are
# a count and a short internal label only — never passport numbers, DOB or
# other identity data, which stay in external secure storage.
# ---------------------------------------------------------------------------
QUANTITY_SERVICE_CODES = {"IMM-QUOTA"}

MULTI_SUBJECT_SERVICE_CODES = {
    "IMM-ECERPAC-PRINCIPAL",
    "IMM-ECERPAC-DEP-SPOUSE",
    "IMM-ECERPAC-DEP-CHILD",
}


def humanize(value: str, mapping: dict | None = None) -> str:
    """Turn a raw db value into a human label. Falls back to title-casing
    underscores so nothing ever shows a raw db value on screen."""
    if value is None:
        return "—"
    if mapping and value in mapping:
        return mapping[value]
    return value.replace("_", " ").strip().capitalize()
