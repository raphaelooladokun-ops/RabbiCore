"""Seed data for Rabbi Core: the locked service catalogue, test logins, and a
small set of demo clients/jobs so the app is usable the moment it's deployed.

Everything here is idempotent (ON CONFLICT ... DO UPDATE / DO NOTHING), so it
is safe to run on every app startup as well as by hand via scripts/init_db.py.
"""

# ---------------------------------------------------------------------------
# THE SERVICE CATALOGUE
# Source: Rabbi — Master Service Catalogue (locked). Each entry becomes a
# `service_type` option; its `fields` become the Type/Routing/Location/Scheme
# sub-fields the capture screen renders when that service is picked.
# ---------------------------------------------------------------------------

def _select(key, label, options):
    return {"key": key, "label": label, "type": "select", "options": options}


SERVICE_CATALOGUE = [
    # ---- PILLAR 1 — CAC (Corporate Affairs) ---------------------------------
    {"code": "CAC-INCORPORATION", "pillar": "CAC", "name": "CAC — Company Incorporation", "fields": []},
    {
        "code": "CAC-AMENDMENT", "pillar": "CAC", "name": "CAC — Amendment / Change",
        "fields": [
            _select("change_type", "Change type",
                    ["Director", "Secretary", "Address", "Name", "Share Capital Increase", "Share Re-allotment"]),
            _select("filed_with", "Filed with", ["CAC", "Immigration", "Both"]),
        ],
    },
    {"code": "CAC-ANNUAL-RETURN", "pillar": "CAC", "name": "CAC — Annual Return", "fields": []},

    # ---- PILLAR 2 — Immigration ----------------------------------------------
    {"code": "IMM-BUSINESS-PERMIT", "pillar": "Immigration", "name": "IMM — Business Permit (Grant)", "fields": []},
    {
        "code": "IMM-QUOTA", "pillar": "Immigration", "name": "IMM — Quota",
        "fields": [_select("quota_type", "Quota type", ["Grant", "Addition", "Renewal"])],
    },
    {
        "code": "IMM-VISA-SHORT-STAY", "pillar": "Immigration", "name": "IMM — Visa (Short Stay)",
        "fields": [_select("visa_type", "Visa type", ["Visa on Arrival", "Business", "Tourist"])],
    },
    {"code": "IMM-VISA-TWP", "pillar": "Immigration", "name": "IMM — Visa (TWP)", "fields": []},
    {
        "code": "IMM-ECERPAC-PRINCIPAL", "pillar": "Immigration", "name": "IMM — E-CERPAC (Principal)",
        "fields": [_select("ecerpac_type", "E-CERPAC type", ["Renewal", "Out-of-Country", "Regularization"])],
    },
    {"code": "IMM-ECERPAC-DEP-SPOUSE", "pillar": "Immigration", "name": "IMM — E-CERPAC (Dependant – Spouse)", "fields": []},
    {"code": "IMM-ECERPAC-DEP-CHILD", "pillar": "Immigration", "name": "IMM — E-CERPAC (Dependant – Child)", "fields": []},
    {"code": "IMM-DELETION-EXPAT", "pillar": "Immigration", "name": "IMM — Deletion of Expatriate", "fields": []},
    {"code": "IMM-CHANGE-EXPAT", "pillar": "Immigration", "name": "IMM — Change of Expatriate", "fields": []},
    {"code": "IMM-NIS-INSPECTION", "pillar": "Immigration", "name": "IMM — NIS Inspection", "fields": []},

    # ---- PILLAR 3 — CIT (Federal Tax) ----------------------------------------
    {"code": "CIT-REGISTRATION", "pillar": "CIT", "name": "CIT — Registration (Tax & VAT Certificate)", "fields": []},
    {"code": "CIT-TRADE-PORTAL", "pillar": "CIT", "name": "CIT — Trade Portal", "fields": []},
    {"code": "CIT-TIN-UPDATE", "pillar": "CIT", "name": "CIT — TIN Update & Validation", "fields": []},
    {
        "code": "CIT-VAT-MONTHLY", "pillar": "CIT", "name": "CIT — Monthly VAT Returns", "fields": [],
        "recurring_frequency": "monthly",
    },
    {
        "code": "CIT-VAT-YEARLY-ANALYSIS", "pillar": "CIT", "name": "CIT — Yearly VAT Analysis", "fields": [],
        "recurring_frequency": "yearly",
    },
    {
        "code": "CIT-VAT-WHT-MONITORING", "pillar": "CIT", "name": "CIT — VAT & WHT Monitoring", "fields": [],
        "recurring_frequency": "monthly",
    },
    {"code": "CIT-TP-FILINGS", "pillar": "CIT", "name": "CIT — TP Filings (Transfer Pricing)", "fields": []},
    {"code": "CIT-TAX-AUDIT", "pillar": "CIT", "name": "CIT — Tax Audit (NRS)", "fields": []},
    {"code": "CIT-TAX-INVESTIGATION", "pillar": "CIT", "name": "CIT — Tax Investigation (NRS)", "fields": []},
    {"code": "CIT-DESK-EXAM", "pillar": "CIT", "name": "CIT — Desk Examination (NRS)", "fields": []},
    {"code": "CIT-STATUTORY-AUDIT", "pillar": "CIT", "name": "CIT — Statutory Audit (AFS)", "fields": []},
    {"code": "CIT-TCC", "pillar": "CIT", "name": "CIT — TCC (Tax Clearance Certificate)", "fields": []},
    {
        "code": "CIT-ANNUAL-RETURN", "pillar": "CIT", "name": "CIT — Annual Return", "fields": [],
        "recurring_frequency": "yearly",
    },

    # ---- PILLAR 4 — State Matters --------------------------------------------
    {"code": "STATE-PAYE-REG", "pillar": "State", "name": "STATE — PAYE Registration", "fields": []},
    {"code": "STATE-PAYE-WHT-MONTHLY", "pillar": "State", "name": "STATE — Monthly PAYE & WHT Returns", "fields": []},
    {"code": "STATE-PAYROLL-MONTHLY", "pillar": "State", "name": "STATE — Monthly Payroll", "fields": []},
    {
        "code": "STATE-PAYE-ANNUAL-RETURN", "pillar": "State", "name": "STATE — PAYE Annual Return",
        "fields": [_select("location", "Location", ["Lagos", "Outside Lagos"])],
    },
    {"code": "STATE-ANNUAL-EXAM-PROJECTION", "pillar": "State", "name": "STATE — Annual Examination & Projection", "fields": []},
    {
        "code": "STATE-PAYE-AUDIT", "pillar": "State", "name": "STATE — PAYE Audit",
        "fields": [_select("location", "Location", ["Lagos", "Outside Lagos"])],
    },
    {
        "code": "STATE-PAYE-INVESTIGATION", "pillar": "State", "name": "STATE — PAYE Investigation",
        "fields": [_select("location", "Location", ["Lagos", "Outside Lagos"])],
    },
    {
        "code": "STATE-INDIVIDUAL-TAX-CLEARANCE", "pillar": "State", "name": "STATE — Individual Tax Clearance",
        "fields": [
            _select("scheme", "Scheme", ["PAYE", "Direct Assessment"]),
            _select("location", "Location", ["Lagos", "Outside Lagos"]),
        ],
    },
    {
        "code": "STATE-TIN-JTB", "pillar": "State", "name": "STATE — Taxpayer TIN / JTB Certificate",
        "fields": [_select("scheme", "Scheme", ["PAYE", "Direct Assessment"])],
    },
    {"code": "STATE-TCC-PAYE", "pillar": "State", "name": "STATE — Processing of TCC (PAYE Scheme)", "fields": []},
    {"code": "STATE-NSITF", "pillar": "State", "name": "STATE — NSITF", "fields": []},
    {"code": "STATE-ITF", "pillar": "State", "name": "STATE — ITF", "fields": []},
    {"code": "STATE-PENSION", "pillar": "State", "name": "STATE — Pension", "fields": []},
    {"code": "STATE-BPR", "pillar": "State", "name": "STATE — BPR (Business Premises Registration)", "fields": []},
    {"code": "STATE-DEV-LEVY", "pillar": "State", "name": "STATE — Development Levy (DL)", "fields": []},
    {"code": "STATE-LAND-USE-CHARGES", "pillar": "State", "name": "STATE — Land Use Charges", "fields": []},
    {"code": "STATE-STAMP-DUTY", "pillar": "State", "name": "STATE — Stamp Duty", "fields": []},
]

