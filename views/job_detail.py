"""The job detail page — the one place to see and act on everything for a
single job. Every clickable job row in the app converges here: clicking a
job IS how you update it, there's no separate "go to update page" step."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import models
from core import ui
from core.constants import (
    CATEGORY_LABELS,
    INVOICE_STATUS_LABELS,
    ROLE_ADMIN,
    ROLE_PRINCIPAL,
    ROLE_SPECIALIST,
    SOURCE_LABELS,
    STATUS_BLOCKED,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_LABELS,
    STATUS_NEW,
    humanize,
)


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

    _header(job)
    st.write("")
    _blocking_alert(job)
    _info(job)
    st.divider()

    editable = user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL) or (
        user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"]
    )

    if editable and job["status"] not in (STATUS_CLOSED, STATUS_DISMISSED):
        _status_actions(job, key_prefix, user["id"])
        st.divider()
        _dependency_control(job, key_prefix, user["id"])
        st.divider()
        _notes_editor(job, key_prefix)
        st.divider()
        _duplicate_control(job, key_prefix, user)

    show_invoice_section = (
        user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL) and job["status"] in (STATUS_DONE, STATUS_CLOSED)
    ) or (user["role"] == ROLE_SPECIALIST and job["owner_id"] == user["id"] and job["invoice_code"])
    if show_invoice_section:
        _invoice_section(job, user)
        st.divider()

    if user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL):
        _expenses(job, user)
        st.divider()

    _comments(job, user)


def _header(job: dict) -> None:
    st.markdown(f'<div class="rc-page-title">{job["job_id"]}</div>', unsafe_allow_html=True)
    badges = ui.status_badge_html(job["status"]) + "&nbsp;&nbsp;" + ui.risk_badge_html(models.compute_risk(job))
    st.markdown(badges, unsafe_allow_html=True)
    st.markdown(
        f'<div class="rc-page-subtitle" style="margin-top:0.5rem;">{job["title"]}</div>',
        unsafe_allow_html=True,
    )


def _blocking_alert(job: dict) -> None:
    """Surfaced prominently: this job is holding up someone else's work."""
    if not job.get("blocking_count"):
        return
    dependents = models.list_blocked_dependents(job["id"])
    names = ", ".join(f"{d['job_id']} ({d['owner_name'] or 'unassigned'})" for d in dependents)
    st.error(f"⛔ Blocking {job['blocking_count']} other job(s) — {names}")


def _info(job: dict) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.write(f"**Client:** {job['client_name'] or '—'}")
        st.write(f"**Service:** {job['service_name'] or '—'}")
        st.write(f"**Category:** {humanize(job['category'], CATEGORY_LABELS)}")
        st.write(f"**Owner:** {job['owner_name'] or '—'}")
        st.write(f"**Source:** {humanize(job['source'], SOURCE_LABELS)}")
    with c2:
        st.write(f"**Logged:** {job['created_at'].strftime('%d %b %Y')}")
        st.write(f"**SLA date:** {job['sla_date'].isoformat() if job['sla_date'] else '—'}")
        if job["blocked_by"]:
            blocker_state = "resolved" if not models.is_actually_blocked(job) else "unresolved"
            st.write(f"**Depends on:** {job['blocked_by_job_code']} ({blocker_state})")

    attrs = models.get_job_extension(job["id"])
    if attrs:
        service = models.get_service(job["service_type"]) if job["service_type"] else None
        field_labels = {f["key"]: f["label"] for f in (service["fields"] if service else [])}
        st.write("**Details:** " + " · ".join(f"{field_labels.get(k, k)}: {v}" for k, v in attrs.items()))

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


def _status_actions(job: dict, key_prefix: str, actor_id: int) -> None:
    st.markdown("#### Status")
    blocked_now = models.is_actually_blocked(job)

    if blocked_now:
        st.warning(f"Blocked by **{job['blocked_by_job_code']}** — resolve that job first.")
        return

    status = job["status"]

    if status == STATUS_NEW:
        if st.button("Start work", key=f"{key_prefix}_start"):
            _apply_status(job["id"], STATUS_IN_PROGRESS, actor_id=actor_id)

    elif status == STATUS_IN_PROGRESS:
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

    elif status == STATUS_BLOCKED and not job["blocked_by"]:
        if st.button("Resume — in progress", key=f"{key_prefix}_resume"):
            _apply_status(job["id"], STATUS_IN_PROGRESS, actor_id=actor_id)


def _apply_status(job_pk: int, new_status: str, reason: str | None = None, actor_id: int | None = None) -> None:
    try:
        models.set_status(job_pk, new_status, reason, actor_id=actor_id)
    except models.JobRuleError as e:
        st.error(str(e))
    else:
        st.toast("Status updated.", icon="✅")
        st.rerun()


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
    """Admin-only way to remove a genuine duplicate without a hard delete —
    dismisses it with a reason, same as any other dismissal, so the record
    and its audit trail stay intact."""
    if user["role"] != ROLE_ADMIN:
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
        if user["role"] == ROLE_ADMIN and job["status"] == STATUS_DONE:
            if st.button("Create invoice", key=f"jd_createinv_{job['id']}", type="primary"):
                st.session_state["invoice_seed_job"] = job["id"]
                st.session_state["invoice_seed_client"] = None
                st.session_state["invoice_revise_id"] = None
                ui.go_to_create_invoice()

    if user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL) and job["status"] == STATUS_DONE and job["invoice_id"]:
        if job["invoice_status"] not in ("approved", "paid"):
            st.info(f"Waiting on approval for invoice **{job['invoice_code']}** before this job can close.")
        else:
            if st.button("Mark closed", key=f"close_{job['id']}", type="primary"):
                try:
                    models.close_job(job["id"])
                except models.JobRuleError as e:
                    st.error(str(e))
                else:
                    st.toast("Job closed.", icon="✅")
                    st.rerun()


def _expenses(job: dict, user: dict) -> None:
    st.markdown("#### Expenses")
    expenses = models.list_job_expenses(job["id"])
    if expenses:
        total = sum(e["amount"] for e in expenses)
        for e in expenses:
            st.write(
                f"{e['expense_date'].isoformat()} — {e['description']} — ₦{e['amount']:,.2f}"
                f"  \n_added by {e['created_by_name'] or '—'}_"
            )
        st.caption(f"Total logged: ₦{total:,.2f}")
    else:
        st.caption("No expenses logged yet.")

    if user["role"] == ROLE_ADMIN:
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


def _comments(job: dict, user: dict) -> None:
    st.markdown("#### Comments")
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
        st.markdown(f"**{c['author_name']}** · {c['created_at'].strftime('%d %b %Y, %H:%M')}")
        st.write(c["body"])
        st.divider()
