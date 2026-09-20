"""The job detail page — the one place to see and act on everything for a
single job. Every clickable job row in the app converges here: clicking a
job IS how you update it, there's no separate "go to update page" step."""

from __future__ import annotations

from datetime import date, datetime, timezone

import streamlit as st

from core import cit
from core import immigration
from core import models
from core import ui
from core.constants import (
    CATEGORY_LABELS,
    FORCE_DELETE_PIN,
    INVOICE_STATUS_LABELS,
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_PRINCIPAL,
    ROLE_SPECIALIST,
    ROLE_SUPER_ADMIN,
    SOURCE_LABELS,
    STATUS_BLOCKED,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_LABELS,
    STATUS_NEW,
    humanize,
    titlecase_name,
)

# Every module's custom gate (job_extension.attributes['active_gate']) gets
# its own message here — the dispatch itself is generic, so a future
# module's gate only ever needs a one-line addition to this dict.
_GATE_MESSAGES = {
    immigration.ACTIVE_GATE_QUOTA: (
        "Blocked — linked quota position has less than 6 months validity remaining. "
        "This unblocks automatically once the quota is renewed."
    ),
    cit.ACTIVE_GATE_TCC: (
        "Blocked — this client has other outstanding CIT obligations. "
        "This unblocks automatically once they're all cleared."
    ),
}


def render(user: dict, job_pk: int) -> None:
    ui.back_button("← Back to list")

    job = models.get_job(job_pk)
    if job is None:
        st.error("That job no longer exists.")
        return

    if user["role"] == "client" or (user["role"] == ROLE_SPECIALIST and job["owner_id"] != user["id"]):
        st.error("You don't have access to this job.")
        return

    key_prefix = f"jd_{job['id']}"

    # The start-job gate: until the owner explicitly starts their own New
    # job, ticking documents, commenting and changing status all stay locked
    # — "Start job" below is the only unlocked action. This is a restriction
    # on the owner's own workflow as a working queue-holder, not a business
    # rule, so it applies to a specialist or a manager owning their own job
    # the same way, and never to anyone else (super_admin included) or to a
    # manager looking at a job they don't own — their broader oversight
    # access there is untouched.
    gate_active = (
        user["role"] in (ROLE_SPECIALIST, ROLE_MANAGER)
        and job["owner_id"] == user["id"]
        and job["status"] == STATUS_NEW
    )

    _header(job, user)
    st.write("")

    if gate_active:
        _start_job_gate(job, key_prefix, user)
        st.divider()

    _blocking_alert(job)
    _info(job)
    if job["category"] == "immigration":
        st.divider()
        _immigration_section(job, user, gate_active=gate_active)
    elif job["category"] == "cit":
        st.divider()
        _cit_section(job, user, gate_active=gate_active)
    st.divider()

    # Comments live near the top-level job info — the conversation about a
    # job is as central as its status, not an afterthought at the bottom of
    # a long page of actions and invoicing detail.
    _comments(job, user, locked=gate_active)
    st.divider()

    editable = user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN) or (
        user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"]
    )

    if editable and job["status"] not in (STATUS_CLOSED, STATUS_DISMISSED):
        if not gate_active:
            # The gate's own "Start job" control above already covers this
            # exact New-status action — showing it again here would just
            # duplicate it.
            _status_actions(job, key_prefix, user)
            st.divider()
        _reassign_owner_control(job, key_prefix, user)
        st.divider()
        _edit_job_code_control(job, key_prefix, user)
        st.divider()
        _edit_job_details_control(job, key_prefix, user)
        st.divider()
        _dependency_control(job, key_prefix, user["id"])
        st.divider()
        _notes_editor(job, key_prefix)
        st.divider()
        _duplicate_control(job, key_prefix, user)

    # Rabbi invoices up front — as soon as a job is logged, not only once it's
    # done — so this section (and Create invoice within it) is available at
    # any non-dismissed status for admin/principal, not gated to done/closed.
    show_invoice_section = (
        user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN) and job["status"] != STATUS_DISMISSED
    ) or (user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"] and job["invoice_code"])
    if show_invoice_section:
        _invoice_section(job, user)
        st.divider()

    can_see_expenses = user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN) or (
        user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"]
    )
    if can_see_expenses:
        _expenses(job, user)
        st.divider()

    if user["role"] == ROLE_SUPER_ADMIN:
        st.divider()
        _danger_zone(job, user)