# service pillar -> job.category (core-level; used to route the job once a
# service is picked at capture time)
PILLAR_TO_CATEGORY = {
    "CAC": "cac",
    "Immigration": "immigration",
    "CIT": "cit",
    "State": "state",
}

# ---------------------------------------------------------------------------
# TEST LOGINS — one per role, so every view can be checked immediately.
# Same demo password for all seeded accounts; change before real use.
# ---------------------------------------------------------------------------
DEMO_PASSWORD = "RabbiDemo123!"

DEMO_CLIENTS = [
    {"name": "Demo Client Ltd", "rc_number": "RC1234567", "contact_name": "Chidi Okafor",
     "contact_email": "client@rabbiconsult.test", "contact_phone": "+234 801 234 5678"},
    {"name": "Acme Nigeria Ltd", "rc_number": "RC7654321", "contact_name": "Amaka Bello",
     "contact_email": "amaka@acme-ng.example", "contact_phone": "+234 802 987 6543"},
    {"name": "Lagos Trading Co", "rc_number": "RC1122334", "contact_name": "Tunde Alao",
     "contact_email": "tunde@lagostrading.example", "contact_phone": "+234 803 555 1212"},
]

# staff seeded with placeholder client_name for role='client' (resolved to an
# id at seed time against DEMO_CLIENTS above)
DEMO_STAFF = [
    {"name": "Firm Owner", "email": "owner@rabbiconsult.test", "role": "super_admin", "client_name": None},
    {"name": "Adaeze Chukwu", "email": "ec@rabbiconsult.test", "role": "principal", "client_name": None},
    {"name": "Femi Okonkwo", "email": "admin@rabbiconsult.test", "role": "admin", "client_name": None},
    {"name": "Chuka Nwosu", "email": "specialist@rabbiconsult.test", "role": "specialist", "client_name": None},
    {"name": "Amara Bello", "email": "specialist2@rabbiconsult.test", "role": "specialist", "client_name": None},
    {"name": "Chidi Okafor", "email": "client@rabbiconsult.test", "role": "client", "client_name": "Demo Client Ltd"},
]

