"""Shared UI building blocks: theme injection, status/risk badges, page
headers — so every screen in the app looks and behaves consistently."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core import models
from core.constants import (
    CATEGORY_LABELS,
    RISK_AMBER,
    RISK_COLORS,
    RISK_EMOJI,
    RISK_GREEN,
    RISK_GREY,
    RISK_LABELS,
    RISK_NAVY,
    RISK_RED,
    SOURCE_LABELS,
    STATUS_LABELS_SHORT,
    humanize,
    titlecase_name,
)

NAV_JOB_KEY = "nav_job_pk"
NAV_INVOICE_KEY = "nav_invoice_pk"
NAV_CREATE_INVOICE_KEY = "nav_create_invoice"
NAV_CLIENT_KEY = "nav_client_pk"

CSS_PATH = Path(__file__).parent.parent / "assets" / "style.css"


def inject_theme() -> None:
    css = CSS_PATH.read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<div class="rc-page-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="rc-page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def short_job_id(job_id: str | None) -> str:
    """Last 4 characters of a job code for compact list/table display —
    e.g. 'JOB-2026-0008' -> '#0008'. Detail pages always show the full code;
    this is only for rows where many jobs are shown at once."""
    if not job_id:
        return "—"
    return f"#{job_id[-4:]}"


def staff_label(s: dict) -> str:
    """A staff member's display label, unique even when two people share a
    name — every staff picker in the app (Capture's Owner field, module
    specialist assignment, bulk-assign, reassign, the Users list) keys off
    this rather than the bare name, so a duplicate name can never silently
    collide with or shadow another person's entry in a dropdown. Shows
    their login username (staff.email) rather than the numeric database
    id — the id means nothing to anyone matching people up day to day.
    Plain text: selectbox options don't render markdown, so this is what
    every dropdown built from this label should use. For text rendered
    through st.write/st.markdown/st.warning, use staff_label_md instead —
    a bare email there would otherwise auto-link as a mailto: address.
    The name is Title Cased for display (see titlecase_name) — the email
    never is, since it's a login identifier, not a name."""
    return f"{titlecase_name(s['name'])} ({s['email']})"


def staff_label_md(s: dict) -> str:
    """Markdown-safe variant of staff_label for st.write/st.markdown/
    st.warning contexts — backtick-wraps the email so Streamlit's markdown
    renderer can't turn it into a clickable mailto: link."""
    return f"{titlecase_name(s['name'])} (`{s['email']}`)"


def risk_legend() -> None:
    """A small colour key so red/amber/green/grey mean the same thing
    everywhere they're used — placed wherever those badges/dots appear."""
    items = "".join(
        f'<span class="rc-legend-item">'
        f'<span class="rc-dot" style="background:{RISK_COLORS[r]}"></span>{RISK_LABELS[r]}</span>'
        for r in (RISK_RED, RISK_AMBER, RISK_GREEN, RISK_NAVY, RISK_GREY)
    )
    st.markdown(f'<div class="rc-legend">{items}</div>', unsafe_allow_html=True)


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


def go_to_client(client_pk: int) -> None:
    """Navigate to the client/company detail page — the one place a client
    name, everywhere it's referenced, converges on."""
    clear_all_nav()
    st.session_state[NAV_CLIENT_KEY] = client_pk
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
    st.session_state[NAV_CLIENT_KEY] = None
    # Bump so any open popover (e.g. the notification bell) gets a fresh
    # widget identity and defaults closed — Streamlit doesn't auto-close a
    # popover just because a click inside it triggered a rerun that swapped
    # out the whole page, so without this it stays open on top of wherever
    # navigation just landed.
    st.session_state["_nav_epoch"] = st.session_state.get("_nav_epoch", 0) + 1


_NAV_QUERY_KEYS = ("p", "j", "i", "ci", "c")


def sync_nav_query_params() -> None:
    """Mirror the current page/job/invoice navigation into the URL so a hard
    reload lands back on the same page/section instead of bouncing to Home
    — the login token already survives a reload (see core/auth.py); this
    does the same for *where* the user was, not just *who* they were."""
    current = {
        "p": st.session_state.get("_current_page"),
        "j": st.session_state.get(NAV_JOB_KEY),
        "i": st.session_state.get(NAV_INVOICE_KEY),
        "ci": "1" if st.session_state.get(NAV_CREATE_INVOICE_KEY) else None,
        "c": st.session_state.get(NAV_CLIENT_KEY),
    }
    for key in _NAV_QUERY_KEYS:
        value = current[key]
        if value:
            st.query_params[key] = str(value)
        else:
            st.query_params.pop(key, None)


