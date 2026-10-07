"""Timezone helpers: every timestamp is stored in UTC (the database and
Python default) and only ever converted to Africa/Lagos at the point it's
shown to someone — so storage, comparison and ordering stay simple and
unambiguous, and nothing has to guess what timezone a stored value is
"really" in. Lagos has no DST (permanently UTC+1), so this conversion is
never ambiguous either."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


def now_utc() -> datetime:
    """The current instant, explicitly UTC — what every timestamp column
    should be written with."""
    return datetime.now(timezone.utc)


def today_lagos() -> date:
    """Today's calendar date as experienced in Lagos — the default for
    anything framed as "today" to the person doing the action, such as a
    recurring checklist's Date completed field."""
    return now_utc().astimezone(LAGOS).date()


def lagos_noon_utc(d: date) -> datetime:
    """A given Africa/Lagos calendar date, anchored at noon before
    converting to UTC — so the stored instant round-trips back to the same
    Lagos date no matter which side of midnight a ±1 hour offset could
    otherwise push it to. Used when only a date (not a precise moment) is
    being recorded, e.g. an edited "Date completed"."""
    return datetime.combine(d, time(12, 0), tzinfo=LAGOS).astimezone(timezone.utc)


def to_lagos(dt: datetime | None) -> datetime | None:
    """A stored timestamp (UTC, or naive-and-assumed-UTC) converted to
    Africa/Lagos."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LAGOS)


def to_lagos_date(dt: datetime | None) -> date | None:
    """The Africa/Lagos calendar date a stored timestamp falls on — what a
    date_input showing a stored completed_at should display."""
    lagos = to_lagos(dt)
    return lagos.date() if lagos else None


def fmt(dt: datetime | None, pattern: str = "%d %b %Y, %H:%M") -> str:
    """Render a stored timestamp in Africa/Lagos for display. '—' for None,
    consistent with how every other missing value reads in this app."""
    lagos = to_lagos(dt)
    return lagos.strftime(pattern) if lagos else "—"
