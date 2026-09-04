"""Real per-user authentication: email + password checked against the staff
table, session held in Streamlit's session_state. Not a role picker.

Streamlit's session_state does not survive a hard browser reload, so a login
also drops a signed, expiring token into the URL's query params (`?s=...`)
and a reload re-derives the session from that token instead of bouncing back
to the login screen. The token is HMAC-signed with a key derived from the
Neon connection string already in st.secrets — no new secret to manage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import bcrypt
import streamlit as st

from core.db import query_one

SESSION_KEY = "rabbi_user"
_TOKEN_PARAM = "s"
_TOKEN_TTL_SECONDS = 12 * 60 * 60  # 12 hours


def verify_login(email: str, password: str) -> dict | None:
    user = query_one(
        "SELECT id, name, email, password_hash, role, client_id, active "
        "FROM staff WHERE lower(email) = lower(%s)",
        (email,),
    )
    if user is None or not user["active"]:
        return None
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return None
    return user


def current_user() -> dict | None:
    return st.session_state.get(SESSION_KEY)


def _signing_key() -> bytes:
    try:
        url = st.secrets["neon"]["url"]
    except (KeyError, FileNotFoundError):
        url = "rabbi-core-no-secrets-configured"
    return hashlib.sha256(url.encode()).digest()


def _sign_token(staff_id: int) -> str:
    expiry = int(time.time()) + _TOKEN_TTL_SECONDS
    payload = f"{staff_id}:{expiry}"
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_token(token: str) -> int | None:
    try:
        staff_id_s, expiry_s, sig = base64.urlsafe_b64decode(token.encode()).decode().split(":")
        expected = hmac.new(_signing_key(), f"{staff_id_s}:{expiry_s}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected) or int(expiry_s) < time.time():
            return None
        return int(staff_id_s)
    except Exception:
        return None


def login(user: dict) -> None:
    st.session_state[SESSION_KEY] = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "client_id": user["client_id"],
    }
    # Persists the login across a hard page reload — Streamlit's own
    # session_state can't. Note: anyone who obtains this URL (a copied or
    # shared link) can use it to sign in as this user until it expires.
    st.query_params[_TOKEN_PARAM] = _sign_token(user["id"])


def restore_session_from_query_params() -> None:
    """Called once near the top of app.py before the login gate: if the
    browser has no live session but the URL carries a valid, unexpired
    token (left there by a previous login on this same tab), re-populate
    the session instead of forcing the user to sign in again."""
    if current_user() is not None:
        return
    token = st.query_params.get(_TOKEN_PARAM)
    if not token:
        return
    staff_id = _verify_token(token)
    if staff_id is None:
        return
    user = query_one(
        "SELECT id, name, email, role, client_id, active FROM staff WHERE id = %s", (staff_id,)
    )
    if user is None or not user["active"]:
        return
    login(user)


def logout() -> None:
    st.session_state.pop(SESSION_KEY, None)
    for key in (
        "nav_job_pk", "nav_invoice_pk", "nav_create_invoice", "_current_page", "_nav_restored",
        "invoice_seed_job", "invoice_seed_client", "invoice_revise_id",
    ):
        st.session_state.pop(key, None)
    for param in (_TOKEN_PARAM, "p", "j", "i", "ci"):
        st.query_params.pop(param, None)


def require_login() -> dict:
    user = current_user()
    if user is None:
        st.stop()
    return user
