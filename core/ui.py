"""Shared UI building blocks: theme injection, status/risk badges, page
headers — so every screen in the app looks and behaves consistently."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core import models
from core.constants import (
    CATEGORY_LABELS,
    RISK_COLORS,
    RISK_EMOJI,
    RISK_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS_SHORT,
    humanize,
)

NAV_JOB_KEY = "nav_job_pk"
NAV_INVOICE_KEY = "nav_invoice_pk"
NAV_CREATE_INVOICE_KEY = "nav_create_invoice"

CSS_PATH = Path(__file__).parent.parent / "assets" / "style.css"


def inject_theme() -> None:
    css = CSS_PATH.read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<div class="rc-page-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="rc-page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def risk_badge_html(risk: str) -> str:
    color = RISK_COLORS[risk]
    label = RISK_LABELS[risk]
    return (
        f'<span class="rc-badge rc-badge-{risk}">'
        f'<span class="rc-dot" style="background:{color}"></span>{label}</span>'
    )


def status_badge_html(status: str) -> str:
    """Neutral badge for the literal workflow status (not the risk colour)."""
    label = STATUS_LABELS_SHORT.get(status, humanize(status))
    return f'<span class="rc-badge rc-badge-grey">{label}</span>'


def category_label(value: str) -> str:
    return humanize(value, CATEGORY_LABELS)


def source_label(value: str) -> str:
    return humanize(value, SOURCE_LABELS)


def go_to_job(job_pk: int) -> None:
    """Navigate to the job detail page for job_pk — the one place every
    clickable job row, everywhere in the app, converges on."""
    clear_all_nav()
    st.session_state[NAV_JOB_KEY] = job_pk
    st.rerun()


def go_to_invoice(invoice_pk: int) -> None:
    """Navigate to the invoice document page — the one place an invoice,
    everywhere it's referenced, converges on."""
    clear_all_nav()
    st.session_state[NAV_INVOICE_KEY] = invoice_pk
    st.rerun()


def go_to_create_invoice() -> None:
    clear_all_nav()
    st.session_state[NAV_CREATE_INVOICE_KEY] = True
    st.rerun()


def clear_job_nav() -> None:
    st.session_state[NAV_JOB_KEY] = None


def clear_invoice_nav() -> None:
    st.session_state[NAV_INVOICE_KEY] = None
    st.session_state[NAV_CREATE_INVOICE_KEY] = False


def clear_all_nav() -> None:
    st.session_state[NAV_JOB_KEY] = None
    st.session_state[NAV_INVOICE_KEY] = None
    st.session_state[NAV_CREATE_INVOICE_KEY] = False


def back_button(label: str = "← Back") -> None:
    if st.button(label, key=f"back_{label}"):
        clear_all_nav()
        st.rerun()


_TABLE_WIDTHS = [0.4, 1.2, 1.6, 2.4, 1.3, 1.2, 1.0, 1.1]
_TABLE_HEADERS = ["", "Job ID", "Client", "What", "Owner", "Status", "SLA date", "Invoice"]


def jobs_row_table(jobs: list, key_prefix: str) -> None:
    """Render jobs as a compact, clickable table — clicking a Job ID opens
    its job detail page. No dataframe row-selection involved (and so no
    stale-selection state to manage): each Job ID is a real button, so a
    click is a one-shot event on the rerun it happens in."""
    if not jobs:
        st.caption("Nothing here.")
        return

    header_cols = st.columns(_TABLE_WIDTHS)
    for col, label in zip(header_cols, _TABLE_HEADERS):
        col.markdown(f"**{label}**")

    for j in jobs:
        cols = st.columns(_TABLE_WIDTHS)
        cols[0].write(RISK_EMOJI[models.compute_risk(j)])
        if cols[1].button(j["job_id"], key=f"{key_prefix}_row_{j['id']}", type="tertiary"):
            go_to_job(j["id"])
        cols[2].write(j.get("client_name") or "—")
        cols[3].write(j["title"])
        cols[4].write(j.get("owner_name") or "—")
        cols[5].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        cols[6].write(j["sla_date"].isoformat() if j.get("sla_date") else "—")
        if j.get("invoice_code"):
            if cols[7].button(j["invoice_code"], key=f"{key_prefix}_inv_{j['id']}", type="tertiary"):
                go_to_invoice(j["invoice_id"])
        else:
            cols[7].write("—")


def sidebar_wordmark() -> None:
    st.sidebar.markdown(
        '<div class="rc-wordmark">RABBI CORE</div>'
        '<div class="rc-tagline">Front Office</div>',
        unsafe_allow_html=True,
    )
