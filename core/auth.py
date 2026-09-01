"""Real per-user authentication: email + password checked against the staff
table, session held in Streamlit's session_state. Not a role picker."""

from __future__ import annotations

import bcrypt
import streamlit as st

from core.db import query_one

SESSION_KEY = "rabbi_user"


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


def login(user: dict) -> None:
    st.session_state[SESSION_KEY] = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "client_id": user["client_id"],
    }


def logout() -> None:
    st.session_state.pop(SESSION_KEY, None)
    for key in (
        "nav_job_pk", "nav_invoice_pk", "nav_create_invoice",
        "invoice_seed_job", "invoice_seed_client", "invoice_revise_id",
    ):
        st.session_state.pop(key, None)


def require_login() -> dict:
    user = current_user()
    if user is None:
        st.stop()
    return user
