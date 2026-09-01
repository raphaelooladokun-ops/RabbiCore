"""Billing: the invoice ledger. Every invoice is clickable — that opens the
actual document, where it gets approved, rejected, or revised. Admin can
also start a new invoice from here (the other entry point is a job's own
detail page)."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import INVOICE_STATUS_LABELS, ROLE_ADMIN, humanize


def render(user: dict) -> None:
    ui.page_header("Billing", _subtitle(user["role"]))

    if user["role"] == ROLE_ADMIN:
        _start_new_invoice()
        st.divider()

    _invoices_list()


def _subtitle(role: str) -> str:
    if role == ROLE_ADMIN:
        return "Start a new invoice, or open one below."
    return "Every invoice — open one pending your approval to review it as a document."


def _start_new_invoice() -> None:
    st.markdown("#### Start a new invoice")
    clients = models.list_clients(active_only=True)
    client_map = {c["name"]: c for c in clients}
    client_name = st.selectbox(
        "Client", options=list(client_map.keys()), index=None,
        placeholder="Select a client…", key="bill_new_client",
    )
    if client_name and st.button("Continue", key="bill_new_continue"):
        st.session_state["invoice_seed_client"] = client_map[client_name]["id"]
        st.session_state["invoice_seed_job"] = None
        st.session_state["invoice_revise_id"] = None
        ui.go_to_create_invoice()


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
