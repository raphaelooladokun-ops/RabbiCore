"""Applies the schema and seeds reference/demo data.

Runs once per running app process (guarded by st.cache_resource in app.py),
not on every script rerun — Streamlit reruns this module's callers on every
click, and Neon's free tier does not need a schema check on each of those.
"""

from datetime import date, timedelta
from pathlib import Path

import bcrypt
import streamlit as st

from core.db import execute, execute_returning, query, query_one, get_cursor
from core.seed_data import (
    DEMO_CLIENTS,
    DEMO_INVOICES,
    DEMO_JOBS,
    DEMO_PASSWORD,
    DEMO_STAFF,
    PILLAR_TO_CATEGORY,
    SERVICE_CATALOGUE,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def ensure_schema() -> None:
    sql = SCHEMA_PATH.read_text()
    with get_cursor(commit=True) as cur:
        cur.execute(sql)


def seed_catalogue() -> None:
    for i, svc in enumerate(SERVICE_CATALOGUE):
        execute(
            """
            INSERT INTO service_catalogue (code, pillar, name, fields, sort_order)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (code) DO UPDATE SET
                pillar = EXCLUDED.pillar,
                name = EXCLUDED.name,
                fields = EXCLUDED.fields,
                sort_order = EXCLUDED.sort_order
            """,
            (svc["code"], svc["pillar"], svc["name"], _to_json(svc["fields"]), i),
        )


def _to_json(value) -> str:
    import json

    return json.dumps(value)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def seed_demo_data() -> None:
    """Idempotent: safe to call on every process start."""
    client_ids = {}
    for c in DEMO_CLIENTS:
        row = query_one("SELECT id FROM client WHERE name = %s", (c["name"],))
        if row is None:
            row = execute_returning(
                """
                INSERT INTO client (name, rc_number, contact_name, contact_email, contact_phone)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (c["name"], c["rc_number"], c["contact_name"], c["contact_email"], c["contact_phone"]),
            )
        client_ids[c["name"]] = row["id"]

    staff_ids = {}
    for s in DEMO_STAFF:
        client_id = client_ids.get(s["client_name"]) if s["client_name"] else None
        row = query_one("SELECT id FROM staff WHERE email = %s", (s["email"],))
        if row is None:
            row = execute_returning(
                """
                INSERT INTO staff (name, email, password_hash, role, client_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (s["name"], s["email"], _hash(DEMO_PASSWORD), s["role"], client_id),
            )
        staff_ids[s["email"]] = row["id"]

    today = date.today()
    job_pk_by_job_id = {}
    for j in DEMO_JOBS:
        existing = query_one("SELECT id, status FROM job WHERE job_id = %s", (j["job_id"],))
        if existing:
            job_pk_by_job_id[j["job_id"]] = existing["id"]
            continue

        service = next(s for s in SERVICE_CATALOGUE if s["code"] == j["service_code"])
        category = PILLAR_TO_CATEGORY[service["pillar"]]
        blocked_by_pk = job_pk_by_job_id.get(j["blocked_by_job_id"]) if j["blocked_by_job_id"] else None
        sla_date = today + timedelta(days=j["sla_offset_days"]) if j["sla_offset_days"] is not None else None

        row = execute_returning(
            """
            INSERT INTO job (job_id, client_id, category, service_type, title, owner_id,
                              status, source, created_by, sla_date, blocked_by, waiting_on_client)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                j["job_id"], client_ids[j["client_name"]], category, service["code"], j["title"],
                staff_ids[j["owner_email"]], j["status"], j["source"], staff_ids[j["owner_email"]],
                sla_date, blocked_by_pk, j["waiting_on_client"],
            ),
        )
        job_pk_by_job_id[j["job_id"]] = row["id"]

    for inv in DEMO_INVOICES:
        existing = query_one("SELECT id FROM invoice WHERE invoice_code = %s", (inv["invoice_code"],))
        if existing:
            continue
        invoice_row = execute_returning(
            """
            INSERT INTO invoice (invoice_code, client_id, status, issued_at, paid_at)
            VALUES (%s, %s, %s, now(), now())
            RETURNING id
            """,
            (inv["invoice_code"], client_ids[inv["client_name"]], inv["status"]),
        )
        for job_id in inv["job_ids"]:
            execute(
                "UPDATE job SET invoice_id = %s WHERE job_id = %s",
                (invoice_row["id"], job_id),
            )
            demo_job = next(j for j in DEMO_JOBS if j["job_id"] == job_id)
            if demo_job.get("close_after_invoicing"):
                execute("UPDATE job SET status = 'closed' WHERE job_id = %s", (job_id,))


@st.cache_resource(show_spinner="Preparing Rabbi Core...")
def bootstrap_once() -> bool:
    ensure_schema()
    seed_catalogue()
    seed_demo_data()
    return True
