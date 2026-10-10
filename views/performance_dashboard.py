"""Performance dashboard — EC/super_admin only. Three tabs (Money, Jobs,
People) that each lead with what's leaking or stalled, not a vanity total.
Every chart/table reads from core.analytics, which does the aggregation in
Neon (GROUP BY) and caches the result — this module only shapes what comes
back into Altair charts and st.dataframe + column_config tables.

Recurring (prepaid) jobs never carry an invoice by design, so every job-
level figure here (revenue, leakage, cycle time, stalls, reopens) is
project-job-only — see core.analytics' own module docstring. Recurring
revenue is its own amortised line, sourced from each client's own annual
retainer amount, not from any job."""

from __future__ import annotations

from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from core import analytics, models
from core import tz
from core import ui
from core.constants import CATEGORY_LABELS, STATUS_LABELS_SHORT, titlecase_name

# ---------------------------------------------------------------------------
# Colour — reusing the app's own RISK_COLORS for urgency (aging, stalls,
# leakage) everywhere a reader already knows red/amber/green from the rest
# of Rabbi Core; a separate, fixed-order categorical ramp (validated for
# colour-vision-deficiency separation) for identity dimensions the app has
# no existing colour for — pillar and the Project/Recurring revenue split.
# Same pillar keeps the same colour in every chart it appears in.
# ---------------------------------------------------------------------------
_PILLAR_ORDER = ["front_office", "cac", "immigration", "cit", "state", "other"]
_CAT_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"]
_PILLAR_COLOR = dict(zip(_PILLAR_ORDER, _CAT_COLORS))

_SERIES_PROJECT = "#2a78d6"
_SERIES_RECURRING = "#1baf7a"

_AGE_GOOD = "#0ca30c"
_AGE_WARNING = "#fab219"
_AGE_CRITICAL = "#d03b3b"

_STATUS_ORDER = ["new", "in_progress", "blocked", "done"]


def _compact_naira(amount: float) -> str:
    """A KPI tile is narrow — "₦1,356,000" doesn't fit, "₦1.36M" does.
    Full precision stays in every table; this is tile display only."""
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000:
        return f"{sign}₦{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{sign}₦{amount / 1_000:.0f}K"
    return f"{sign}₦{amount:,.0f}"


def render(user: dict) -> None:
    ui.page_header("Performance", "Leaks and stalls first — revenue, job health, and who's carrying what.")
    st.caption(
        "Only as complete as what's been logged and invoiced — a blank figure means nothing was "
        "recorded, not zero. Recurring (prepaid) jobs are excluded from every job-level figure here; "
        "their revenue is the separate amortised line below."
    )

    date_from, date_to, pillar, owner_id = _filters_row()
    _kpi_row(date_from, date_to, pillar, owner_id)

    tab_money, tab_jobs, tab_people = st.tabs(["Money", "Jobs", "People"])
    with tab_money:
        _money_tab(date_from, date_to, pillar, owner_id)
    with tab_jobs:
        _jobs_tab(date_from, date_to, pillar, owner_id)
    with tab_people:
        _people_tab(date_from, date_to, pillar)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _filters_row() -> tuple:
    today = tz.today_lagos()
    default_from = today - timedelta(days=365)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        date_range = st.date_input(
            "Date range", value=(default_from, today), max_value=today, key="perf_daterange",
        )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        date_from, date_to = date_range
    else:
        date_from, date_to = default_from, today

    with c2:
        pillar_options = ["All"] + _PILLAR_ORDER
        pillar_choice = st.selectbox(
            "Pillar", options=pillar_options,
            format_func=lambda v: "All pillars" if v == "All" else CATEGORY_LABELS.get(v, v),
            key="perf_pillar",
        )
    with c3:
        staff = [s for s in models.list_staff() if s["role"] != "client"]
        owner_names = {s["id"]: titlecase_name(s["name"]) for s in staff}
        owner_options = ["All"] + [s["id"] for s in staff]
        owner_choice = st.selectbox(
            "Owner", options=owner_options,
            format_func=lambda v: "All owners" if v == "All" else owner_names[v],
            key="perf_owner",
        )

    pillar = None if pillar_choice == "All" else pillar_choice
    owner_id = None if owner_choice == "All" else owner_choice
    return date_from, date_to, pillar, owner_id


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
def _weighted_avg_days(df: pd.DataFrame) -> float | None:
    if df.empty or df["n"].sum() == 0:
        return None
    return float((df["avg_days"] * df["n"]).sum() / df["n"].sum())