def _header(job: dict, user: dict) -> None:
    st.markdown(f'<div class="rc-page-title">{job["job_id"]}</div>', unsafe_allow_html=True)
    badges = ui.status_badge_html(job["status"]) + "&nbsp;&nbsp;" + ui.risk_badge_html(models.compute_risk(job))
    time_badge = _time_on_job_badge_html(job, user)
    if time_badge:
        badges += "&nbsp;&nbsp;" + time_badge
    st.markdown(badges, unsafe_allow_html=True)
    st.markdown(
        f'<div class="rc-job-title" style="margin-top:0.5rem;">{job["title"]}</div>',
        unsafe_allow_html=True,
    )


def _time_on_job_badge_html(job: dict, user: dict) -> str | None:
    """Compact elapsed-time badge: "On for X" while in progress, "Took X"
    once done — both read from the same started_at/completed_at pair the
    workload report uses. Visible to the owning specialist, admin, manager,
    EC (principal) and super_admin; nobody else, same as the expense log."""
    can_see = user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN) or (
        user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"]
    )
    if not can_see or not job.get("started_at"):
        return None
    if job.get("completed_at"):
        label = f"Took {models.format_duration(job['started_at'], job['completed_at'])}"
    else:
        label = f"On for {models.format_duration(job['started_at'], datetime.now(timezone.utc))}"
    return f'<span class="rc-badge rc-badge-grey">⏱ {label}</span>'


def _blocking_alert(job: dict) -> None:
    """Surfaced prominently: this job is holding up someone else's work."""
    if not job.get("blocking_count"):
        return
    dependents = models.list_blocked_dependents(job["id"])
    names = ", ".join(f"{d['job_id']} ({titlecase_name(d['owner_name']) or 'unassigned'})" for d in dependents)
    st.error(f"⛔ Blocking {job['blocking_count']} other job(s) — {names}")


def _info(job: dict) -> None:
    c1, c2 = st.columns(2)
    with c1:
        if job["client_id"]:
            cc1, cc2 = st.columns([0.9, 3])
            cc1.write("**Client:**")
            if cc2.button(titlecase_name(job["client_name"]) or "—", key=f"jd_client_{job['id']}", type="tertiary"):
                ui.go_to_client(job["client_id"])
        else:
            st.write(f"**Client:** {titlecase_name(job['client_name']) or '—'}")
        st.write(f"**Service:** {job['service_name'] or '—'}")
        st.write(f"**Category:** {humanize(job['category'], CATEGORY_LABELS)}")
        st.write(f"**Owner:** {titlecase_name(job['owner_name']) or '—'}")
        st.write(f"**Source:** {humanize(job['source'], SOURCE_LABELS)}")
    with c2:
        st.write(f"**Logged:** {job['created_at'].strftime('%d %b %Y')}")
        st.write(f"**SLA date:** {job['sla_date'].isoformat() if job['sla_date'] else '—'}")
        if job["blocked_by_job_code"]:
            blocker_state = "resolved" if not models.is_actually_blocked(job) else "unresolved"
            st.write(f"**Depends on:** {job['blocked_by_job_code']} ({blocker_state})")

    attrs = models.get_job_extension(job["id"])
    if attrs:
        service = models.get_service(job["service_type"]) if job["service_type"] else None
        field_labels = {f["key"]: f["label"] for f in (service["fields"] if service else [])}
        bits = []
        for k, v in attrs.items():
            if k == "subject_count":
                continue
            if k == "subject_labels":
                bits.append(f"Subjects ({attrs.get('subject_count', len(v))}): " + ", ".join(v))
            elif k == "quantity":
                bits.append(f"Quantity: {v}")
            else:
                bits.append(f"{field_labels.get(k, k)}: {v}")
        if bits:
            st.write("**Details:** " + " · ".join(bits))

    if job["description"]:
        st.write(f"**Description:** {job['description']}")
    if job["waiting_on_client"]:
        st.info(f"**Waiting on client:** {job['waiting_on_client']}")
    if job["dismissed_reason"]:
        st.write(f"**Dismissed — reason:** {job['dismissed_reason']}")
    if job["status_reason"]:
        st.write(f"**Reason ({humanize(job['status'], STATUS_LABELS)}):** {job['status_reason']}")
    if job["internal_notes"]:
        st.caption(f"Internal notes: {job['internal_notes']}")


