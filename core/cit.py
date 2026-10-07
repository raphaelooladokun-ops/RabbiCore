"""CIT module business rules — the two module-specific rules that can't
live in the generic job spine:

1. The TCC gate: a TCC (Tax Clearance Certificate) job is blocked while the
   client has any other open CIT job — an unresolved audit, investigation,
   desk examination, or an unfiled return. Unlike immigration's quota gate
   (date/validity-driven), this one IS status-driven — "outstanding" means
   "not done/closed yet" — but it still can't reuse job_status_guard's own
   blocked_by resolution directly, because there's no single fixed blocker
   job to point at ahead of time: it's whichever open CIT job is oldest at
   the moment, and that can change as jobs come and go. So it manages
   blocked_by + status itself via models.force_block/force_unblock, exactly
   like immigration's gate, and is excluded from the generic self-heal the
   same way (job_extension.attributes['active_gate']).

2. The Annual Return -> Desk Examination auto-trigger: NRS follows a filed
   Annual Return with a Desk Examination, so filing one isn't the end of
   the story — it's created automatically the moment the Annual Return is
   marked done, so it's tracked from day one rather than remembered later.

Everything reusable (the document checklist, expiry tracking,
module-specialist routing, and now recurring jobs) stays generic in
core/models.py — this file holds only what's genuinely CIT-specific,
exactly the split core/immigration.py established.
"""

from __future__ import annotations

from core import models
from core.constants import STATUS_CLOSED, STATUS_DONE

TCC_SERVICE_CODE = "CIT-TCC"
ANNUAL_RETURN_SERVICE_CODE = "CIT-ANNUAL-RETURN"
DESK_EXAM_SERVICE_CODE = "CIT-DESK-EXAM"

ACTIVE_GATE_TCC = "tcc_obligations"

_GATE_BLOCK_REASON = (
    "Blocked — this client has other outstanding CIT obligations (an audit, investigation, "
    "or unfiled return). All must be cleared before a TCC can be issued."
)


def list_outstanding_obligations(client_id: int | None, exclude_job_pk: int | None = None) -> list:
    """Every other open CIT job for this client — every CIT service counts
    (audits, investigations, desk exams, unfiled returns, registrations in
    progress...), not just the three named examples, matching the brief's
    broader "other outstanding CIT obligations." TCC's own gate reads this
    list directly; the CIT dashboard's panel does too.

    A recurring obligation (Monthly VAT Returns, VAT & WHT Monitoring,
    Annual Return) now lives on a shared checklist job covering every
    client due that period, rather than this client's own copy of it — so
    a plain client_id-filtered job query can't see it. This client's own
    unticked row on one of those is exactly the same "unfiled return" this
    gate exists to catch, just represented differently now."""
    if not client_id:
        return []
    jobs = models.list_jobs(client_id=client_id, category="cit", exclude_dismissed=True)
    outstanding = [
        j for j in jobs
        if j["service_type"] != TCC_SERVICE_CODE
        and j["status"] not in (STATUS_DONE, STATUS_CLOSED)
        and j["id"] != exclude_job_pk
    ]
    outstanding += [
        j for j in models.list_pending_recurring_obligations_for_client(client_id, category="cit")
        if j["id"] != exclude_job_pk
    ]
    return outstanding


def is_tcc_gate_active(job: dict) -> bool:
    return models.get_job_extension(job["id"]).get("active_gate") == ACTIVE_GATE_TCC


def sync_tcc_gate(tcc_job_pk: int | None = None, actor_id: int | None = None) -> None:
    """Re-evaluate the TCC gate. With a specific job, checks just that one
    (called right after it's created, or whenever any of this client's CIT
    jobs changes status); with none, sweeps every open TCC job — the
    periodic safety net, mirroring immigration's quota-gate sweep."""
    if tcc_job_pk:
        jobs = [models.get_job(tcc_job_pk)]
    else:
        jobs = [
            j for j in models.list_jobs(category="cit", exclude_dismissed=True)
            if j["service_type"] == TCC_SERVICE_CODE
        ]

    for job in jobs:
        if not job or job["status"] in (STATUS_DONE, STATUS_CLOSED, "dismissed") or not job["client_id"]:
            continue
        attrs = models.get_job_extension(job["id"])
        gate_active = attrs.get("active_gate") == ACTIVE_GATE_TCC
        outstanding = list_outstanding_obligations(job["client_id"], exclude_job_pk=job["id"])

        if outstanding and not gate_active:
            blocker = min(outstanding, key=lambda j: j["created_at"])
            attrs["active_gate"] = ACTIVE_GATE_TCC
            models.set_job_extension(job["id"], attrs)
            models.force_block(job["id"], blocker["id"], _GATE_BLOCK_REASON)
            if job["owner_id"] and job["owner_id"] != actor_id:
                models.create_notification(
                    job["owner_id"], "tcc_gate_blocked", "job", job["id"],
                    f"{job['job_id']} blocked — outstanding CIT obligations must clear first",
                )
        elif not outstanding and gate_active:
            attrs.pop("active_gate", None)
            models.set_job_extension(job["id"], attrs)
            models.force_unblock(job["id"])
            if job["owner_id"] and job["owner_id"] != actor_id:
                models.create_notification(
                    job["owner_id"], "tcc_gate_unblocked", "job", job["id"],
                    f"{job['job_id']} unblocked — outstanding CIT obligations cleared, TCC can proceed",
                )


