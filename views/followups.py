"""Follow-ups — Super Admin only. A single view of what still needs
chasing across every job: one row per open job by default (current status
+ latest comment), not a dump of every comment ever. "Include completed"
brings done/closed jobs back in; "Full comment log" switches to one row
per comment for a complete history export. Read-only — nothing here edits
a job or a comment; every number comes from core.followups, which does the
aggregation in Neon and caches it.

Job ID links here are raw <a href> (st.column_config.LinkColumn — a
dataframe cell can't be a real Streamlit button), so clicking one is a
genuine browser navigation. The href always carries p=Follow-ups so a
click-through lands back on this page, not the first nav page, same fix
applied to the performance dashboard's own job links."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import followups, models, tz, ui
from core.constants import titlecase_name


def _safe_fmt(dt):
    """tz.fmt, but NaT-safe — a job with no comment at all leaves
    last_comment_at/comment_at as pandas' NaT, not None, and NaT doesn't
    support .astimezone() the way tz.fmt assumes. Only needed for the CSV
    export; the on-screen table keeps these as real datetime64 columns so
    DatetimeColumn's own timezone= handles the shift and NaT natively."""
    return tz.fmt(dt) if pd.notna(dt) else "—"


_STATUS_DISPLAY = {
    "new": "⚪ New",
    "in_progress": "🔵 In progress",
    "blocked": "🟠 Blocked",
    "done": "🟢 Done",
    "closed": "⚫ Closed",
}

_FLAG_DISPLAY = {
    "": "",
    "EC": "📌 EC",
    "Stale": "⏳ Stale",
    "EC, Stale": "📌⏳ EC+Stale",
}

_TABLE_HEIGHT = 560


def render(user: dict) -> None:
    ui.page_header("Follow-ups", "What still needs chasing, across every open job — not a comment dump.")

    owner_id, service_code, include_completed, full_log = _filters_row()

    summary_df = followups.job_summary(False, owner_id, service_code)
    _summary_strip(summary_df)

    st.write("")
    if full_log:
        df = followups.comment_log(include_completed, owner_id, service_code)
    else:
        df = followups.job_summary(include_completed, owner_id, service_code)
    df = followups.sort_by_flag_then_staleness(df)

    _table_header(df, full_log)

    if df.empty:
        st.caption("✅ All clear — nothing to follow up on.")
        return

    _render_table(df, full_log)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _filters_row() -> tuple:
    c1, c2, c3, c4 = st.columns([1.3, 1.3, 1, 1])
    with c1:
        staff = [s for s in models.list_staff() if s["role"] != "client"]
        owner_names = {s["id"]: titlecase_name(s["name"]) for s in staff}
        owner_choice = st.selectbox(
            "Owner", options=["All"] + [s["id"] for s in staff],
            format_func=lambda v: "All owners" if v == "All" else owner_names[v],
            key="fu_owner",
        )
    with c2:
        services = models.list_service_catalogue()
        service_names = {sv["code"]: sv["name"] for sv in services}
        service_choice = st.selectbox(
            "Service", options=["All"] + [sv["code"] for sv in services],
            format_func=lambda v: "All services" if v == "All" else service_names[v],
            key="fu_service",
        )
    with c3:
        include_completed = st.checkbox("Include completed", value=False, key="fu_include_completed")
    with c4:
        full_log = st.checkbox("Full comment log", value=False, key="fu_full_log")

    owner_id = None if owner_choice == "All" else owner_choice
    service_code = None if service_choice == "All" else service_choice
    return owner_id, service_code, include_completed, full_log