def _document_checklist_section(
    job: dict, user: dict, *, on_expiry_change=None, gate_locked: bool = False,
) -> bool:
    """Generic across every module: the checklist + readiness badge, ticked
    off document by document. `on_expiry_change`, if given, is called after
    an expiry date is edited — the one module-specific follow-up (e.g.
    immigration's quota-gate re-check) a checklist edit can trigger.
    `gate_locked` is the specialist start-job gate — while active it turns
    off editing for the specialist owner only; every other role keeps its
    usual access. Returns whether this job's owner is allowed to edit it,
    so callers can reuse the same editable check for their own
    module-specific controls below it."""
    docs = models.list_job_documents(job["id"])
    received, required = models.job_document_readiness(job["id"])
    if required == 0:
        st.caption("No fixed checklist for this service — documents depend on what's requested at the time.")
    else:
        ready = received == required
        badge_color = "green" if ready else ("amber" if received > 0 else "grey")
        ready_suffix = " — ready to submit" if ready else ""
        st.markdown(
            f'<span class="rc-badge rc-badge-{badge_color}">'
            f'Readiness: {received}/{required} documents{ready_suffix}</span>',
            unsafe_allow_html=True,
        )
        st.write("")

    editable = (
        user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN)
        or (user["role"] == ROLE_MANAGER and not gate_locked)
        or (user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"] and not gate_locked)
    )
    if gate_locked and docs:
        st.caption("🔒 Locked until you start this job.")

    for d in docs:
        cols = st.columns([0.4, 2.4, 1.3, 1.1])
        checked = cols[0].checkbox(
            "", value=d["received"], key=f"jdoc_{d['id']}", disabled=not editable, label_visibility="collapsed",
        )
        cols[1].write(d["document_name"])
        if checked != d["received"]:
            models.set_job_document_received(d["id"], checked)
            st.rerun()

        if d["has_expiry"]:
            new_expiry = cols[2].date_input(
                "Expiry", value=d["expiry_date"], key=f"jdocexp_{d['id']}", disabled=not editable,
                label_visibility="collapsed",
            )
            if new_expiry != d["expiry_date"]:
                models.set_job_document_expiry(d["id"], new_expiry)
                if on_expiry_change:
                    on_expiry_change()
                st.rerun()
            if d["expiry_date"]:
                cols[3].caption(models.EXPIRY_URGENCY_LABELS[models.expiry_urgency(d["expiry_date"])])
        else:
            cols[2].write("—")

    return editable


def _immigration_section(job: dict, user: dict, *, gate_active: bool = False) -> None:
    st.markdown("#### Immigration checklist")
    editable = _document_checklist_section(
        job, user, on_expiry_change=lambda: immigration.sync_quota_cerpac_gate(actor_id=user["id"]),
        gate_locked=gate_active,
    )

    if job["service_type"] == immigration.CERPAC_PRINCIPAL_SERVICE_CODE:
        st.write("")
        _quota_link_control(job, user, editable)


def _quota_link_control(job: dict, user: dict, editable: bool) -> None:
    with st.expander("Linked quota position", expanded=True):
        candidates = immigration.list_quota_candidates(job["client_id"])
        options = {"— no linked quota —": None}
        options.update({f"{c['job_id']} — {c['title']}": c["id"] for c in candidates})

        linked_pk = immigration.get_linked_quota_job_id(job)
        current_label = "— no linked quota —"
        for label, pk in options.items():
            if pk == linked_pk:
                current_label = label
                break

        if linked_pk:
            _, days_remaining = immigration.quota_validity(linked_pk)
            if days_remaining is None:
                st.warning(
                    "Linked quota has no recorded approval expiry yet — treated as not valid "
                    "until one is entered on the quota job's own checklist."
                )
            elif days_remaining < immigration.QUOTA_MIN_VALIDITY_DAYS:
                st.error(
                    f"⛔ Quota validity: {days_remaining} day(s) remaining — under the 6-month "
                    "minimum. This job is blocked until the quota is renewed."
                )
            else:
                st.success(f"✅ Quota validity: {days_remaining} day(s) remaining.")

        if not editable:
            st.caption(current_label)
            return

        choice = st.selectbox(
            "Linked quota job", options=list(options.keys()),
            index=list(options.keys()).index(current_label), key=f"jd_{job['id']}_quotalink",
        )
        if st.button("Save quota link", key=f"jd_{job['id']}_savequota"):
            immigration.set_quota_link(job["id"], options[choice], actor_id=user["id"])
            st.toast("Saved.", icon="✅")
            st.rerun()