def list_tcc_jobs_blocked() -> list:
    """(tcc_job, blocking_obligations) pairs for every TCC job currently
    gated — used by the module dashboard."""
    jobs = [
        j for j in models.list_jobs(category="cit", exclude_dismissed=True)
        if j["service_type"] == TCC_SERVICE_CODE and is_tcc_gate_active(j)
    ]
    return [(j, list_outstanding_obligations(j["client_id"], exclude_job_pk=j["id"])) for j in jobs]


def spawn_desk_examination(annual_return_job_pk: int, actor_id: int | None = None) -> dict | None:
    """A filed Annual Return isn't done in isolation — NRS follows up with a
    Desk Examination. Create that job automatically so it's tracked from
    day one, not remembered later. Idempotent per Annual Return filing:
    each recurring cycle's own Annual Return job gets its own Desk Exam,
    but the same Annual Return job never spawns a second one."""
    job = models.get_job(annual_return_job_pk)
    if not job or job["service_type"] != ANNUAL_RETURN_SERVICE_CODE:
        return None

    attrs = models.get_job_extension(job["id"])
    if attrs.get("desk_exam_job_id"):
        return None

    desk_exam = models.create_job(
        client_id=job["client_id"], category="cit", service_type=DESK_EXAM_SERVICE_CODE,
        title=f"Desk Examination — following {job['job_id']}",
        description=f"Auto-created: NRS desk examination following the Annual Return filing {job['job_id']}.",
        owner_id=job["owner_id"], source=job["source"], created_by=actor_id or job["created_by"],
        sla_date=None, attributes={},
    )

    desk_attrs = models.get_job_extension(desk_exam["id"])
    desk_attrs["triggered_by_annual_return_job_id"] = job["id"]
    models.set_job_extension(desk_exam["id"], desk_attrs)

    attrs["desk_exam_job_id"] = desk_exam["id"]
    models.set_job_extension(job["id"], attrs)

    if desk_exam["owner_id"]:
        models.create_notification(
            desk_exam["owner_id"], "desk_exam_triggered", "job", desk_exam["id"],
            f"{desk_exam['job_id']} auto-created — Desk Examination following Annual Return {job['job_id']}",
        )
    return desk_exam


def spawn_desk_examination_for_client(parent_job: dict, client_id: int, actor_id: int | None = None) -> dict | None:
    """The same follow-up as spawn_desk_examination, for an Annual Return
    that's now a shared checklist job covering many clients: each client's
    own filing still gets its own Desk Examination the moment THEIR row is
    ticked, exactly as it did back when Annual Return was a single-client
    job and marking the whole thing done triggered this. Idempotent per
    (parent job, client) — tracked in the parent's own job_extension as a
    client_id -> desk_exam_job_id map, since there's no longer just the
    one desk_exam_job_id a single-client job carries."""
    if parent_job["service_type"] != ANNUAL_RETURN_SERVICE_CODE:
        return None

    attrs = models.get_job_extension(parent_job["id"])
    desk_exam_map = attrs.get("desk_exam_job_ids", {})
    if str(client_id) in desk_exam_map:
        return None

    client = models.get_client(client_id)
    desk_exam = models.create_job(
        client_id=client_id, category="cit", service_type=DESK_EXAM_SERVICE_CODE,
        title=f"Desk Examination — following {parent_job['job_id']}",
        description=(
            f"Auto-created: NRS desk examination following {client['name'] if client else 'this client'}'s "
            f"Annual Return filing on {parent_job['job_id']}."
        ),
        owner_id=None, source=parent_job["source"], created_by=actor_id or parent_job["created_by"],
        sla_date=None, attributes={},
    )

    desk_attrs = models.get_job_extension(desk_exam["id"])
    desk_attrs["triggered_by_annual_return_job_id"] = parent_job["id"]
    models.set_job_extension(desk_exam["id"], desk_attrs)

    desk_exam_map[str(client_id)] = desk_exam["id"]
    attrs["desk_exam_job_ids"] = desk_exam_map
    models.set_job_extension(parent_job["id"], attrs)

    return desk_exam