# demo jobs. sla_offset_days is relative to seed time; None = no SLA date.
# `status` is the status the job is inserted with; a job that should end up
# 'closed' is seeded as 'done' and closed by the script below once it has
# been attached to an invoice (the DB will not allow closed-without-invoice).
DEMO_JOBS = [
    dict(job_id="JOB-2026-0001", client_name="Demo Client Ltd", service_code="IMM-QUOTA",
         title="Quota renewal — 2 expatriate slots", owner_email="specialist@rabbiconsult.test",
         source="team_group_forward", status="in_progress", sla_offset_days=5,
         blocked_by_job_id=None, waiting_on_client=None),
    dict(job_id="JOB-2026-0002", client_name="Demo Client Ltd", service_code="IMM-ECERPAC-PRINCIPAL",
         title="E-CERPAC renewal — MD", owner_email="specialist@rabbiconsult.test",
         source="client_email", status="new", sla_offset_days=-2,
         blocked_by_job_id="JOB-2026-0001", waiting_on_client="Waiting on old CERPAC card scan"),
    dict(job_id="JOB-2026-0003", client_name="Acme Nigeria Ltd", service_code="CIT-VAT-MONTHLY",
         title="August VAT return", owner_email="specialist@rabbiconsult.test",
         source="client_email", status="done", sla_offset_days=1,
         blocked_by_job_id=None, waiting_on_client=None),
    dict(job_id="JOB-2026-0004", client_name="Acme Nigeria Ltd", service_code="CAC-AMENDMENT",
         title="Change of registered address", owner_email="admin@rabbiconsult.test",
         source="team_group_forward", status="new", sla_offset_days=10,
         blocked_by_job_id=None, waiting_on_client="Need the new tenancy agreement"),
    dict(job_id="JOB-2026-0005", client_name="Lagos Trading Co", service_code="STATE-PAYE-REG",
         title="PAYE registration — Lagos", owner_email="specialist@rabbiconsult.test",
         source="client_email", status="done", sla_offset_days=-20,
         blocked_by_job_id=None, waiting_on_client=None, close_after_invoicing=True),
    dict(job_id="JOB-2026-0006", client_name="Lagos Trading Co", service_code="CIT-TCC",
         title="Tax clearance certificate", owner_email="specialist@rabbiconsult.test",
         source="client_email", status="in_progress", sla_offset_days=-1,
         blocked_by_job_id=None, waiting_on_client=None),
    dict(job_id="JOB-2026-0007", client_name="Demo Client Ltd", service_code="STATE-ITF",
         title="Annual ITF contribution filing", owner_email="admin@rabbiconsult.test",
         source="team_group_forward", status="new", sla_offset_days=30,
         blocked_by_job_id=None, waiting_on_client=None),
]

DEMO_INVOICES = [
    # invoice_code, client_name, status, line items (each ties to a 'done' job)
    dict(invoice_code="INV-2026-0001", client_name="Lagos Trading Co", status="paid",
         lines=[{"job_id": "JOB-2026-0005", "description": "PAYE registration — Lagos", "amount": 45000}]),
]