def _cit_section(job: dict, user: dict, *, gate_active: bool = False) -> None:
    st.markdown("#### CIT checklist")
    _document_checklist_section(job, user, gate_locked=gate_active)

    frequency = models.is_recurring_service(job["service_type"]) if job["service_type"] else None
    if frequency:
        st.write("")
        _recurring_info(job, frequency)

    if job["service_type"] == cit.TCC_SERVICE_CODE:
        st.write("")
        _tcc_obligations_panel(job)

    if job["service_type"] == cit.ANNUAL_RETURN_SERVICE_CODE:
        desk_exam_id = models.get_job_extension(job["id"]).get("desk_exam_job_id")
        if desk_exam_id:
            st.write("")
            desk_exam = models.get_job(desk_exam_id)
            if desk_exam:
                st.info("📋 Desk Examination auto-created for this filing:")
                if st.button(
                    f"{desk_exam['job_id']} — {desk_exam['title']}", key=f"jd_{job['id']}_deskexam", type="tertiary",
                ):
                    ui.go_to_job(desk_exam["id"])

    triggered_by = models.get_job_extension(job["id"]).get("triggered_by_annual_return_job_id")
    if triggered_by:
        st.write("")
        parent = models.get_job(triggered_by)
        if parent:
            st.caption("Auto-created following the Annual Return filing below.")
            if st.button(f"{parent['job_id']} — {parent['title']}", key=f"jd_{job['id']}_parentreturn", type="tertiary"):
                ui.go_to_job(parent["id"])


def _recurring_info(job: dict, frequency: str) -> None:
    attrs = models.get_job_extension(job["id"])
    with st.expander(f"Recurring — {frequency}", expanded=False):
        st.caption(
            f"This service recurs {frequency}. Marking this job done automatically creates the next cycle's job."
        )
        prev_id = attrs.get("previous_cycle_job_id")
        next_id = attrs.get("next_cycle_job_id")
        if prev_id:
            prev_job = models.get_job(prev_id)
            if prev_job and st.button(
                f"Previous cycle: {prev_job['job_id']}", key=f"jd_{job['id']}_prevcycle", type="tertiary",
            ):
                ui.go_to_job(prev_job["id"])
        if next_id:
            next_job = models.get_job(next_id)
            if next_job and st.button(
                f"Next cycle: {next_job['job_id']}", key=f"jd_{job['id']}_nextcycle", type="tertiary",
            ):
                ui.go_to_job(next_job["id"])
        elif not prev_id and not next_id:
            st.caption("No other cycle yet — the next one is created automatically once this is marked done.")


def _tcc_obligations_panel(job: dict) -> None:
    with st.expander("Outstanding CIT obligations", expanded=True):
        outstanding = cit.list_outstanding_obligations(job["client_id"], exclude_job_pk=job["id"])
        if outstanding:
            st.error(
                f"⛔ {len(outstanding)} other outstanding CIT job(s) for this client — "
                "this TCC is blocked until they're all done or closed."
            )
            for o in outstanding:
                if st.button(
                    f"{o['job_id']} — {o['service_name'] or o['title']} ({o['status']})",
                    key=f"jd_{job['id']}_obl_{o['id']}", type="tertiary",
                ):
                    ui.go_to_job(o["id"])
        else:
            st.success("✅ No other outstanding CIT obligations for this client.")


def _start_job_gate(job: dict, key_prefix: str, user: dict) -> None:
    """The specialist start-job gate, rendered at the very top of the page
    so it's the first and only thing they can act on before starting:
    ticking documents, posting comments and every other status action stay
    locked (see _document_checklist_section/_comments's gate_locked/locked
    params) until this succeeds. Reuses the same invoice-before-work check
    as the normal Start-work action — a specialist still can't jump that
    queue, they just see the gate framed as "start this job" rather than
    buried under the checklist."""
    st.markdown("#### Start job")
    can_start, block_reason = models.can_start_work(job)
    if can_start:
        st.info("Documents, comments and status stay locked until you start this job.")
        if st.button("Start job", key=f"{key_prefix}_gate_start", type="primary"):
            _apply_status(job["id"], STATUS_IN_PROGRESS, actor_id=user["id"])
    else:
        st.warning(f"Can't start yet — {block_reason}")
        st.caption("Documents, comments and status stay locked until this job is started.")


