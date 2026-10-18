"""Notifications — the full feed behind the bell's "See all" button. Every
role lands here on their own alerts, not just the last 15 the popover shows.
Read-only besides "Mark all read": a Linked item cell is a genuine browser
navigation (st.column_config.LinkColumn — a dataframe cell can't be a real
Streamlit button, same constraint as the Follow-ups and Performance pages),
so unlike the popover's own buttons it can't also mark that row read on the
way past — only "Mark all read" changes read state here."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import models, tz, ui

_TYPE_DISPLAY = {
    "assigned": "📋 Assigned",
    "reassigned": "📋 Reassigned",
    "status_done": "✅ Marked done",
    "status_blocked": "🟠 Marked blocked",
    "unblocked": "🔓 Unblocked",
    "blocking": "⛔ Blocking",
    "comment": "💬 Comment",
    "ec_action_resolved": "📌 EC action resolved",
    "invoice_approved": "🧾 Invoice approved",
    "invoice_rejected": "🧾 Invoice rejected",
    "invoice_unapproved": "🧾 Invoice un-approved",
    "recurring_job_created": "🔁 Recurring job created",
    "tcc_gate_blocked": "🚧 TCC gate blocked",
    "tcc_gate_unblocked": "🚧 TCC gate cleared",
    "quota_gate_blocked": "🚧 Quota gate blocked",
    "quota_gate_unblocked": "🚧 Quota gate cleared",
    "desk_exam_triggered": "🔁 Desk exam triggered",
    "sla_overdue": "⏰ SLA overdue",
    "sla_due": "⏰ SLA due soon",
}

_COLS = ["id", "kind", "link_type", "link_id", "message", "read", "created_at", "job_code", "invoice_code"]

_TABLE_HEIGHT = 560


def render(user: dict) -> None:
    ui.back_button("← Back")
    ui.page_header("Notifications", "Every alert sent to you — not just the most recent ones in the bell.")

    rows = models.list_all_notifications(user["id"])
    df = pd.DataFrame(rows, columns=_COLS) if rows else pd.DataFrame(columns=_COLS)

    _summary_strip(df)
    st.write("")

    unread_only = st.checkbox("Unread only", value=False, key="notif_page_unread_only")
    shown = df[~df["read"]] if unread_only and not df.empty else df

    _table_header(df, shown, user)

    if shown.empty:
        st.caption("Nothing unread — you're all caught up." if unread_only else "No notifications yet.")
        return

    _render_table(shown)


def _summary_strip(df: pd.DataFrame) -> None:
    unread = df[~df["read"]] if not df.empty else df
    oldest_days = 0
    if not unread.empty:
        today = tz.today_lagos()
        oldest_days = int(unread["created_at"].map(lambda dt: (today - tz.to_lagos_date(dt)).days).max())
    c1, c2, c3 = st.columns(3)
    c1.metric("Total", len(df))
    c2.metric("Unread", len(unread))
    c3.metric("Oldest unread (days)", oldest_days)


def _table_header(full_df: pd.DataFrame, shown_df: pd.DataFrame, user: dict) -> None:
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"#### {len(shown_df)} notification(s)")
    with right:
        if not full_df.empty and int((~full_df["read"]).sum()) > 0:
            if st.button(
                "Mark all read", key="notif_page_markall", type="primary", use_container_width=True,
            ):
                models.mark_all_notifications_read(user["id"])
                st.rerun()


def _render_table(df: pd.DataFrame) -> None:
    s_token = st.query_params.get("s", "")
    p_val = st.query_params.get("p", "")

    show = pd.DataFrame(index=df.index)
    show["Status"] = df["read"].map(lambda r: "" if r else "🔵 Unread")
    show["Type"] = df["kind"].map(lambda k: _TYPE_DISPLAY.get(k, k.replace("_", " ").title()))
    show["Message"] = df["message"]
    show["Linked item"] = df.apply(lambda r: _link_href(r, s_token, p_val), axis=1)
    show["Received"] = df["created_at"].map(tz.fmt)
    show["Age"] = df["created_at"].map(_age_dot)

    height = min(_TABLE_HEIGHT, 38 + 35 * (len(show) + 1))

    st.dataframe(
        show,
        column_config={
            "Status": st.column_config.TextColumn("", width="small"),
            "Type": st.column_config.TextColumn("Type", width="medium"),
            "Message": st.column_config.TextColumn("Message", width="large"),
            "Linked item": st.column_config.LinkColumn("Linked item", display_text=r"#(.*)$", width="medium"),
            "Received": st.column_config.TextColumn("Received", width="medium"),
            "Age": st.column_config.TextColumn("", width="small"),
        },
        hide_index=True, use_container_width=True, height=height,
    )


def _link_href(row, s_token: str, p_val: str) -> str:
    """A job/invoice that's since been hard-deleted (e.g. via Hidden Jobs'
    force-delete) leaves job_code/invoice_code NULL — no link to build, so
    the cell renders blank rather than a broken href. n=1 carries the
    Notifications overlay through the reload this link causes, so "← Back"
    on the job/invoice it opens returns here instead of the user's normal
    home page."""
    code = row["job_code"] if row["link_type"] == "job" else row["invoice_code"]
    if pd.isna(code):
        return ""
    param = "j" if row["link_type"] == "job" else "i"
    p_part = f"&p={p_val}" if p_val else ""
    return f"?s={s_token}&n=1{p_part}&{param}={int(row['link_id'])}#{code}"


def _age_dot(created_at) -> str:
    days = (tz.today_lagos() - tz.to_lagos_date(created_at)).days
    if days > 7:
        return "🔴"
    if days > 2:
        return "🟠"
    return "🟢"