def _kpi_row(date_from, date_to, pillar, owner_id) -> None:
    prev_from, prev_to = analytics.shifted_range(date_from, date_to)

    leaked = analytics.leakage_jobs(date_from, date_to, pillar, owner_id)
    leak = analytics.leakage_summary(leaked)
    leaked_prev = analytics.leakage_jobs(prev_from, prev_to, pillar, owner_id)
    leak_prev = analytics.leakage_summary(leaked_prev)

    receivables = analytics.receivables_detail(pillar, owner_id)
    receivables_total = float(receivables["amount"].sum()) if not receivables.empty else 0.0
    receivables_n = int(receivables["invoice_id"].nunique()) if not receivables.empty else 0

    open_df = analytics.open_jobs_by_status(pillar, owner_id)
    open_count = int(open_df.loc[open_df["status"] != "done", "n"].sum()) if not open_df.empty else 0

    cycle_df = analytics.cycle_time_by_pillar(date_from, date_to, owner_id)
    if pillar and not cycle_df.empty:
        cycle_df = cycle_df[cycle_df["pillar"] == pillar]
    avg_cycle = _weighted_avg_days(cycle_df)
    cycle_df_prev = analytics.cycle_time_by_pillar(prev_from, prev_to, owner_id)
    if pillar and not cycle_df_prev.empty:
        cycle_df_prev = cycle_df_prev[cycle_df_prev["pillar"] == pillar]
    avg_cycle_prev = _weighted_avg_days(cycle_df_prev)

    waiting_ec = models.count_open_ec_action_points()

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        if leak["amount"] is not None:
            delta = leak["amount"] - leak_prev["amount"] if leak_prev["amount"] is not None else None
            st.metric(
                "Leakage", _compact_naira(leak["amount"]),
                delta=_compact_naira(delta) if delta is not None else None, delta_color="inverse",
            )
        else:
            st.metric("Leakage (no costing on file)", leak["count"])
        st.caption(f"{leak['count']} job(s) done/closed, unbilled")
    with c2:
        st.metric("Receivables", _compact_naira(receivables_total))
        st.caption(f"{receivables_n} invoice(s) approved, unpaid")
    with c3:
        st.metric("Open jobs", open_count)
    with c4:
        if avg_cycle is not None:
            delta = (avg_cycle - avg_cycle_prev) if avg_cycle_prev is not None else None
            st.metric(
                "Avg cycle time", f"{avg_cycle:.1f}d",
                delta=f"{delta:+.1f}d" if delta is not None else None, delta_color="inverse",
                help="Average days from created to done, project jobs only.",
            )
        else:
            st.metric("Avg cycle time", "—")
    with c5:
        st.metric("Waiting on EC", waiting_ec)