def _status_actions(job: dict, key_prefix: str, user: dict) -> None:
    st.markdown("#### Status")
    actor_id = user["id"]
    role = user["role"]
    blocked_now = models.is_actually_blocked(job)

    if blocked_now:
        st.warning(f"Blocked by **{job['blocked_by_job_code']}** — resolve that job first.")
        return

    status = job["status"]

    if status == STATUS_NEW:
        can_start, block_reason = models.can_start_work(job)
        if can_start:
            if job.get("start_override_by"):
                st.caption(f"Principal override on file — {job.get('start_override_reason') or 'start allowed'}.")
            if st.button("Start work", key=f"{key_prefix}_start"):
                _apply_status(job["id"], STATUS_IN_PROGRESS, actor_id=actor_id)
        else:
            st.info(f"Can't start yet — {block_reason}")
            # Overriding the invoice-before-work rule is exactly the kind of
            # "override system rules" authority reserved to EC/super_admin —
            # manager is deliberately never added here.
            if role in (ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
                with st.form(key=f"{key_prefix}_override_form"):
                    st.write("Allow this job to start without an approved invoice")
                    reason = st.text_input("Reason for the override *", key=f"{key_prefix}_override_reason")
                    if st.form_submit_button("Allow start"):
                        if not reason.strip():
                            st.error("A reason is required to override the invoice gate.")
                        else:
                            models.set_start_override(job["id"], actor_id, reason.strip())
                            st.toast("Override recorded — the specialist can now start work.", icon="✅")
                            st.rerun()

    elif status == STATUS_IN_PROGRESS:
        is_owning_worker = role in (ROLE_SPECIALIST, ROLE_MANAGER) and job["owner_id"] == actor_id
        if role in (ROLE_PRINCIPAL, ROLE_SUPER_ADMIN) or is_owning_worker:
            c1, c2 = st.columns(2)
            with c1:
                with st.form(key=f"{key_prefix}_done_form"):
                    st.write("Mark done")
                    reason = st.text_area("What was completed? *", key=f"{key_prefix}_done_reason")
                    if st.form_submit_button("Mark done"):
                        if not reason.strip():
                            st.error("A reason is required to mark a job done.")
                        else:
                            _apply_status(job["id"], STATUS_DONE, reason.strip(), actor_id)
            with c2:
                with st.form(key=f"{key_prefix}_block_form"):
                    st.write("Mark blocked")
                    reason = st.text_area("What is this blocked on? *", key=f"{key_prefix}_block_reason")
                    if st.form_submit_button("Mark blocked"):
                        if not reason.strip():
                            st.error("A reason is required to mark a job blocked.")
                        else:
                            _apply_status(job["id"], STATUS_BLOCKED, reason.strip(), actor_id)
        else:
            st.caption("Only the owner of this job, or the principal, can mark it done or blocked.")

    elif status == STATUS_BLOCKED:
        # Reaching here means blocked_now was False above — the dependency
        # (if any) is resolved, or this was a manual block with no
        # dependency — either way, resuming is normally available. The one
        # exception: a module's own custom gate (immigration's quota
        # validity, CIT's TCC obligations) is driven by something other
        # than "is the blocker done/closed," so is_actually_blocked() can
        # read False here even while the gate is still active — never let a
        # manual Resume bypass it. Only that gate's own sync function clears
        # it, automatically, once the underlying condition resolves.
        gate_kind = models.get_job_extension(job["id"]).get("active_gate")
        if gate_kind:
            st.warning(_GATE_MESSAGES.get(gate_kind, "Blocked by an active dependency gate."))
            return
        if st.button("Resume — in progress", key=f"{key_prefix}_resume"):
            _apply_status(job["id"], STATUS_IN_PROGRESS, actor_id=actor_id)


def _apply_status(job_pk: int, new_status: str, reason: str | None = None, actor_id: int | None = None) -> None:
    try:
        models.set_status(job_pk, new_status, reason, actor_id=actor_id)
    except models.JobRuleError as e:
        st.error(str(e))
        return

    if new_status == STATUS_DONE:
        # Generic: any recurring service spawns its next cycle automatically.
        models.create_next_cycle_job(job_pk, actor_id=actor_id)
        job = models.get_job(job_pk)
        if job and job["category"] == "cit":
            if job["service_type"] == cit.ANNUAL_RETURN_SERVICE_CODE:
                cit.spawn_desk_examination(job_pk, actor_id=actor_id)
            # This job finishing may be exactly what an outstanding TCC for
            # the same client was waiting on.
            cit.sync_tcc_gate(actor_id=actor_id)

    st.toast("Status updated.", icon="✅")
    st.rerun()


def _reassign_owner_control(job: dict, key_prefix: str, user: dict) -> None:
    """admin/manager/super_admin: change who owns this job after creation.
    Shares models.reassign_owner with the register's bulk-assign action —
    the same primitive, just applied to one job at a time here."""
    if user["role"] not in (ROLE_ADMIN, ROLE_MANAGER, ROLE_SUPER_ADMIN):
        return
    with st.expander("Reassign owner"):
        staff = [s for s in models.list_staff(active_only=True) if s["role"] != "client"]
        options = [ui.staff_label(s) for s in staff]
        staff_by_name = {ui.staff_label(s): s["id"] for s in staff}
        index = next((i for i, s in enumerate(staff) if s["id"] == job["owner_id"]), None)
        choice = st.selectbox(
            "Owner", options=options, index=index, placeholder="Select owner…",
            key=f"{key_prefix}_reassign",
        )
        if st.button("Save owner", key=f"{key_prefix}_savereassign"):
            if not choice:
                st.error("Select an owner.")
            else:
                changed = models.reassign_owner(job["id"], staff_by_name[choice], actor_id=user["id"])
                st.toast(f"Reassigned to {choice}." if changed else "Already owned by this person.", icon="✅")
                st.rerun()


def _edit_job_code_control(job: dict, key_prefix: str, user: dict) -> None:
    """EC (principal)/admin/manager/super_admin: correct or manually set
    this job's number. job_id is normally auto-generated and never touched
    again — this exists for the rare "this was set wrong" case, and every
    change is logged (who, when, old -> new) so a correction is never
    silent."""
    if user["role"] not in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        return
    with st.expander("Edit job number"):
        new_code = st.text_input("Job ID", value=job["job_id"], key=f"{key_prefix}_jobcode")
        if st.button("Save job number", key=f"{key_prefix}_savejobcode"):
            try:
                models.update_job_code(job["id"], new_code, actor_id=user["id"])
            except models.CodeEditError as e:
                st.error(str(e))
            else:
                st.toast("Job number updated.", icon="✅")
                st.rerun()

        edits = models.list_code_edits("job", job["id"])
        if edits:
            st.caption("Edit history:")
            for e in edits:
                st.caption(
                    f"{e['old_code']} → {e['new_code']} — {titlecase_name(e['changed_by_name']) or '—'}, "
                    f"{e['changed_at'].strftime('%d %b %Y, %H:%M')}"
                )


def _edit_job_details_control(job: dict, key_prefix: str, user: dict) -> None:
    """EC (principal)/admin/manager/super_admin: correct this job's title
    or description — every change is logged (who, when, old -> new)."""
    if user["role"] not in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        return
    with st.expander("Edit title / description"):
        new_title = st.text_input("Title", value=job["title"], key=f"{key_prefix}_edittitle")
        new_description = st.text_area(
            "Description", value=job["description"] or "", key=f"{key_prefix}_editdesc",
        )
        if st.button("Save changes", key=f"{key_prefix}_savedetails"):
            try:
                models.update_job_details(
                    job["id"], title=new_title, description=new_description, actor_id=user["id"],
                )
            except models.FieldEditError as e:
                st.error(str(e))
            else:
                st.toast("Job details updated.", icon="✅")
                st.rerun()

        edits = models.list_field_edits("job", job["id"])
        if edits:
            st.caption("Edit history:")
            for e in edits:
                st.caption(
                    f"{e['field'].capitalize()}: {e['old_value'] or '—'} → {e['new_value'] or '—'} — "
                    f"{titlecase_name(e['changed_by_name']) or '—'}, {e['changed_at'].strftime('%d %b %Y, %H:%M')}"
                )


def _dependency_control(job: dict, key_prefix: str, actor_id: int) -> None:
    with st.expander("Dependency"):
        candidates = [
            j for j in models.list_jobs(exclude_dismissed=True)
            if j["id"] != job["id"] and j["client_id"] == job["client_id"]
        ]
        options = {"— no dependency —": None}
        options.update({f"{c['job_id']} — {c['title']}": c["id"] for c in candidates})

        current_label = "— no dependency —"
        if job["blocked_by"]:
            for label, pk in options.items():
                if pk == job["blocked_by"]:
                    current_label = label
                    break

        choice = st.selectbox(
            "Blocked by (dependency)", options=list(options.keys()),
            index=list(options.keys()).index(current_label), key=f"{key_prefix}_dep",
        )
        if st.button("Save dependency", key=f"{key_prefix}_savedep"):
            try:
                models.set_blocked_by(job["id"], options[choice], actor_id=actor_id)
            except models.JobRuleError as e:
                st.error(str(e))
            else:
                st.toast("Saved.", icon="✅")
                st.rerun()


def _notes_editor(job: dict, key_prefix: str) -> None:
    with st.expander("Internal notes & waiting-on-client"):
        notes = st.text_area("Internal notes", value=job["internal_notes"] or "", key=f"{key_prefix}_notes")
        waiting = st.text_input(
            "Waiting on client for…", value=job["waiting_on_client"] or "", key=f"{key_prefix}_waiting"
        )
        if st.button("Save notes", key=f"{key_prefix}_savenotes"):
            models.update_job_fields(job["id"], internal_notes=notes or None, waiting_on_client=waiting or None)
            st.toast("Saved.", icon="✅")
            st.rerun()


def _duplicate_control(job: dict, key_prefix: str, user: dict) -> None:
    """Admin/manager/super_admin way to remove a genuine duplicate without a
    hard delete — dismisses it with a reason, same as any other dismissal,
    so the record and its audit trail stay intact."""
    if user["role"] not in (ROLE_ADMIN, ROLE_MANAGER, ROLE_SUPER_ADMIN):
        return
    with st.expander("Mark as duplicate"):
        st.caption(
            "If this job is a genuine duplicate of another one already logged, dismiss it here — "
            "the record stays for audit, it just won't show as active work."
        )
        reason = st.text_input(
            "Duplicate of / reason *", key=f"{key_prefix}_dupreason",
            placeholder="e.g. Duplicate of JOB-2026-0007",
        )
        if st.button("Mark as duplicate", key=f"{key_prefix}_markdup"):
            if not reason.strip():
                st.error("Enter which job this duplicates, or why.")
            else:
                try:
                    models.set_status(
                        job["id"], STATUS_DISMISSED, f"Duplicate — {reason.strip()}", actor_id=user["id"]
                    )
                except models.JobRuleError as e:
                    st.error(str(e))
                else:
                    if job["category"] == "cit":
                        cit.sync_tcc_gate(actor_id=user["id"])
                    st.toast("Marked as duplicate.", icon="✅")
                    st.rerun()


def _invoice_section(job: dict, user: dict) -> None:
    st.markdown("#### Invoice")

    if job["invoice_code"]:
        status_label = humanize(job["invoice_status"], INVOICE_STATUS_LABELS)
        if st.button(f"{job['invoice_code']} — {status_label}", key=f"jd_openinv_{job['id']}", type="tertiary"):
            ui.go_to_invoice(job["invoice_id"])
    else:
        st.caption("Not yet invoiced.")
        if user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_SUPER_ADMIN) and job["status"] != STATUS_DISMISSED:
            if st.button("Create invoice", key=f"jd_createinv_{job['id']}", type="primary"):
                st.session_state["invoice_seed_job"] = job["id"]
                st.session_state["invoice_seed_client"] = None
                st.session_state["invoice_revise_id"] = None
                ui.go_to_create_invoice()

    invoice_closable_roles = (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN)
    if user["role"] in invoice_closable_roles and job["status"] == STATUS_DONE and job["invoice_id"]:
        if job["invoice_status"] not in ("approved", "paid"):
            st.info(f"Waiting on approval for invoice **{job['invoice_code']}** before this job can close.")
        else:
            if st.button("Mark closed", key=f"close_{job['id']}", type="primary"):
                try:
                    models.close_job(job["id"])
                except models.JobRuleError as e:
                    st.error(str(e))
                else:
                    if job["category"] == "cit":
                        cit.sync_tcc_gate(actor_id=user["id"])
                    st.toast("Job closed.", icon="✅")
                    st.rerun()


