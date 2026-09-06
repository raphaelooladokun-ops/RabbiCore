"""Immigration module business rules — the one specialist rule that can't
live in the generic job spine: a CERPAC (Principal) job depends on its
linked quota position still having enough runway, checked against that
quota job's own document checklist rather than a new field bolted onto
`job`. This is the pattern future modules (CIT, State) follow for their own
module-specific rules — everything reusable (the checklist mechanism,
expiry tracking, module-specialist routing) stays generic in
`core/models.py`; only this one gate lives here.

The gate is date/validity-driven, not status-driven, so it deliberately
does NOT reuse job_status_guard's blocked_by resolution (which only checks
whether the blocker is done/closed) — it manages `blocked_by` + `status`
directly. `models._resync_stale_blocked()` is taught to leave alone any
job whose extension marks its blocked_by as this kind of link, so the two
mechanisms never fight over the same job.
"""

from __future__ import annotations

from datetime import date

from core import models
from core.constants import STATUS_CLOSED, STATUS_DISMISSED, STATUS_DONE, STATUS_IN_PROGRESS
from core.db import execute, query_one

QUOTA_SERVICE_CODE = "IMM-QUOTA"
CERPAC_PRINCIPAL_SERVICE_CODE = "IMM-ECERPAC-PRINCIPAL"
QUOTA_APPROVAL_DOC_CODE = "QUOTA-APPROVAL"

# ~6 months. Approximated in days (no new date-math dependency) rather than
# calendar months, so "less than 6 months validity" is a simple, unambiguous
# comparison.
QUOTA_MIN_VALIDITY_DAYS = 182

ACTIVE_GATE_QUOTA = "quota_validity"

_GATE_BLOCK_REASON = (
    "Blocked — linked quota position has less than 6 months validity remaining. "
    "The quota must be renewed before this CERPAC job can proceed."
)


def list_quota_candidates(client_id: int | None) -> list:
    """This client's Quota jobs — what a CERPAC-Principal job can link to."""
    if not client_id:
        return []
    jobs = models.list_jobs(client_id=client_id, category="immigration", exclude_dismissed=True)
    return [j for j in jobs if j["service_type"] == QUOTA_SERVICE_CODE]


def quota_validity(quota_job_pk: int) -> tuple:
    """(expiry_date, days_remaining) from the quota job's own QUOTA-APPROVAL
    checklist item — (None, None) if it hasn't been received/dated yet,
    which is treated as "not valid" by the gate below."""
    row = query_one(
        "SELECT expiry_date FROM job_document WHERE job_id = %s AND document_type_code = %s AND received = TRUE",
        (quota_job_pk, QUOTA_APPROVAL_DOC_CODE),
    )
    if not row or not row["expiry_date"]:
        return None, None
    return row["expiry_date"], (row["expiry_date"] - date.today()).days


def is_quota_gate_active(job: dict) -> bool:
    return models.get_job_extension(job["id"]).get("active_gate") == ACTIVE_GATE_QUOTA


def get_linked_quota_job_id(job: dict) -> int | None:
    return models.get_job_extension(job["id"]).get("linked_quota_job_id")


def set_quota_link(cerpac_job_pk: int, quota_job_pk: int | None, actor_id: int | None = None) -> None:
    """Link (or unlink) a CERPAC-Principal job to the quota job whose
    validity gates it, then immediately re-evaluate the gate."""
    job = models.get_job(cerpac_job_pk)
    attrs = models.get_job_extension(cerpac_job_pk)
    was_gated = attrs.get("active_gate") == ACTIVE_GATE_QUOTA

    if quota_job_pk:
        attrs["linked_quota_job_id"] = quota_job_pk
    else:
        attrs.pop("linked_quota_job_id", None)
        attrs.pop("active_gate", None)
    models.set_job_extension(cerpac_job_pk, attrs)

    if quota_job_pk:
        sync_quota_cerpac_gate(cerpac_job_pk, actor_id=actor_id)
    elif was_gated and job and job["status"] == "blocked":
        execute(
            "UPDATE job SET blocked_by = NULL, status = %s, status_reason = NULL WHERE id = %s",
            (STATUS_IN_PROGRESS, cerpac_job_pk),
        )


def sync_quota_cerpac_gate(cerpac_job_pk: int | None = None, actor_id: int | None = None) -> None:
    """Re-evaluate the quota->CERPAC gate. With a specific job, checks just
    that one (called right after linking); with none, sweeps every linked
    CERPAC-Principal job — the periodic safety net for pure time-based
    drift, e.g. a validity window crossing the 6-month line with nobody
    having touched any record."""
    if cerpac_job_pk:
        jobs = [models.get_job(cerpac_job_pk)]
    else:
        jobs = [
            j for j in models.list_jobs(category="immigration", exclude_dismissed=True)
            if j["service_type"] == CERPAC_PRINCIPAL_SERVICE_CODE
        ]

    for job in jobs:
        if not job or job["status"] in (STATUS_DONE, STATUS_CLOSED, "dismissed"):
            continue
        attrs = models.get_job_extension(job["id"])
        quota_pk = attrs.get("linked_quota_job_id")
        if not quota_pk:
            continue

        _, days_remaining = quota_validity(quota_pk)
        gate_should_block = days_remaining is None or days_remaining < QUOTA_MIN_VALIDITY_DAYS
        gate_active = attrs.get("active_gate") == ACTIVE_GATE_QUOTA

        if gate_should_block and not gate_active:
            attrs["active_gate"] = ACTIVE_GATE_QUOTA
            models.set_job_extension(job["id"], attrs)
            execute(
                "UPDATE job SET blocked_by = %s, status = 'blocked', status_reason = %s WHERE id = %s",
                (quota_pk, _GATE_BLOCK_REASON, job["id"]),
            )
            if job["owner_id"] and job["owner_id"] != actor_id:
                models.create_notification(
                    job["owner_id"], "quota_gate_blocked", "job", job["id"],
                    f"{job['job_id']} blocked — linked quota position needs renewal (under 6 months validity)",
                )
        elif not gate_should_block and gate_active:
            attrs.pop("active_gate", None)
            models.set_job_extension(job["id"], attrs)
            execute(
                "UPDATE job SET blocked_by = NULL, status = %s, status_reason = NULL WHERE id = %s",
                (STATUS_IN_PROGRESS, job["id"]),
            )
            if job["owner_id"] and job["owner_id"] != actor_id:
                models.create_notification(
                    job["owner_id"], "quota_gate_unblocked", "job", job["id"],
                    f"{job['job_id']} unblocked — linked quota position renewed, work can proceed",
                )


def list_cerpac_jobs_with_quota_link() -> list:
    """(cerpac_job, quota_job) pairs for every non-dismissed CERPAC-Principal
    job that has a linked quota — used by the module dashboard."""
    jobs = [
        j for j in models.list_jobs(category="immigration", exclude_dismissed=True)
        if j["service_type"] == CERPAC_PRINCIPAL_SERVICE_CODE
    ]
    result = []
    for j in jobs:
        quota_pk = models.get_job_extension(j["id"]).get("linked_quota_job_id")
        if quota_pk:
            result.append((j, models.get_job(quota_pk)))
    return result