# ---------------------------------------------------------------------------
# MONEY tab
# ---------------------------------------------------------------------------
def _money_tab(date_from, date_to, pillar, owner_id) -> None:
    st.markdown("#### Revenue trend")
    rev_df = analytics.revenue_by_month(date_from, date_to, pillar, owner_id)
    recurring = analytics.recurring_revenue_monthly()
    if recurring["clients_set"] == 0:
        st.caption(
            "⚠️ No client has an annual retainer amount on file — recurring revenue shows as ₦0 "
            "until one is set on a client's own page."
        )
    elif recurring["clients_set"] < recurring["clients_total"]:
        st.caption(
            f"Recurring revenue reflects {recurring['clients_set']} of {recurring['clients_total']} "
            "active clients with a retainer on file, shown flat per month (today's base ÷ 12) — not a "
            "historical reconstruction, since retainer start/change dates aren't tracked."
        )
    trend = _build_revenue_trend(rev_df, recurring["monthly"], date_from, date_to)
    if trend.empty:
        st.caption("No revenue in this period.")
    else:
        st.altair_chart(_revenue_trend_chart(trend), use_container_width=True)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Revenue by pillar")
        pillar_df = analytics.revenue_by_pillar(date_from, date_to, owner_id)
        if pillar_df.empty:
            st.caption("No project revenue in this period.")
        else:
            st.altair_chart(_pillar_bar_chart(pillar_df), use_container_width=True)
    with col2:
        st.markdown("#### Top clients")
        clients_df = analytics.top_clients(date_from, date_to, pillar, owner_id)
        if clients_df.empty:
            st.caption("No project revenue in this period.")
        else:
            st.altair_chart(_clients_bar_chart(clients_df), use_container_width=True)
            total_revenue = float(rev_df["revenue"].sum()) if not rev_df.empty else 0.0
            if total_revenue:
                top = clients_df.iloc[0]
                pct = top["revenue"] / total_revenue * 100
                st.caption(f"Top client ({titlecase_name(top['client'])}) is {pct:.0f}% of revenue in this period.")

    st.markdown("#### Leakage — done/closed, never invoiced")
    leaked = analytics.leakage_jobs(date_from, date_to, pillar, owner_id)
    _leakage_table(leaked)

    st.markdown("#### Receivables aging")
    receivables = analytics.receivables_detail(pillar, owner_id)
    aging = analytics.receivables_aging_summary(receivables)
    _receivables_tables(aging, receivables)


def _build_revenue_trend(rev_df: pd.DataFrame, recurring_monthly: float, date_from, date_to) -> pd.DataFrame:
    months = pd.date_range(
        start=pd.Timestamp(date_from).replace(day=1), end=pd.Timestamp(date_to), freq="MS",
    )
    if len(months) == 0:
        return pd.DataFrame(columns=["month", "series", "revenue"])
    project_by_month = {pd.Timestamp(r["month"]): float(r["revenue"]) for _, r in rev_df.iterrows()}
    rows = []
    for m in months:
        rows.append({"month": m, "series": "Project", "revenue": project_by_month.get(m, 0.0)})
        rows.append({"month": m, "series": "Recurring", "revenue": recurring_monthly})
    return pd.DataFrame(rows)


def _revenue_trend_chart(trend: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(trend)
        .mark_bar(size=18)
        .encode(
            x=alt.X("month:T", title=None, axis=alt.Axis(format="%b %Y")),
            y=alt.Y("revenue:Q", title="Revenue (₦)", stack="zero"),
            color=alt.Color(
                "series:N", title=None,
                scale=alt.Scale(domain=["Project", "Recurring"], range=[_SERIES_PROJECT, _SERIES_RECURRING]),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("month:T", title="Month", format="%b %Y"),
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("revenue:Q", title="Revenue", format=",.0f"),
            ],
        )
        .configure_axis(gridOpacity=0.25, domain=False)
        .properties(height=320)
    )


def _pillar_bar_chart(pillar_df: pd.DataFrame) -> alt.Chart:
    df = pillar_df.copy()
    df["pillar_label"] = df["pillar"].map(lambda c: CATEGORY_LABELS.get(c, c))
    return (
        alt.Chart(df)
        .mark_bar(size=20, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("revenue:Q", title="Revenue (₦)"),
            y=alt.Y("pillar_label:N", title=None, sort="-x"),
            color=alt.Color(
                "pillar:N", legend=None,
                scale=alt.Scale(domain=list(_PILLAR_COLOR.keys()), range=list(_PILLAR_COLOR.values())),
            ),
            tooltip=[alt.Tooltip("pillar_label:N", title="Pillar"), alt.Tooltip("revenue:Q", title="Revenue", format=",.0f")],
        )
        .configure_axis(gridOpacity=0.25, domain=False)
        .properties(height=260)
    )