def restore_nav_from_query_params(page_names: list) -> None:
    """Called once per session, right after login resolves: if there's no
    navigation state yet in session_state (a fresh session_state, whether
    from a first visit or a hard reload re-authenticated via the login
    token), pick it back up from the URL instead of defaulting to the first
    page. After this first restore, session_state — kept in sync with the
    URL by sync_nav_query_params() — is the source of truth."""
    if st.session_state.get("_nav_restored"):
        return
    st.session_state["_nav_restored"] = True

    page = st.query_params.get("p")
    if page in page_names:
        st.session_state["_current_page"] = page

    for key, param in ((NAV_JOB_KEY, "j"), (NAV_INVOICE_KEY, "i"), (NAV_CLIENT_KEY, "c")):
        value = st.query_params.get(param)
        if value and value.isdigit():
            st.session_state[key] = int(value)

    if st.query_params.get("ci") == "1":
        st.session_state[NAV_CREATE_INVOICE_KEY] = True


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
        if cols[1].button(short_job_id(j["job_id"]), key=f"{key_prefix}_row_{j['id']}", type="tertiary"):
            go_to_job(j["id"])
        if j.get("client_id") and j.get("client_name"):
            if cols[2].button(titlecase_name(j["client_name"]), key=f"{key_prefix}_client_{j['id']}", type="tertiary"):
                go_to_client(j["client_id"])
        else:
            cols[2].write(titlecase_name(j.get("client_name")) or "—")
        cols[3].write(j["title"])
        cols[4].write(titlecase_name(j.get("owner_name")) or "—")
        cols[5].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        cols[6].write(j["sla_date"].isoformat() if j.get("sla_date") else "—")
        if j.get("invoice_code"):
            if cols[7].button(j["invoice_code"], key=f"{key_prefix}_inv_{j['id']}", type="tertiary"):
                go_to_invoice(j["invoice_id"])
        else:
            cols[7].write("—")


def notification_bell(user: dict) -> None:
    """A bell in the sidebar showing unread alerts for this user — assigned
    jobs, status changes, invoice events, SLA warnings, dependency alerts.
    Each is clickable and jumps straight to the job or invoice it's about.
    Turns red (via the st-key-notifwrap_unread CSS hook) whenever there's
    something unread, for every role — not just a number to notice, a
    colour to notice."""
    unread = models.count_unread_notifications(user["id"])
    label = f"🔔 {unread} new" if unread else "🔔 Notifications"
    epoch = st.session_state.get("_nav_epoch", 0)
    wrapper_key = "notifwrap_unread" if unread else "notifwrap_read"
    wrapper = st.sidebar.container(key=wrapper_key)
    with wrapper.popover(label, use_container_width=True, key=f"notif_pop_{epoch}"):
        st.markdown("**Notifications**")
        notes = models.list_notifications(user["id"], limit=15)
        if not notes:
            st.caption("Nothing yet.")
        for n in notes:
            marker = "🔵 " if not n["read"] else ""
            if st.button(
                f"{marker}{n['message']}", key=f"notif_{n['id']}", type="tertiary", use_container_width=True
            ):
                models.mark_notification_read(n["id"])
                if n["link_type"] == "job":
                    go_to_job(n["link_id"])
                else:
                    go_to_invoice(n["link_id"])
        if notes:
            st.divider()
            if st.button("Mark all read", key="notif_markall", use_container_width=True):
                models.mark_all_notifications_read(user["id"])
                st.rerun()


def sidebar_wordmark() -> None:
    st.sidebar.markdown(
        '<div class="rc-wordmark">RABBI CORE</div>'
        '<div class="rc-tagline">Front Office</div>',
        unsafe_allow_html=True,
    )


def super_admin_badge() -> None:
    """A deliberately loud, unmissable marker that this session has full
    access across every role's environment — never to be confused with an
    ordinary role caption."""
    st.sidebar.markdown(
        '<div class="rc-superadmin-badge">⚡ Full access — all environments</div>',
        unsafe_allow_html=True,
    )