_EXPENSE_WIDTHS = [2.6, 1.3, 1.1, 1.4]


def _expenses(job: dict, user: dict) -> None:
    st.markdown("#### Expenses")
    expenses = models.list_job_expenses(job["id"])
    if expenses:
        header = st.columns(_EXPENSE_WIDTHS)
        for col, label in zip(header, ["Description", "Amount", "Date", "Added by"]):
            col.markdown(f"**{label}**")
        total = 0.0
        for e in expenses:
            cols = st.columns(_EXPENSE_WIDTHS)
            cols[0].write(e["description"])
            cols[1].write(f"₦{float(e['amount']):,.2f}")
            cols[2].write(e["expense_date"].isoformat())
            cols[3].write(titlecase_name(e["created_by_name"]) or "—")
            total += float(e["amount"])
        st.caption(f"Total logged: ₦{total:,.2f}")
    else:
        st.caption("No expenses logged yet.")

    if user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_SUPER_ADMIN):
        with st.form(key=f"expense_form_{job['id']}", clear_on_submit=True):
            st.write("Add expense")
            c1, c2, c3 = st.columns([2, 1, 1])
            desc = c1.text_input("Description", label_visibility="collapsed", placeholder="Description")
            amount = c2.number_input("Amount", min_value=0.0, step=100.0, format="%.2f", label_visibility="collapsed")
            edate = c3.date_input("Date", value=date.today(), label_visibility="collapsed")
            if st.form_submit_button("Add expense"):
                if not desc.strip() or amount <= 0:
                    st.error("Enter a description and an amount greater than zero.")
                else:
                    models.add_job_expense(job["id"], desc.strip(), amount, edate, user["id"])
                    st.toast("Expense added.", icon="✅")
                    st.rerun()