# ---------------------------------------------------------------------------
# Summary strip — always open jobs only, regardless of the toggles below,
# so it reads as a stable pulse-check rather than shifting when someone
# flips "Include completed" to look at history.
# ---------------------------------------------------------------------------
def _summary_strip(summary_df: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open follow-ups", len(summary_df))
    c2.metric("Flagged", int((summary_df["flag"] != "").sum()) if not summary_df.empty else 0)
    c3.metric(
        "Stale > 3 days",
        int((summary_df["days_since_activity"] > followups.STALE_DAYS).sum()) if not summary_df.empty else 0,
    )
    c4.metric(
        "Oldest (days)",
        int(summary_df["days_since_activity"].max()) if not summary_df.empty else 0,
    )


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------
def _table_header(df: pd.DataFrame, full_log: bool) -> None:
    left, right = st.columns([3, 1])
    with left:
        label = f"{len(df)} comment(s)" if full_log else f"{len(df)} job(s)"
        st.markdown(f"#### {label}")
    with right:
        if not df.empty:
            csv_bytes = _build_csv(df, full_log)
            filename = f"follow-ups_{tz.today_lagos().isoformat()}.csv"
            st.download_button(
                "Download CSV", data=csv_bytes, file_name=filename, mime="text/csv",
                type="primary", key="fu_download", use_container_width=True,
            )


def _render_table(df: pd.DataFrame, full_log: bool) -> None:
    s_token = st.query_params.get("s", "")
    show = pd.DataFrame(index=df.index)
    show["Job ID"] = df.apply(lambda r: f"?s={s_token}&j={int(r['id'])}&p=Follow-ups#{r['job_code']}", axis=1)
    show["Client"] = df["client_name"].map(lambda n: titlecase_name(n) if pd.notna(n) else "—")
    show["Service"] = df["service_name"].fillna("—")
    show["Owner"] = df["owner_name"].map(lambda n: titlecase_name(n) if pd.notna(n) else "—")
    show["Status"] = df["status"].map(lambda s: _STATUS_DISPLAY.get(s, s))
    show["Last comment"] = (df["comment_body"] if full_log else df["last_comment"]).fillna("—")
    # Pre-formatted to a Lagos string, not left as a datetime64 column for
    # st.column_config.DatetimeColumn to render — confirmed in isolation
    # that it shows a missing value as the literal text "None", not blank,
    # which would read inconsistently next to this row's own "—" in Last
    # comment. _safe_fmt gives "—" for a job with no comment, same as
    # every other missing value on this page.
    show["Last comment date"] = (df["comment_at"] if full_log else df["last_comment_at"]).map(_safe_fmt)
    show["Age"] = df["days_since_activity"].map(_age_dot)
    show["Days since last activity"] = df["days_since_activity"]
    show["Flag"] = df["flag"].map(lambda f: _FLAG_DISPLAY.get(f, f))
    if full_log:
        show["Author"] = df["comment_author"].map(lambda n: titlecase_name(n) if pd.notna(n) else "—")

    columns = ["Job ID", "Client", "Service", "Owner", "Status", "Last comment", "Last comment date",
               "Age", "Days since last activity", "Flag"]
    if full_log:
        columns.append("Author")

    # Row height is Streamlit's own default (~35px) plus the header; capping
    # at _TABLE_HEIGHT keeps a long table scrollable instead of sprawling
    # the page, but a short table gets a height that fits it instead of a
    # wall of empty grid lines below the last real row.
    height = min(_TABLE_HEIGHT, 38 + 35 * (len(show) + 1))

    st.dataframe(
        show[columns],
        column_config={
            "Job ID": st.column_config.LinkColumn("Job ID", display_text=r"#(.*)$", width="small"),
            "Last comment": st.column_config.TextColumn("Last comment", width="large"),
            "Last comment date": st.column_config.TextColumn("Last comment date", width="medium"),
            "Age": st.column_config.TextColumn("", width="small"),
            "Days since last activity": st.column_config.NumberColumn("Days since last activity", format="%d"),
            "Flag": st.column_config.TextColumn("Flag", width="small"),
        },
        hide_index=True, use_container_width=True, height=height,
    )


def _age_dot(days: float) -> str:
    if days > 7:
        return "🔴"
    if days > followups.STALE_DAYS:
        return "🟠"
    return "🟢"


# ---------------------------------------------------------------------------
# CSV export — plain text, no emoji/markup, so it reads cleanly offline or
# fed to an LLM. Carries the same rows/filters as what's on screen.
# ---------------------------------------------------------------------------
def _build_csv(df: pd.DataFrame, full_log: bool) -> bytes:
    snapshot = tz.fmt(tz.now_utc())
    out = pd.DataFrame(index=df.index)
    out["Job ID"] = df["job_code"]
    out["Client"] = df["client_name"].map(lambda n: titlecase_name(n) if pd.notna(n) else "")
    out["Service"] = df["service_name"].fillna("")
    out["Owner"] = df["owner_name"].map(lambda n: titlecase_name(n) if pd.notna(n) else "")
    out["Status"] = df["status"].map(lambda s: s.replace("_", " ").title())
    out["Last comment"] = (df["comment_body"] if full_log else df["last_comment"]).fillna("")
    out["Last comment date"] = (df["comment_at"] if full_log else df["last_comment_at"]).map(
        lambda dt: tz.fmt(dt) if pd.notna(dt) else ""
    )
    out["Days since last activity"] = df["days_since_activity"]
    out["Flag"] = df["flag"]
    if full_log:
        out["Author"] = df["comment_author"].map(lambda n: titlecase_name(n) if pd.notna(n) else "")
    out["Snapshot generated at"] = snapshot
    return out.to_csv(index=False).encode("utf-8")