def _clients_bar_chart(clients_df: pd.DataFrame) -> alt.Chart:
    df = clients_df.copy()
    df["client_label"] = df["client"].map(titlecase_name)
    return (
        alt.Chart(df)
        .mark_bar(size=18, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=_SERIES_PROJECT)
        .encode(
            x=alt.X("revenue:Q", title="Revenue (₦)"),
            y=alt.Y("client_label:N", title=None, sort="-x"),
            tooltip=[alt.Tooltip("client_label:N", title="Client"), alt.Tooltip("revenue:Q", title="Revenue", format=",.0f")],
        )
        .configure_axis(gridOpacity=0.25, domain=False)
        .properties(height=260)
    )


def _age_badge(days: float, good_max: float, warn_max: float) -> str:
    if days <= good_max:
        return "🟢"
    if days <= warn_max:
        return "🟠"
    return "🔴"


def _leakage_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.caption("No leakage in this period — every done/closed project job on file has an invoice.")
        return
    s_token = st.query_params.get("s", "")
    show = df.copy()
    show["Job"] = show.apply(lambda r: f"?s={s_token}&j={int(r['id'])}#{r['job_code']}", axis=1)
    show["Client"] = show["client_name"].map(lambda n: titlecase_name(n) if n else "—")
    show["What"] = show["title"]
    show["Pillar"] = show["pillar"].map(lambda c: CATEGORY_LABELS.get(c, c))
    show["Status"] = show["status"].map(lambda s: STATUS_LABELS_SHORT.get(s, s))
    show["Owner"] = show["owner_name"].map(lambda n: titlecase_name(n) if n else "—")
    show["Completed"] = show["completed_at"].map(tz.to_lagos)
    show["Est. value (₦)"] = show["est_value"]

    priced = int(show["Est. value (₦)"].notna().sum())
    if priced < len(show):
        st.caption(
            f"Estimated value shown for {priced} of {len(show)} job(s) with a costing sheet on file — "
            "the rest have none entered, so no value is shown for them."
        )

    st.dataframe(
        show[["Job", "Client", "What", "Pillar", "Status", "Owner", "Completed", "Est. value (₦)"]],
        column_config={
            "Job": st.column_config.LinkColumn("Job", display_text=r"#(.*)$", width="small"),
            "Completed": st.column_config.DatetimeColumn("Completed", format="D MMM YYYY"),
            "Est. value (₦)": st.column_config.NumberColumn("Est. value (₦)", format="₦%.2f"),
        },
        hide_index=True, use_container_width=True,
    )


