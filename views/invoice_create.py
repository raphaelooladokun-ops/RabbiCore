"""Build or revise a real invoice: client (fixed, pulled from the seed job
or a direct client pick), one or more done-but-unbilled jobs as line items
with an editable description and amount, an invoice code and date. Admin
only. Revising a rejected invoice reuses this same builder, pre-loaded."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import models
from core import ui


def render(user: dict) -> None:
    ui.back_button("← Cancel")

    revise_id = st.session_state.get("invoice_revise_id")
    if revise_id:
        _render_revise(user, revise_id)
        return

    seed_job_pk = st.session_state.get("invoice_seed_job")
    seed_client_id = st.session_state.get("invoice_seed_client")

    client = None
    seed_job = None
    if seed_job_pk:
        seed_job = models.get_job(seed_job_pk)
        if seed_job is None or seed_job["client_id"] is None:
            st.error("That job can't be invoiced — it has no client on file.")
            return
        client = models.get_client(seed_job["client_id"])
    elif seed_client_id:
        client = models.get_client(seed_client_id)

    if client is None:
        st.error("No client selected for this invoice. Start from a job's detail page or from Billing.")
        return

    ui.page_header("Create invoice", f"For {client['name']}")
    _builder(user, client, preselect_job_id=seed_job["id"] if seed_job else None)


def _render_revise(user: dict, invoice_id: int) -> None:
    invoice = models.get_invoice(invoice_id)
    if invoice is None:
        st.error("Invoice not found.")
        return
    client = models.get_client(invoice["client_id"])
    ui.page_header("Revise invoice", f"{invoice['invoice_code']} — {client['name']}")
    if invoice["rejection_reason"]:
        st.warning(f"**Rejected by {invoice['rejected_by_name'] or 'the principal'}:** {invoice['rejection_reason']}")
    _builder(user, client, invoice=invoice)


def _builder(user: dict, client: dict, preselect_job_id: int | None = None, invoice: dict | None = None) -> None:
    revising = invoice is not None
    existing_lines = models.list_invoice_lines(invoice["id"]) if revising else []
    existing_by_job = {line["job_id"]: line for line in existing_lines}

    jobs = models.list_jobs_available_for_invoice(
        client["id"], invoice_id=invoice["id"] if revising else None
    )
    if not jobs:
        st.caption(f"No done, unbilled jobs for {client['name']}.")
        return

    st.markdown("#### Jobs on this invoice")
    st.caption("Check the jobs to bill — each becomes one line item below.")

    selected = {}
    for j in jobs:
        default_checked = (j["id"] in existing_by_job) or (j["id"] == preselect_job_id)
        checked = st.checkbox(
            f"{ui.short_job_id(j['job_id'])} — {j['title']}", value=default_checked, key=f"invsel_{j['id']}"
        )
        if checked:
            selected[j["id"]] = j

    line_inputs = []
    if selected:
        st.write("")
        st.markdown("#### Line items")
        running_total = 0.0
        for job_id, j in selected.items():
            existing = existing_by_job.get(job_id)
            default_desc = existing["description"] if existing else j["title"]
            default_amount = float(existing["amount"]) if existing else 0.0
            c1, c2 = st.columns([3, 1])
            desc = c1.text_input(
                f"Description — {ui.short_job_id(j['job_id'])}", value=default_desc, key=f"invdesc_{job_id}"
            )
            amount = c2.number_input(
                "Amount (₦)", min_value=0.0, step=500.0, value=default_amount,
                key=f"invamt_{job_id}", format="%.2f",
            )
            running_total += amount
            line_inputs.append({"job_id": job_id, "description": desc, "amount": amount})
        st.markdown(f"**Total: ₦{running_total:,.2f}**")
    else:
        st.caption("Select at least one job.")

    st.write("")
    if revising:
        st.caption(f"Invoice code **{invoice['invoice_code']}** stays the same on a revision.")
    else:
        st.caption("The invoice code is generated automatically once submitted.")
    invoice_date = st.date_input(
        "Invoice date", value=invoice["invoice_date"] if revising else date.today(), key="inv_date",
    )

    st.write("")
    label = "Resubmit for approval" if revising else "Submit for approval"
    if st.button(label, type="primary", key="inv_submit"):
        if not line_inputs:
            st.error("Select at least one job.")
            return
        if any(line["amount"] <= 0 for line in line_inputs):
            st.error("Every line item needs an amount greater than zero.")
            return
        if revising:
            result = models.revise_invoice(invoice["id"], invoice_date, line_inputs)
        else:
            result = models.create_invoice(client["id"], invoice_date, user["id"], line_inputs)
        st.toast(f"{result['invoice_code']} submitted for approval.", icon="✅")
        ui.go_to_invoice(result["id"])
