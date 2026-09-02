"""Billing: the invoice ledger. Every invoice is clickable — that opens the
actual document, where it gets approved, rejected, or revised. Admin also
sees a ready-to-invoice register here: every done, unbilled job across every
client, each with its own Create-invoice button (the other entry point is
that same button on a job's own detail page)."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import INVOICE_STATUS_LABELS, ROLE_ADMIN, STATUS_DONE, STATUS_LABELS_SHORT, humanize

_ROW_WIDTHS = [1.1, 1.6, 2.2, 1.2, 1.1, 1.3]


def render(user: dict) -> None:
    ui.page_header("Billing", _subtitle(user["role"]))

    if user["role"] == ROLE_ADMIN:
        _ready_to_invoice()
        st.divider()

    _invoices_list()


def _subtitle(role: str) -> str:
    if role == ROLE_ADMIN:
        return "Every unbilled job — create an invoice once it's done."
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
        cols[1].write(j["client_name"] or "—")
        cols[2].write(j["title"])
        cols[3].write(j["owner_name"] or "—")
        cols[4].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        if j["status"] == STATUS_DONE:
            if cols[5].button("Create invoice", key=f"readyinv_{j['id']}"):
                st.session_state["invoice_seed_job"] = j["id"]
                st.session_state["invoice_seed_client"] = None
                st.session_state["invoice_revise_id"] = None
                ui.go_to_create_invoice()
        else:
            cols[5].caption("Not done yet")


def _invoices_list() -> None:
    st.markdown("#### All invoices")
    invoices = models.list_invoices()
    if not invoices:
        st.caption("No invoices yet.")
        return

    for inv in invoices:
        with st.container(border=True):
            status_label = humanize(inv["status"], INVOICE_STATUS_LABELS)
            total = models.invoice_total(inv["id"])
            if st.button(
                f"{inv['invoice_code']} — {inv['client_name']} · {status_label} · ₦{total:,.2f}",
                key=f"openinv_{inv['id']}", type="tertiary",
            ):
                ui.go_to_invoice(inv["id"])

            trail = f"Submitted by {inv['created_by_name'] or '—'}"
            if inv["approved_by_name"]:
                trail += f" · Approved by {inv['approved_by_name']}"
            elif inv["status"] == "rejected":
                trail += f" · Rejected by {inv['rejected_by_name'] or '—'}"
            st.caption(trail)