def _receivables_tables(aging: pd.DataFrame, detail: pd.DataFrame) -> None:
    if detail.empty:
        st.caption("Nothing outstanding — no approved, unpaid invoices.")
        return

    total = float(aging["amount"].sum())
    aging_show = aging.copy()
    aging_show["Bucket"] = aging_show["bucket"]
    aging_show["Badge"] = aging_show["bucket"].map(
        {"0-30 days": "🟢", "30-60 days": "🟠", "60+ days": "🔴"}
    )
    aging_show["Amount (₦)"] = aging_show["amount"]
    aging_show["Invoices"] = aging_show["count"]
    st.dataframe(
        aging_show[["Badge", "Bucket", "Amount (₦)", "Invoices"]],
        column_config={
            "Amount (₦)": st.column_config.ProgressColumn(
                "Amount (₦)", format="₦%.0f", min_value=0, max_value=max(total, 1),
            ),
        },
        hide_index=True, use_container_width=True,
    )

    with st.expander(f"{len(detail)} outstanding invoice(s) — detail"):
        s_token = st.query_params.get("s", "")
        d = detail.sort_values("age_days", ascending=False).copy()
        d["Client"] = d["client_name"].map(titlecase_name)
        d["Invoice"] = d.apply(lambda r: f"?s={s_token}&i={int(r['invoice_id'])}#{r['invoice_code']}", axis=1)
        d["Invoice date"] = d["invoice_date"]
        d["Age (days)"] = d["age_days"]
        d["Badge"] = d["age_days"].map(lambda a: _age_badge(a, 30, 60))
        d["Amount (₦)"] = d["amount"]
        st.dataframe(
            d[["Badge", "Invoice", "Client", "Invoice date", "Age (days)", "Amount (₦)"]],
            column_config={
                "Invoice": st.column_config.LinkColumn("Invoice", display_text=r"#(.*)$", width="small"),
                "Amount (₦)": st.column_config.NumberColumn("Amount (₦)", format="₦%.2f"),
                "Age (days)": st.column_config.ProgressColumn("Age (days)", min_value=0, max_value=90),
            },
            hide_index=True, use_container_width=True,
        )


# ---------------------------------------------------------------------------
# JOBS tab
# ---------------------------------------------------------------------------
def _jobs_tab(date_from, date_to, pillar, owner_id) -> None:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Open jobs by status")
        status_df = analytics.open_jobs_by_status(pillar, owner_id)
        if status_df.empty:
            st.caption("No open project jobs match these filters.")
        else:
            st.altair_chart(_status_bar_chart(status_df), use_container_width=True)
    with col2:
        st.markdown("#### Cycle time by pillar")
        trend_df = analytics.cycle_time_trend(date_from, date_to, pillar, owner_id)
        if trend_df.empty:
            st.caption("No completed jobs with cycle-time data in this period.")
        else:
            st.altair_chart(_cycle_time_chart(trend_df), use_container_width=True)

    st.markdown("#### Stalled jobs — no activity in 3+ days")
    stalled = analytics.stalled_jobs(pillar, owner_id)
    _stalled_table(stalled)

    st.markdown("#### Waiting on EC")
    _waiting_ec_table()


def _status_bar_chart(status_df: pd.DataFrame) -> alt.Chart:
    df = status_df.copy()
    df["status_label"] = df["status"].map(lambda s: STATUS_LABELS_SHORT.get(s, s))
    order = [STATUS_LABELS_SHORT.get(s, s) for s in _STATUS_ORDER]
    return (
        alt.Chart(df)
        .mark_bar(size=28, color=_SERIES_PROJECT, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("status_label:N", title=None, sort=order),
            y=alt.Y("n:Q", title="Jobs"),
            tooltip=[alt.Tooltip("status_label:N", title="Status"), alt.Tooltip("n:Q", title="Jobs")],
        )
        .configure_axis(gridOpacity=0.25, domain=False)
        .properties(height=260)
    )


def _cycle_time_chart(trend_df: pd.DataFrame) -> alt.Chart:
    df = trend_df.copy()
    df["pillar_label"] = df["pillar"].map(lambda c: CATEGORY_LABELS.get(c, c))
    label_domain = [CATEGORY_LABELS.get(p, p) for p in _PILLAR_COLOR]
    return (
        alt.Chart(df)
        .mark_line(size=2, point=alt.OverlayMarkDef(size=60, filled=True))
        .encode(
            x=alt.X("month:T", title=None, axis=alt.Axis(format="%b %Y")),
            y=alt.Y("avg_days:Q", title="Avg days to done"),
            color=alt.Color(
                "pillar_label:N", title=None,
                scale=alt.Scale(domain=label_domain, range=list(_PILLAR_COLOR.values())),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("month:T", title="Month", format="%b %Y"),
                alt.Tooltip("pillar_label:N", title="Pillar"),
                alt.Tooltip("avg_days:Q", title="Avg days", format=".1f"),
            ],
        )
        .configure_axis(gridOpacity=0.25, domain=False)
        .properties(height=300)
    )


