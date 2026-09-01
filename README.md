# Rabbi Core

The foundation of the Rabbi Consult suite, plus its first module: **Front
Office** (intake, register, billing). Later modules — Immigration, CIT,
State Matters — build on this same core.

**Stack:** Streamlit (thin presentation layer) + Neon PostgreSQL (all schema
and business rules) + GitHub (public repo, free-tier deploy).

## The data boundary

This app is a **process tracker, not a document store**. It holds only
low-sensitivity metadata (client, job type, status, owner, dates). It must
never hold passport numbers, dates of birth, permit numbers, or uploaded
documents — see `.gitignore`, which blocks common data-export file types
from ever being committed.

## Project layout

```
core/
  schema.sql       # the shared spine: client, staff, service_catalogue,
                    invoice, job, job_extension + the status-guard trigger
  seed_data.py      # the locked 43-service catalogue + demo logins/jobs
  bootstrap.py       # applies schema.sql and seeds data (idempotent, runs
                       once per app process)
  db.py               # Neon connection pool (via st.secrets, never hardcoded)
  auth.py              # real per-user login (bcrypt password + session)
  models.py            # all queries and business rules — views never write SQL
  constants.py          # roles, statuses, colours, human-readable labels
  ui.py                  # theme injection, badges, page headers
views/
  login.py, capture.py, register.py, billing.py, job_detail.py,
  home_principal.py, home_admin.py, home_specialist.py, home_client.py
assets/style.css      # navy/teal brand theme, Montserrat/Lato, status colours
app.py                  # entrypoint: login gate + role-based navigation
scripts/init_db.py       # manual/CI schema+seed runner (optional — the app
                           # does this automatically on first load)
```

## Local setup

1. Create a Neon Postgres database (or point at any Postgres 14+ instance).
2. Copy the secrets template and fill in your connection string:
   ```
   cp .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```
   ```toml
   [neon]
   url = "postgresql://<user>:<password>@<host>/<database>?sslmode=require"
   ```
   `.streamlit/secrets.toml` is gitignored — it never gets committed.
3. Install dependencies and run:
   ```
   pip install -r requirements.txt
   streamlit run app.py
   ```
   The schema is applied and reference/demo data seeded automatically on
   first load — nothing else to run.

## Deploying (Streamlit Community Cloud, free tier)

1. Push this repo to GitHub (public — required for the free tier).
2. On [share.streamlit.io](https://share.streamlit.io), deploy from the repo,
   entrypoint `app.py`.
3. In the app's **Settings → Secrets**, paste the same `[neon]` block as
   above. Never put the connection string in code.

## Test logins

Seeded automatically — one per role, same demo password for all:

| Role | Email | Password |
|---|---|---|
| Principal | `ec@rabbiconsult.test` | `RabbiDemo123!` |
| Admin | `admin@rabbiconsult.test` | `RabbiDemo123!` |
| Specialist | `specialist@rabbiconsult.test` | `RabbiDemo123!` |
| Client | `client@rabbiconsult.test` | `RabbiDemo123!` |

Change or remove these before using the app with real client data.

## What's built (first slice)

- **The shared core:** `client`, `job`, `invoice`, `staff`, plus
  `service_catalogue` and `job_extension` (category-specific fields attach
  here — the core `job` table never changes for a new module). The `job`
  → `job` `blocked_by` dependency link and the invoice-groups-many-jobs
  relationship are enforced by a database trigger, not just the UI, so no
  future module can bypass them:
  - a job cannot become `closed` without an `invoice_id`;
  - a job with an unresolved `blocked_by` is forced to `blocked` and cannot
    advance to `in_progress` / `done` / `closed`.
- **Roles & real login:** bcrypt-hashed passwords, session held in
  `st.session_state`, four genuinely different views (principal, admin,
  specialist, client).
- **The locked service catalogue:** all 43 services across CAC / Immigration
  / CIT / State, with their Type / Routing / Location / Scheme sub-fields
  rendered dynamically at capture time.
- **Front office module:** capture (two sources + dismiss-with-reason),
  register with a triage view, the `new → in_progress → blocked → done →
  closed` lifecycle, and billing.
- **Every job is one click away:** every job row anywhere in the app (the
  register, needs-attention lists, the principal's clickable status counts)
  opens the same job detail page — the one place a job is viewed and acted
  on. Marking a job `done` or `blocked` requires a reason, captured in
  `job.status_reason`. Admin logs per-job expenses (`job_expense`); the
  principal can see them. Anyone with access to a job can leave a comment
  (`job_comment`) — newest first, lightweight, no editing or threading.
- **A real invoice, not a toggle:** admin builds it from a job's detail page
  (or from Billing) — client is fixed from the job, one or more done jobs
  become line items (`invoice_line`: description + amount each), plus an
  invoice code and date. Submitting sends it to the principal as
  `pending_approval`, rendered as an actual document (bill-to, line items,
  total) rather than a form. The principal can edit descriptions/amounts and
  approve, or reject it back to admin with a required reason — only admin
  can change *which jobs* are on an invoice (add/remove), so a rejection is
  how the principal disputes composition rather than quietly fixing it
  themselves. A rejected invoice can be revised (jobs, lines, code, date)
  and resubmitted. A job can't move to `closed` until its invoice is
  `approved` or `paid` — enforced by the same trigger that guards
  `blocked_by`. Every invoice is clickable everywhere it's referenced
  (register, job detail, billing) and opens the same document page.

## A judgement call worth flagging

The brief's `job.category` enum is `front_office | immigration | cit |
state`, but the locked catalogue has four pillars including **CAC**. Since
every CAC service needs a category too, `category` is auto-derived from the
chosen service's pillar (`cac`, `immigration`, `cit`, `state`); `front_office`
is used for the small number of jobs with no catalogue service — a dismissed
item, for instance. Flagging this in case a different mapping is intended
once the CAC module is planned.

## Not built yet (by design)

Specialist modules (Immigration, CIT, State, CAC) and any sensitive personal
data (passport numbers, uploaded documents) — per the brief, those come
later, on this same core, and the modules that need sensitive data move to a
private host at that point.