def _comments(job: dict, user: dict, *, locked: bool = False) -> None:
    st.markdown("#### Comments")
    if locked:
        st.caption("🔒 Locked until you start this job.")
    else:
        with st.form(key=f"comment_form_{job['id']}", clear_on_submit=True):
            body = st.text_area(
                "Add a comment", label_visibility="collapsed", placeholder="Add a comment…",
                key=f"comment_body_{job['id']}",
            )
            if st.form_submit_button("Post comment"):
                if body.strip():
                    models.add_job_comment(job["id"], user["id"], body.strip())
                    st.toast("Comment posted.", icon="✅")
                    st.rerun()

    comments = models.list_job_comments(job["id"])
    if not comments:
        st.caption("No comments yet.")
    for c in comments:
        st.markdown(f"**{titlecase_name(c['author_name'])}** · {c['created_at'].strftime('%d %b %Y, %H:%M')}")
        st.write(c["body"])
        st.divider()


def _danger_zone(job: dict, user: dict) -> None:
    """super_admin only. Hide is the everyday cleanup action — soft,
    reversible, one click. Delete is real and permanent, kept visually and
    behaviourally separate: its own expander, its own explicit confirmation,
    never a single click away."""
    with st.expander("⚠️ Danger zone (super admin)"):
        st.caption(
            "Hiding removes this job from every view and every count for every role — "
            "it's still in the database and can be restored from **Hidden Jobs**."
        )
        if st.button("Hide this job", key=f"hide_{job['id']}"):
            models.hide_jobs([job["id"]], user["id"])
            st.toast(f"{job['job_id']} hidden.", icon="✅")
            ui.clear_all_nav()
            st.rerun()

        st.divider()
        st.markdown("**Delete permanently**")
        st.caption("This cannot be undone. The record, its comments, expenses and documents are all gone for good.")
        confirm = st.checkbox(
            f"Yes, permanently delete {job['job_id']} — I understand this cannot be undone.",
            key=f"delconfirm_{job['id']}",
        )
        if st.button("Delete permanently", key=f"del_{job['id']}", disabled=not confirm, type="primary"):
            try:
                models.delete_job(job["id"])
            except models.JobDeleteError as e:
                st.error(str(e))
            else:
                st.toast(f"{job['job_id']} permanently deleted.", icon="✅")
                ui.clear_all_nav()
                st.rerun()

        st.divider()
        st.markdown("**Force delete (bypass invoice check)**")
        st.caption(
            "Only needed when the delete above refuses because this job is on an invoice — the "
            "invoice line is removed but the invoice itself survives. Requires the 4-digit PIN."
        )
        force_confirm = st.checkbox(
            f"Yes, force-delete {job['job_id']} — I understand this cannot be undone.",
            key=f"forceconfirm_{job['id']}",
        )
        force_pin = st.text_input(
            "4-digit PIN", type="password", max_chars=4, key=f"forcepin_{job['id']}",
        )
        if st.button("Force delete", key=f"forcedel_{job['id']}", disabled=not force_confirm, type="primary"):
            if force_pin != FORCE_DELETE_PIN:
                st.error("Incorrect PIN.")
            else:
                models.delete_job(job["id"], force=True)
                st.toast(f"{job['job_id']} force-deleted.", icon="✅")
                ui.clear_all_nav()
                st.rerun()