def _stalled_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.caption("Nothing stalled — every open project job has moved in the last 3 days.")
        return
    s_token = st.query_params.get("s", "")
    show = df.copy()
    show["Badge"] = show["age_days"].map(lambda a: _age_badge(a, 7, 14))
    show["Job"] = show.apply(lambda r: f"?s={s_token}&j={int(r['id'])}#{r['job_code']}", axis=1)
    show["Client"] = show["client_name"].map(lambda n: titlecase_name(n) if n else "—")
    show["What"] = show["title"]
    show["Status"] = show["status"].map(lambda s: STATUS_LABELS_SHORT.get(s, s))
    show["Owner"] = show["owner_name"].map(lambda n: titlecase_name(n) if n else "—")
    show["Age (days)"] = show["age_days"]
    st.dataframe(
        show[["Badge", "Job", "Client", "What", "Status", "Owner", "Age (days)"]],
        column_config={
            "Job": st.column_config.LinkColumn("Job", display_text=r"#(.*)$", width="small"),
            "Age (days)": st.column_config.ProgressColumn("Age (days)", format="%.1f", min_value=0, max_value=14),
        },
        hide_index=True, use_container_width=True,
    )


def _waiting_ec_table() -> None:
    points = models.list_open_ec_action_points()
    if not points:
        st.caption("Nothing waiting on EC right now.")
        return
    s_token = st.query_params.get("s", "")
    rows = []
    now = tz.now_utc()
    for p in points:
        age_days = (now - p["action_opened_at"]).total_seconds() / 86400.0
        rows.append(
            {
                "Badge": _age_badge(age_days, 2, 5),
                "Job": f"?s={s_token}&j={p['job_pk']}#{p['job_code']}",
                "Client": titlecase_name(p.get("client_name")) or "—",
                "Raised by": titlecase_name(p["author_name"]),
                "Ask": p["body"],
                "Age (days)": age_days,
            }
        )
    show = pd.DataFrame(rows)
    st.dataframe(
        show,
        column_config={
            "Job": st.column_config.LinkColumn("Job", display_text=r"#(.*)$", width="small"),
            "Age (days)": st.column_config.ProgressColumn("Age (days)", format="%.1f", min_value=0, max_value=7),
        },
        hide_index=True, use_container_width=True,
    )


# ---------------------------------------------------------------------------
# PEOPLE tab
# ---------------------------------------------------------------------------
def _people_tab(date_from, date_to, pillar) -> None:
    st.caption(
        "Speed and reopened rate are shown side by side on purpose — a fast owner whose jobs keep "
        "coming back isn't actually fast."
    )
    perf = analytics.owner_performance(date_from, date_to, pillar)
    if perf.empty:
        st.caption("No project jobs on file for any owner matching these filters.")
        return

    show = perf.copy()
    show["Owner"] = show["owner_name"].map(titlecase_name)
    show["Open jobs"] = show["open_jobs"]
    show["Completed this period"] = show["completed_this_period"]
    show["Avg cycle time (days)"] = show["avg_cycle_days"]
    show["Reopened rate"] = show["reopened_rate"]
    show["_badge_val"] = show["reopened_rate"].fillna(0)
    show["Badge"] = show["_badge_val"].map(lambda r: _age_badge(r, 5, 15))

    st.dataframe(
        show[["Badge", "Owner", "Open jobs", "Completed this period", "Avg cycle time (days)", "Reopened rate"]],
        column_config={
            "Avg cycle time (days)": st.column_config.NumberColumn("Avg cycle time (days)", format="%.1f"),
            "Reopened rate": st.column_config.ProgressColumn("Reopened rate", format="%.0f%%", min_value=0, max_value=100),
        },
        hide_index=True, use_container_width=True,
    )
    st.caption("Reopened rate: jobs that were marked done at some point but are active again, as a share of everything that owner has ever completed.")
