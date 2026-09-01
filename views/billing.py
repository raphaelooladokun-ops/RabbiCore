"""Billing: admin submits invoices for approval; principal approves them.
Neither role can do the other's step — the accountability trail (submitted
by / approved by) is shown on every invoice."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import INVOICE_STATUS_LABELS, ROLE_ADMIN, ROLE_PRINCIPAL, humanize


def render(user: dict) -> None:
    ui.page_header("Billing", _subtitle(user["role"]))

    if user["role"] == ROLE_ADMIN:
        _create_invoice_section(user)
        st.divider()
    elif user["role"] == ROLE_PRINCIPAL:
        _approvals_section(user)
        st.divider()

    _invoices_section(user)


def _subtitle(role: str) -> str:
    if role == ROLE_ADMIN:
        return "Group jobs onto an invoice, then submit it for the principal's approval."
    return "Approve invoices submitted by admin — a job can't close until its invoice is approved."


def _create_invoice_section(user: dict) -> None:
    st.markdown("#### Create invoice")
    clients = models.list_clients(active_only=True)
    client_map = {c["name"]: c for c in clients}
    client_name = st.selectbox(
        "Client", options=list(client_map.keys()), index=None,
        placeholder="Select a client to see their done-but-unbilled jobs…", key="bill_client",
    )
    if not client_name:
        return

    client = client_map[client_name]
    jobs = models.list_jobs_awaiting_invoice(client_id=client["id"])
    if not jobs:
        st.caption(f"Nothing awaiting invoice for {client_name}.")
        return

    invoice_code = st.text_input(
        "Invoice code *", key="bill_code",
        placeholder="e.g. INV-2026-014 or your accounting reference",
    )

    st.write(f"**{len(jobs)} job(s)** done and not yet invoiced for **{client_name}**:")
    selected_ids = []
    for j in jobs:
        done_on = j["status_changed_at"].strftime("%d %b %Y") if j["status_changed_at"] else "—"
        checked = st.checkbox(f"{j['job_id']} — {j['title']} (done {done_on})", key=f"bill_chk_{j['id']}")
        if checked:
            selected_ids.append(j["id"])

    st.write("")
    if st.button("Submit for approval", type="primary", disabled=not selected_ids, key="bill_create"):
        if not invoice_code.strip():
            st.error("Enter an invoice code.")
            return
        try:
            invoice = models.create_invoice(client["id"], invoice_code.strip(), user["id"])
        except models.InvoiceRuleError as e:
            st.error(str(e))
            return
        for job_id in selected_ids:
            models.attach_job_to_invoice(job_id, invoice["id"])
        st.toast(f"{invoice['invoice_code']} submitted for approval.", icon="✅")
        st.rerun()


def _approvals_section(user: dict) -> None:
    st.markdown("#### Pending your approval")
    pending = models.list_invoices(status="pending_approval")
    if not pending:
        st.caption("Nothing waiting on you right now.")
        return

    for inv in pending:
        with st.container(border=True):
            st.markdown(f"**{inv['invoice_code']}** — {inv['client_name']}")
            st.caption(f"Submitted by {inv['created_by_name'] or '—'} on {inv['created_at'].strftime('%d %b %Y')}")
            ui.jobs_row_table(models.list_jobs_for_invoice(inv["id"]), key_prefix=f"appr_{inv['id']}")
            if st.button("Approve", key=f"approve_{inv['id']}", type="primary"):
                models.approve_invoice(inv["id"], user["id"])
                st.toast(f"{inv['invoice_code']} approved.", icon="✅")
                st.rerun()


def _invoices_section(user: dict) -> None:
    st.markdown("#### All invoices")
    invoices = models.list_invoices()
    if not invoices:
        st.caption("No invoices yet.")
        return

    for inv in invoices:
        with st.container(border=True):
            status_label = humanize(inv["status"], INVOICE_STATUS_LABELS)
            st.markdown(f"**{inv['invoice_code']}** — {inv['client_name']} &nbsp;·&nbsp; {status_label}")
            trail = f"Submitted by {inv['created_by_name'] or '—'}"
            if inv["approved_by_name"]:
                trail += f" · Approved by {inv['approved_by_name']}"
            st.caption(trail)

            ui.jobs_row_table(models.list_jobs_for_invoice(inv["id"]), key_prefix=f"inv_{inv['id']}")

            if user["role"] == ROLE_ADMIN and inv["status"] == "approved":
                if st.button("Mark paid", key=f"markpaid_{inv['id']}"):
                    models.set_invoice_status(inv["id"], "paid")
                    st.toast("Marked paid.", icon="✅")
                    st.rerun()
