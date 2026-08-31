"""Shared UI building blocks: theme injection, status/risk badges, page
headers — so every screen in the app looks and behaves consistently."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.constants import (
    CATEGORY_LABELS,
    RISK_COLORS,
    RISK_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS_SHORT,
    humanize,
)

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


def sidebar_wordmark() -> None:
    st.sidebar.markdown(
        '<div class="rc-wordmark">RABBI CORE</div>'
        '<div class="rc-tagline">Front Office</div>',
        unsafe_allow_html=True,
    )
