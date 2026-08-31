"""Billing: group done-but-unbilled jobs onto an invoice, then close jobs
once they're attached. One invoice can cover several jobs."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import INVOICE_STATUS_LABELS, STATUS_DONE, STATUS_LABELS_SHORT, humanize

NEXT_INVOICE_STATUS = {"draft": "issued", "issued": "paid"}


def render(user: dict) -> None:
    ui.page_header("Billing", "Group jobs onto an invoice, then close them once billed.")
    _create_invoice_section()
    st.divider()
    _invoices_section()


def _create_invoice_section() -> None:
    st.markdown("#### Ready to invoice")
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

    st.write(f"**{len(jobs)} job(s)** done and not yet invoiced for **{client_name}**:")
    selected_ids = []
    for j in jobs:
        done_on = j["status_changed_at"].strftime("%d %b %Y") if j["status_changed_at"] else "—"
        checked = st.checkbox(f"{j['job_id']} — {j['title']} (done {done_on})", key=f"bill_chk_{j['id']}")
        if checked:
            selected_ids.append(j["id"])

    st.write("")
    if st.button("Group into invoice", type="primary", disabled=not selected_ids, key="bill_create"):
        invoice = models.create_invoice(client["id"])
        for job_id in selected_ids:
            models.attach_job_to_invoice(job_id, invoice["id"])
        st.toast(f"Created {invoice['invoice_code']} with {len(selected_ids)} job(s) attached.", icon="✅")
        st.rerun()


def _invoices_section() -> None:
    st.markdown("#### Invoices")
    invoices = models.list_invoices()
    if not invoices:
        st.caption("No invoices yet.")
        return

    for inv in invoices:
        with st.container(border=True):
            status_label = humanize(inv["status"], INVOICE_STATUS_LABELS)
            st.markdown(f"**{inv['invoice_code']}** — {inv['client_name']} &nbsp;·&nbsp; {status_label}")

            jobs = models.list_jobs_for_invoice(inv["id"])
            for j in jobs:
                c1, c2, c3 = st.columns([4, 2, 1])
                c1.write(f"{j['job_id']} — {j['title']}")
                c2.write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
                if j["status"] == STATUS_DONE:
                    if c3.button("Close", key=f"bill_close_{j['id']}"):
                        models.close_job(j["id"])
                        st.rerun()

            next_status = NEXT_INVOICE_STATUS.get(inv["status"])
            if next_status:
                label = f"Mark {humanize(next_status, INVOICE_STATUS_LABELS)}"
                if st.button(label, key=f"bill_invstatus_{inv['id']}"):
                    models.set_invoice_status(inv["id"], next_status)
                    st.rerun()
