"""Billing: the invoice ledger. Every invoice is clickable — that opens the
actual document, where it gets approved, rejected, or revised. Admin also
sees a ready-to-invoice register here: every done, unbilled job across every
client, each with its own Create-invoice button (the other entry point is
that same button on a job's own detail page)."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import models
from core import ui
from core.constants import (
    BULK_INVOICE_APPROVE_ROLES,
    BULK_INVOICE_PAID_SENT_ROLES,
    FORCE_DELETE_PIN,
    INVOICE_STATUS_LABELS,
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_SUPER_ADMIN,
    STATUS_LABELS_SHORT,
    humanize,
    titlecase_name,
)

_ROW_WIDTHS = [1.1, 1.6, 2.2, 1.2, 1.1, 1.3]

# Bulk selection/actions are offered only to roles that can run at least one
# bulk action — the union of BULK_INVOICE_APPROVE_ROLES and
# BULK_INVOICE_PAID_SENT_ROLES. Everyone else (principal/manager) keeps the
# plain, unchanged invoice list.
_BULK_CAPABLE_ROLES = tuple(set(BULK_INVOICE_APPROVE_ROLES) | set(BULK_INVOICE_PAID_SENT_ROLES))

_ACTION_VERB = {"approve": "Approve", "paid": "Mark as paid", "sent": "Mark as sent to client"}
_ACTION_VERB_PAST = {"approve": "approved", "paid": "marked paid", "sent": "marked sent to client"}


def render(user: dict) -> None:
    ui.page_header("Billing", _subtitle(user["role"]))

    if user["role"] in (ROLE_ADMIN, ROLE_MANAGER, ROLE_SUPER_ADMIN):
        _ready_to_invoice()
        st.divider()

    _invoices_list(user)

    if user["role"] == ROLE_SUPER_ADMIN:
        st.divider()
        _delete_all_invoices_control()


def _subtitle(role: str) -> str:
    if role in (ROLE_ADMIN, ROLE_MANAGER, ROLE_SUPER_ADMIN):
        return "Every unbilled job — invoice it as soon as it's logged."
    return "Every invoice — open one pending your approval to review it as a document."


def _ready_to_invoice() -> None:
    st.markdown("#### Ready to invoice")
    jobs = models.list_unbilled_jobs()
    if not jobs:
        st.caption("Nothing unbilled right now.")
        return

    header = st.columns(_ROW_WIDTHS)
    for col, label in zip(header, ["Job ID", "Client", "What", "Owner", "Status", ""]):
        col.markdown(f"**{label}**")

    for j in jobs:
        cols = st.columns(_ROW_WIDTHS)
        if cols[0].button(ui.short_job_id(j["job_id"]), key=f"readyjob_{j['id']}", type="tertiary"):
            ui.go_to_job(j["id"])
        cols[1].write(titlecase_name(j["client_name"]) or "—")
        cols[2].write(j["title"])
        cols[3].write(titlecase_name(j["owner_name"]) or "—")
        cols[4].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        if cols[5].button("Create invoice", key=f"readyinv_{j['id']}"):
            st.session_state["invoice_seed_job"] = j["id"]
            st.session_state["invoice_seed_client"] = None
            st.session_state["invoice_revise_id"] = None
            ui.go_to_create_invoice()


def _invoices_list(user: dict) -> None:
    st.markdown("#### All invoices")
    if user["role"] in _BULK_CAPABLE_ROLES:
        _bulk_invoices_list(user)
        return

    invoices = models.list_invoices()
    if not invoices:
        st.caption("No invoices yet.")
        return

    for inv in invoices:
        with st.container(border=True):
            status_label = humanize(inv["status"], INVOICE_STATUS_LABELS)
            total = models.invoice_total(inv["id"])
            if st.button(
                f"{inv['invoice_code']} — {titlecase_name(inv['client_name'])} · {status_label} · ₦{total:,.2f}",
                key=f"openinv_{inv['id']}", type="tertiary",
            ):
                ui.go_to_invoice(inv["id"])

            trail = f"Submitted by {titlecase_name(inv['created_by_name']) or '—'}"
            if inv["approved_by_name"]:
                trail += f" · Approved by {titlecase_name(inv['approved_by_name'])}"
            elif inv["status"] == "rejected":
                trail += f" · Rejected by {titlecase_name(inv['rejected_by_name']) or '—'}"
            st.caption(trail)


# ---------------------------------------------------------------------------
# Bulk-capable invoice list — admin/super_admin only (see
# _BULK_CAPABLE_ROLES). A status filter (so "select all" can mean "all
# filtered"), a per-invoice checkbox, and whichever bulk actions this
# user's role is allowed to run (hidden otherwise). Every bulk action is
# the SAME per-invoice rule as its single-invoice counterpart, just applied
# to a batch — see bulk_approve_invoices/bulk_mark_invoices_sent/
# bulk_mark_invoices_paid in core/models.py.
# ---------------------------------------------------------------------------
def _bulk_invoices_list(user: dict) -> None:
    _render_result_banner()

    status_options = ["All statuses"] + list(INVOICE_STATUS_LABELS.values())
    label_to_status = {v: k for k, v in INVOICE_STATUS_LABELS.items()}
    status_choice = st.selectbox("Status", options=status_options, key="binv_f_status")
    status_filter = label_to_status.get(status_choice)

    invoices = models.list_invoices(status=status_filter)
    if not invoices:
        st.caption("No invoices yet." if not status_filter else "No invoices match this filter.")
        return

    totals = models.invoice_totals([inv["id"] for inv in invoices])

    select_key = "binv_selectall"
    prev_key = "binv_selectall_prev"
    select_all = st.checkbox(f"Select all ({len(invoices)} filtered)", key=select_key)
    if st.session_state.get(prev_key) != select_all:
        for inv in invoices:
            st.session_state[f"binv_sel_{inv['id']}"] = select_all
        st.session_state[prev_key] = select_all
        st.rerun()

    for inv in invoices:
        with st.container(border=True):
            c0, c1 = st.columns([0.05, 0.95])
            c0.checkbox("", key=f"binv_sel_{inv['id']}", label_visibility="collapsed")
            with c1:
                status_label = humanize(inv["status"], INVOICE_STATUS_LABELS)
                total = totals.get(inv["id"], 0.0)
                if st.button(
                    f"{inv['invoice_code']} — {titlecase_name(inv['client_name'])} · {status_label} · ₦{total:,.2f}",
                    key=f"binv_open_{inv['id']}", type="tertiary",
                ):
                    ui.go_to_invoice(inv["id"])

                trail = f"Submitted by {titlecase_name(inv['created_by_name']) or '—'}"
                if inv["approved_by_name"]:
                    trail += f" · Approved by {titlecase_name(inv['approved_by_name'])}"
                elif inv["status"] == "rejected":
                    trail += f" · Rejected by {titlecase_name(inv['rejected_by_name']) or '—'}"
                st.caption(trail)

    selected_ids = [inv["id"] for inv in invoices if st.session_state.get(f"binv_sel_{inv['id']}")]
    if selected_ids:
        by_id = {inv["id"]: inv for inv in invoices}
        _bulk_actions_bar([by_id[i] for i in selected_ids], totals, user)


def _bulk_actions_bar(selected: list, totals: dict, user: dict) -> None:
    selected_total = sum(totals.get(inv["id"], 0.0) for inv in selected)

    st.write("")
    st.markdown(f"**{len(selected)} invoice(s) selected — ₦{selected_total:,.2f}**")

    available = []
    if user["role"] in BULK_INVOICE_APPROVE_ROLES:
        available.append("approve")
    if user["role"] in BULK_INVOICE_PAID_SENT_ROLES:
        available += ["paid", "sent"]
    if not available:
        return

    labels = {"approve": "Bulk Approve", "paid": "Bulk Mark as Paid", "sent": "Bulk Mark as Sent to Client"}
    cols = st.columns(len(available))
    for col, action in zip(cols, available):
        if col.button(f"{labels[action]} ({len(selected)})", key=f"binv_pick_{action}", use_container_width=True):
            st.session_state["binv_pending_action"] = action

    pending = st.session_state.get("binv_pending_action")
    if pending not in available:
        return
    _confirm_panel(pending, selected, selected_total, user)


def _confirm_panel(action: str, selected: list, selected_total: float, user: dict) -> None:
    with st.container(border=True):
        verb = _ACTION_VERB[action]
        st.markdown(f"**{verb} {len(selected)} invoice(s) totalling ₦{selected_total:,.2f}?**")

        pin = None
        confirm_checked = True
        payment_reference = None
        payment_date_val = None

        if action == "approve":
            confirm_checked = st.checkbox(
                f"Yes, approve these {len(selected)} invoice(s) exactly as they currently stand — "
                "I understand this gives final approval.",
                key="binv_approve_confirm",
            )
            pin = st.text_input("4-digit PIN", type="password", max_chars=4, key="binv_approve_pin")
        elif action == "paid":
            st.caption("One payment reference and date applied to every invoice in this batch.")
            c1, c2 = st.columns(2)
            with c1:
                payment_reference = st.text_input("Payment reference *", key="binv_paid_ref")
            with c2:
                payment_date_val = st.date_input("Payment date *", value=date.today(), key="binv_paid_date")

        b1, b2, _ = st.columns([1.4, 0.8, 2])
        confirm_clicked = b1.button(
            f"Confirm — {verb.lower()} {len(selected)} invoice(s)", type="primary",
            key="binv_confirm_btn", disabled=not confirm_checked,
        )
        cancel_clicked = b2.button("Cancel", key="binv_cancel_btn")

        if cancel_clicked:
            st.session_state["binv_pending_action"] = None
            st.rerun()

        if not confirm_clicked:
            return

        if action == "approve" and pin != FORCE_DELETE_PIN:
            st.error("Incorrect PIN.")
            return

        ids = [inv["id"] for inv in selected]
        if action == "approve":
            applied, skipped = models.bulk_approve_invoices(ids, user["id"])
        elif action == "paid":
            try:
                applied, skipped = models.bulk_mark_invoices_paid(ids, payment_reference, payment_date_val)
            except models.InvoicePaymentError as e:
                st.error(str(e))
                return
        else:
            applied, skipped = models.bulk_mark_invoices_sent(ids, user["id"])

        st.session_state["binv_result"] = {
            "action": action,
            "applied": [inv["invoice_code"] for inv in applied],
            "skipped": [(inv["invoice_code"], _skip_reason(action, inv)) for inv in skipped],
        }
        _clear_bulk_selection(selected)
        st.session_state["binv_pending_action"] = None
        st.rerun()


def _skip_reason(action: str, inv: dict) -> str:
    status_label = humanize(inv["status"], INVOICE_STATUS_LABELS).lower()
    if action == "approve":
        return f"not awaiting approval (currently {status_label})"
    if action == "paid":
        return "already paid" if inv["status"] == "paid" else f"not approved yet (currently {status_label})"
    if action == "sent":
        if inv.get("sent_at"):
            return "already sent to client"
        return f"not approved or paid yet (currently {status_label})"
    return status_label


def _clear_bulk_selection(selected: list) -> None:
    for inv in selected:
        st.session_state.pop(f"binv_sel_{inv['id']}", None)
    st.session_state.pop("binv_selectall", None)
    st.session_state.pop("binv_selectall_prev", None)


def _render_result_banner() -> None:
    result = st.session_state.get("binv_result")
    if not result:
        return
    with st.container(border=True):
        applied, skipped = result["applied"], result["skipped"]
        verb_past = _ACTION_VERB_PAST[result["action"]]
        if applied:
            st.success(f"{len(applied)} invoice(s) {verb_past}: {', '.join(applied)}")
        if skipped:
            st.warning(f"{len(skipped)} skipped:")
            for code, reason in skipped:
                st.caption(f"• {code} — {reason}")
        if not applied and not skipped:
            st.info("Nothing to do — none of the selected invoices were eligible.")
        if st.button("Dismiss", key="binv_result_dismiss"):
            st.session_state["binv_result"] = None
            st.rerun()


def _delete_all_invoices_control() -> None:
    with st.expander("🗑️ Delete ALL invoices (super admin) — clearing test data"):
        st.caption(
            "Permanently deletes every invoice in the system in one action — for wiping test data, "
            "not a per-invoice cleanup. There's no separate 'test' flag on an invoice, so this clears "
            "all of them. Every job currently on an invoice is detached (a Closed job reverts to Done, "
            "since Closed requires an invoice). This cannot be undone."
        )
        count = models.count_invoices()
        if count == 0:
            st.caption("No invoices to delete.")
            return

        st.warning(f"This will permanently delete **{count} invoice(s)**.")
        confirm = st.checkbox(
            f"Yes, permanently delete all {count} invoice(s) — I understand this cannot be undone.",
            key="delallinv_confirm",
        )
        pin = st.text_input("4-digit PIN", type="password", max_chars=4, key="delallinv_pin")
        if st.button("Delete ALL invoices", key="delallinv_btn", disabled=not confirm, type="primary"):
            if pin != FORCE_DELETE_PIN:
                st.error("Incorrect PIN.")
            else:
                deleted = models.delete_all_invoices()
                st.toast(f"Deleted {deleted} invoice(s).", icon="✅")
                st.rerun()
