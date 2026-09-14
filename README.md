# Rabbi Core

The foundation of the Rabbi Consult suite — **Front Office** (intake,
register, billing) — plus its first specialist module, **Immigration**.
CIT and State Matters build on the same core the same way.

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
                    invoice, job, job_extension + the status-guard trigger,
                    plus the generic document_type / service_document_
                    requirement / job_document / module_specialist tables
  seed_data.py      # the locked 43-service catalogue + demo logins/jobs
  seed_documents.py  # document-type catalogue + per-service checklist
                       requirements (Immigration's 10 services)
  bootstrap.py       # applies schema.sql and seeds data (idempotent, runs
                       once per app process)
  db.py               # Neon connection pool (via st.secrets, never hardcoded)
  auth.py              # real per-user login (bcrypt password + session)
  models.py            # all queries and GENERIC business rules — the
                         document checklist, expiry tracking and module-
                         specialist mechanisms every module shares; views
                         never write SQL
  immigration.py        # the ONE immigration-specific rule: the quota →
                          CERPAC validity gate. Future CIT/State modules add
                          their own cit.py / state.py alongside this, never
                          touching the generic core/models.py mechanisms
  constants.py          # roles, statuses, colours, human-readable labels
  ui.py                  # theme injection, badges, page headers
  pdf.py                  # renders an approved invoice as a downloadable PDF
views/
  login.py, capture.py, register.py, billing.py, job_detail.py,
  immigration.py (module dashboard), home_principal.py, home_admin.py,
  home_specialist.py, home_client.py
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
  (register, job detail, billing) and opens the same document page. Billing's
  admin view is a ready-to-invoice register (every done, unbilled job across
  every client) rather than a pick-a-client-first dropdown — Create Invoice
  is right there on each row, same as on a job's own detail page.
- **Notifications:** a bell in the sidebar (`notification` table) alerts each
  role to what needs them instead of making them go find it — a specialist
  when a job is assigned to them, the owner and principal when a job is
  marked done or blocked (with the reason), the principal when an invoice is
  submitted, the submitting admin when it's approved or rejected, and job
  owners when their SLA is due soon or past (a single set-based sweep,
  cached 5 minutes, dedupes against itself so it never re-notifies for a
  condition it already flagged). Every notification is clickable and jumps
  straight to the job or invoice it's about.
- **Cross-module dependency alerts:** setting `blocked_by` notifies the
  *blocking* job's owner immediately, even across specialists/modules — "your
  job is holding up someone else's." A job that's blocking another shows a
  red alert on its own detail page and is forced to the top of every list
  (`blocking_count`, a correlated subquery on every job read, takes priority
  in `compute_risk()`). When the blocking job is marked done or closed, every
  job waiting on it automatically moves back to `in_progress` and its owner
  is notified it can proceed — no manual re-save needed.

## User management (super_admin only)

The firm owner creates every other staff login from inside the app — no
direct database access needed. A **Users** page (visible only to
`super_admin`) lets them:

- **Create a user** — full name, role (principal / admin / specialist), and
  for a specialist, their speciality/module (immigration / CIT / state,
  reusing the same `module_specialist` routing table the Immigration module
  introduced — so a specialist created here immediately floats to the top
  of the Owner list in Capture for their module's services).
- **Get credentials back immediately** — the system generates a username
  (`firstname.lastname@rabbicore.local`, de-duplicated with a numeric suffix
  if that name is already taken — nothing here is a real mailbox, it's a
  login identifier the way `email` has always doubled as one) and a
  12-character password drawn from an alphabet with the visually ambiguous
  characters removed (no `l`/`1`/`I`, no `O`/`0`) so it's easy to read back
  and type correctly. Both are shown in their own `st.code` block — a
  monospace, one-click-to-copy box — right after creation, with a clear
  "won't be shown again" warning; only the bcrypt hash is ever written to
  the database, exactly like every other staff password.
- **See and manage every user** — name, role, speciality, active/inactive,
  with Deactivate/Reactivate and **Reset password** per row. Deactivating
  never deletes the row: `verify_login()` already refuses inactive
  accounts, so it's an instant, reversible access cut — their past jobs,
  comments and invoices keep their name attached exactly as before.
  Reset password issues a brand-new generated password on the spot (same
  one-time `st.code` display as creation; the username is unchanged) —
  this is deliberately *not* a persistent register of every password ever
  issued. Passwords are hashed one-way (bcrypt) specifically so they can
  never be recovered later, by anyone, including the app itself; "I need
  to hand out working credentials again" is answered by issuing a fresh
  password, not by keeping old ones readable somewhere.
- A user created this way can log in immediately with the generated
  username/password, straight into their correct role (and, for a
  specialist, their speciality) view — same `verify_login()` path every
  other account uses, no special-casing.

**Personalisation.** Every screen already greeted people by their first
name (`home_*.py`'s `"Good to see you, {first name}"`, `home_client.py`'s
`"Welcome, {first name}"`) — the sidebar identity block now does the same
explicitly: the person's full name stays the bold, primary line, and the
smaller caption under it is their role *plus* their speciality when they
have one (e.g. "Specialist — Immigration"), reusing the same
`module_specialist` lookup the Users list uses. Two demo accounts were
also renamed for this to actually read as personalisation in a demo —
`"EC (Principal)"` and `"Ops Coordinator"` were job-title-shaped strings
sitting in the `name` column, not real names, so their own greetings read
like a role label no matter what the code did with them. They're now
Adaeze Chukwu (principal) and Femi Okonkwo (admin); the emails/passwords
those two log in with are unchanged.

**User list now shows Created.** Each row in the Users list shows the
date and time that login was created (`staff.created_at`, already there
since the very first schema — this only surfaces it in the UI), formatted
the same way comment timestamps already are (`%d %b %Y, %H:%M`).

## Hide / delete jobs (super_admin only)

Clearing out demo/test jobs without losing the register's integrity, or
genuinely destroying a record when that's really what's wanted — two
separate, deliberately distinct actions:

- **Hide** is soft and reversible. A hidden job (`job.hidden`, plus
  `hidden_at`/`hidden_by` for an audit trail) stays in the database, but a
  single change makes it disappear everywhere at once: `_JOB_SELECT` — the
  one query every normal read path in `models.py` builds on (`get_job`,
  `list_jobs`, invoicing lookups, duplicate detection, dependency lookups,
  all of it) — now carries `WHERE j.hidden = FALSE` as its base filter, so
  every list, count, dashboard stat, and detail page for every role
  (principal, admin, specialist, client) excludes it automatically, with
  no per-view filtering to remember or forget. The `blocked_by` join and
  the `blocking_count` subquery are guarded the same way, so a hidden
  job's `job_id`/status never leaks into another job's "Depends on" line
  or blocking count either — and `is_actually_blocked()` now treats a
  hidden blocker as resolved (checking the *joined* `blocked_by_job_code`
  rather than the raw FK), so a job that depended on one isn't left
  permanently stuck.
- **Bulk hide.** The register's "All jobs" table, for `super_admin` only,
  gets a checkbox per row plus "Select all" and a "Hide selected (N)"
  button — clearing a batch of test jobs is one click, not one job at a
  time. A single job can also be hidden from its own detail page (a
  "Danger zone" section, `super_admin` only).
- **Hidden Jobs** is a new `super_admin`-only nav page listing exactly
  what's hidden (`models.list_hidden_jobs()`, the one place that queries
  past the `_JOB_SELECT` filter) with an **Unhide** button per row.
- **Delete** is real and permanent, and kept deliberately separate from
  hide — its own section, its own explicit "I understand this cannot be
  undone" confirmation checkbox before the button is even clickable, both
  on a job's own detail page and from Hidden Jobs. It refuses outright if
  the job has ever been on an invoice (`invoice_line` references it) —
  that's real accounting history, not something a cleanup action should
  silently erase; hide it instead, or take it off the invoice first. Any
  other job that depended on the deleted one is unblocked the same way it
  would be if that job had been marked done (not just left with a dangling
  reference and no way to self-heal) — job_extension/expense/comment/
  document rows all cascade automatically.

**Judgement call:** an already-issued invoice's own line items and total
are untouched by hiding the job behind them — those are a historical
financial document, not a live stat, so hiding doesn't rewrite them
(mirrors why delete refuses an invoiced job outright, just non-destructively).

## The Immigration module — the first specialist module

This extends the shared job spine — it does not replace it. An immigration
job is a `job` with `category = 'immigration'`, plus specialist detail
attached via extension mechanisms; capture, register, invoicing,
notifications, comments and expenses all keep working unchanged. The
pattern here is deliberately generic so CIT and State can reuse it without
rewriting it:

- **Document checklist (generic, reusable).** `document_type` is the master
  catalogue of document/permit kinds; `service_document_requirement` says
  which ones each service's checklist needs (optionally narrowed to one
  Type-field variant, e.g. E-CERPAC Renewal adds "Old CERPAC Card" that
  Out-of-Country doesn't). `job_document` is materialized onto a job at
  creation time from its service + variant, then ticked off as received —
  no per-service Python code, just data. **Readiness** (`received/required`)
  shows as a badge on the job page; a service with no fixed checklist (NIS
  Inspection) reads `0/0` and says so, never a misleading 100%.
- **Expiry tracking (generic, reusable).** Any received `job_document` with
  an expiry date is picked up by `models.list_upcoming_expiries()`, bucketed
  `expired` (< today) / `due soon` (≤ 30 days) / `approaching` (≤ 90 days) —
  the Immigration dashboard's "Upcoming expiries" panel is this query
  filtered to `category = 'immigration'`; CIT/State get the same panel for
  free by filtering the same function to their own category.
- **The quota → CERPAC gate (the one immigration-specific rule).** A CERPAC
  (Principal) job can be linked to the client's Quota job; if that quota's
  current approval (its own `QUOTA-APPROVAL` checklist item) has under 6
  months' validity remaining, the CERPAC job is forced `blocked` and
  `blocked_by` points at the quota job — reusing the existing dependency
  column and "Depends on" UI. This is date-driven, not status-driven, so it
  deliberately does **not** reuse the generic `job_status_guard` trigger's
  blocker-done/closed resolution (a quota job is usually already `done` —
  that's not what makes it valid). `core/immigration.py` owns the whole
  lifecycle: it sets/clears `blocked_by` + `status` directly, and
  `models._resync_stale_blocked()` is taught to leave alone any job whose
  extension marks its `blocked_by` as this kind of link, so the generic
  self-heal and the module's own gate never fight over the same job. The
  gate re-checks immediately whenever the quota's checklist changes, and
  on a 5-minute sweep (mirroring the SLA sweep) as a safety net for pure
  time-based drift; when it resolves, the CERPAC job auto-unblocks and its
  owner is notified (reusing the existing notification mechanism), and the
  job detail page removes the manual "Resume" button while the gate is
  active so it can't be bypassed. This is the module's whole reason to
  exist — get an expatriate's CERPAC renewed before the underlying quota
  lapses.
- **Specialist routing (`module_specialist`).** A category → staff mapping
  managed from the Immigration dashboard. Purely a soft nudge: in Capture,
  once an immigration service is picked, assigned staff float to the top of
  the Owner dropdown and the first one is pre-selected — nobody is filtered
  out, so it can't cost a specialist a fast capture (front-office brief:
  "keep it fast").
- **The data boundary, unchanged.** `job_document` stores only a document
  type code, a received flag/date, and an expiry date — never the file, a
  passport number, a date of birth, or the CERPAC number itself. The actual
  sensitive record lives externally; the app only tracks that it was
  received and when it expires.

**Judgement calls worth flagging:**
- The 6-month minimum is approximated as 182 days (`QUOTA_MIN_VALIDITY_DAYS`
  in `core/immigration.py`) rather than calendar months, to avoid adding a
  date-math dependency for one comparison.
- `QUOTA-APPROVAL` (the quota's current approval document) is on every
  Quota variant's checklist (Grant/Addition/Renewal), not just Renewal's —
  it doubles as the input for a renewal *and* the record of the newly
  granted approval's validity for Grant/Addition, which is what the gate
  reads for any subsequently linked CERPAC job.
- The quota link is scoped to E-CERPAC (Principal) only, per the brief —
  the dependant e-CERPACs (spouse/child) don't carry their own quota
  position.
- Expiry urgency thresholds (due ≤ 30 days, approaching ≤ 90 days) aren't
  specified anywhere in the source material; chosen as reasonable defaults.

## Interconnection audit (batch 2)

Checked whether any view could show stale or diverging data. Findings:
- Zero raw SQL outside `models.py` — every view reads through the same
  functions, and job reads all go through one shared `_JOB_SELECT`. There's
  no second, parallel path that could drift from it.
- `@st.cache_resource` is used in exactly two places — the Neon connection
  pool object and the one-time (or 5-minutes-TTL, for the SLA sweep) setup
  functions — never on query *results*. No `@st.cache_data` anywhere.
  Every `models.*` call hits Postgres fresh on every rerun.
- The only session-state a browser tab holds onto is its own identity and
  navigation pointers (which job/invoice is open) — never a copy of job or
  invoice data — so there's nothing to go stale between reruns.
- The one honest limitation: this is a request-driven app, not push/realtime.
  If User A has a job open and User B changes it elsewhere, A's page won't
  update until A next interacts or reloads — normal for a UI without
  websocket push, and out of scope here since re-adding polling would cut
  against "don't degrade performance." Every actual *read* is always live.

## Fixes + batch 3 (clarity, UX, and a few real bugs)

Six things reported directly against the deployed app, then six quick
clarity/UX items:

- **Billing was hiding newly-logged jobs.** It only ever queried
  `status = 'done'` jobs, so anything still `new`/`in_progress` — like the
  two jobs the user had just logged — was invisible there. Billing's
  register now shows **every unbilled job, any status**, with a Status
  column; **Create invoice** only lights up once a job is actually `done`
  (the "no bill, no close" rule itself is untouched — this only fixes what's
  *visible*, not what's *invoiceable*).
- **Invoice codes are auto-generated** (`INV-{year}-{id:04d}`, same
  insert-then-update-by-id trick already used for job codes) — the manual
  "Invoice code \*" field is gone. Revising a rejected invoice keeps its
  existing code; it's still the same invoice, just corrected.
- **Accounting-format numbers** — audited every amount on screen (line
  items, totals, expense logs); all of them were already rendered as
  `₦{amount:,.2f}` (comma thousands, 2 decimals). No changes needed — this
  was a "make sure," not a bug.
- **Short job IDs in every list/table** — `#0008` instead of
  `JOB-2026-0008` in the register, billing, and invoice line items, so
  scanning a list of jobs doesn't mean reading the same 9-character prefix
  over and over. The job's own detail page still shows the full code.
- **Persistent login across a page reload.** Streamlit's `session_state`
  doesn't survive a hard reload, so login now also drops a signed, 12-hour
  token into the URL (`?s=...`) — HMAC-SHA256, stdlib only, keyed off a
  hash of the Neon connection string already in secrets (no new secret to
  configure). A reload with no live session re-derives it from that token
  instead of bouncing to the login screen; logging out clears it.
  **Trade-off worth knowing:** anyone who gets hold of that URL (a copied
  link, a shared screenshot with the address bar visible) can use it to
  sign in as that user until the token expires. Fine for an internal tool;
  worth knowing if the URL is ever shared outside the firm.
- **Specialists can now open an invoice covering a job they own** —
  read-only. The access gate on the invoice document page and on a job's
  own "Invoice" section now also admits a specialist who owns at least one
  job on that invoice; every edit/approve/reject/revise/mark-paid control
  was already gated to admin/principal specifically, so nothing else had to
  change for this to be safely read-only.

**Batch 3 — clarity and UX:**

1. **Colour legend** — red/amber/green/grey now come with a small key
   ("Expired / at risk", "Due / needs attention", "Done / ready",
   "Monitoring") reusing the exact `RISK_LABELS` strings everywhere the
   colours already lived, so there's one definition, not a re-authored copy.
2. **Log out button was invisible** (white text on white). Root cause:
   `requirements.txt` had `streamlit>=1.38` (unbounded), so the deployed
   Cloud version could resolve differently from whatever was tested
   locally, and Streamlit has changed the DOM markup for a "secondary"
   button across versions (`kind="secondary"` vs.
   `data-testid="stBaseButton-secondary"`). Fixed defensively (CSS now
   targets both, with a higher-specificity sidebar-scoped override) *and*
   preventively (`streamlit==1.62.0` pinned exactly, matching what's
   actually tested against).
3. **"Invoiced" is now a filterable status** in the register — it isn't a
   real `job.status` value (touching the enum/trigger felt riskier than the
   ask needed), so it's a derived filter: `done` AND already has an
   `invoice_id`. All the real statuses (new, in progress, blocked, done,
   closed) are now always offered as filter options too, even when no job
   currently has one, rather than only appearing once a job exists with
   that status.
4. **Specialist screen reordered** — "Needs attention" (red/amber risk)
   first, then "In progress," with everything else — including every
   done/closed job — behind a collapsed "All my jobs" expander that reuses
   the full filterable register. Finished work no longer eats the top of
   the screen.
5. **"+ Add new client" inline in Capture** — picking it reveals name/
   contact fields right there; submitting the job creates the client first
   (`models.create_client`), so a request for a brand-new client never
   requires leaving the capture screen.
6. **Duplicate-job warning.** Selecting a client + service that already has
   a live (not dismissed/closed) job for that pairing shows "This job may
   already exist," with the existing job's short ID, owner, and date;
   submitting requires checking "Log anyway." Removing a genuine duplicate
   reuses the same dismiss-with-reason mechanism as any other dismissal
   (`status = 'dismissed'`, admin-only, via a "Mark as duplicate" action on
   the job's own page) rather than a hard delete — the record and its audit
   trail stay intact.

## Batch 4 — invoice-before-work, role/blocking fixes, expenses, invoice lifecycle, sidebar/reload

The biggest change in this batch is a genuine flow reversal: **Rabbi invoices
up front, before work starts**, not after. Everything else in this batch is
either a role/permission tightening, a real bug fix, or a UI addition.

**A. Invoice-before-work.** The sequence is now: job logged → admin creates
the invoice (available the moment a job is logged, any status — not just
`done` anymore) → principal approves → specialist can start work. This is
enforced in the same place every other hard rule lives — the
`job_status_guard` trigger — not just the UI: a `new → in_progress` update
now fails at the database unless the job's invoice exists and is `approved`
or `paid`. A principal can override this per-job (`job.start_override_by/
at/reason`, an audited exception, not a rule change) when work genuinely
needs to start before approval; the specialist still clicks **Start work**
themselves — the override only lifts the gate, it doesn't start the job.
**Judgement call:** closing (`done → closed`) still requires the invoice to
be approved/paid exactly as before — this batch only added a gate at the
*start* of the job, it didn't touch the existing gate at the end.

**B. Status/role rules.**
- Confirmed unchanged: a job stays `new` until its specialist clicks **Start
  work** themselves.
- **Admin can no longer mark a job done or blocked** — only the specialist
  who owns it, or the principal, can. Admin still sees the Status section
  (so they can see what's blocking a job) but the Mark done/blocked forms
  are gated to `ROLE_SPECIALIST`/`ROLE_PRINCIPAL`; a caption explains why
  they're missing rather than the section silently vanishing.
- Confirmed unchanged: marking done or blocked still requires a reason.

**C. Blocking bugs.**
- **Real bug, found and fixed:** the "Resume — in progress" button only
  ever rendered when a blocked job had *no* `blocked_by` set
  (`elif status == STATUS_BLOCKED and not job["blocked_by"]`) — so a job
  that was blocked *by a dependency* had no way back to in-progress from
  its own page even once that dependency resolved, if the automatic
  unblock (`_unblock_dependents`, wired since batch 2) ever missed it for
  any reason. Fixed the dead branch (any non-actually-blocked `blocked`
  job can resume), and — belt-and-braces — added a self-healing sweep
  (`_resync_stale_blocked()`) that runs on every job read: any job still
  reading `blocked` whose blocker has since resolved is corrected back to
  `in_progress` right there, rather than trusting a single write path to
  have always caught it.
- **Client-side blocking:** audited `waiting_on_client` — it's a
  free-text note field only (capture, display, edit); nothing in the
  status-transition code has ever checked or gated on it. Nothing to fix
  there. The practical way a job *could* get stuck on something
  client-side was really the dependency-resolution bug above (a job
  logged purely to track "waiting on the client" that never gets marked
  done would otherwise permanently block whatever depends on it) — the
  fix in C covers this: the moment any blocking job resolves, everything
  waiting on it is freed automatically, on every read, not just once.

**D. Expenses.** The per-job expense log is now a real table (Description /
Amount / Date / Added by columns) instead of a line-per-entry text block.
Still admin-only to add (unchanged); the specialist who owns the job can
now see it (read-only, no add form) alongside admin/principal, so more than
one person can catch a wrong number, per the ask.

**E. Comments.** Author name + timestamp were already shown on every
comment (batch 1) — confirmed, no change needed. New: posting a comment now
notifies the job's owner, every admin, and every principal (excluding
whoever just posted it) — so a note from any one role reaches the other
two, not just whoever happens to check the job next.

**F. Invoice → payment lifecycle.** Three new invoice-detail actions, all
admin-only:
- **Download PDF** — a real downloadable document (fpdf2, pure-python, no
  system dependency so it works the same on Streamlit Cloud), available
  once an invoice is approved or paid.
- **Mark sent to client** — records who and when (`invoice.sent_at/by`);
  shown once approved. Kept as its own field rather than a new `status`
  value, since "sent" is orthogonal to the approve → pay pipeline, not a
  stage in it.
- **Mark as paid** — now a small form requiring a payment reference *and*
  a payment date; both are required, enforced in `models.mark_invoice_paid`
  (raises rather than silently accepting an empty reference), not just a
  disabled button.

**G. UI/UX.**
- **Sidebar contrast, root-caused this time.** `.streamlit/config.toml` had
  `secondaryBackgroundColor = "#FFFFFF"` — Streamlit's own theme, which
  governs the sidebar's default background before any custom CSS runs.
  The CSS override (`[data-testid="stSidebar"] { background-color: ... }`)
  had no `!important`, so on whichever exact Streamlit build is actually
  serving the deployed app, the theme's white could still win the
  specificity/order tie — forcing near-white sidebar *text* (which did
  have `!important`) onto a background that hadn't actually turned navy.
  Fixed at the root with Streamlit's own `[theme.sidebar]` config block
  (a stable public API since 1.35, not an internal DOM testid that can
  drift release to release) plus a hardened `!important` CSS layer as a
  second line of defence.
- **Page/section survives a reload.** The same URL that already carries the
  signed login token (batch 3) now also carries the current page and, if
  one is open, the job or invoice detail view (`?p=...&j=...&i=...`) —
  kept in sync on every navigation and restored once per session on load,
  so a hard reload lands back where the user was instead of bouncing to
  Home.

**Testing note:** the Playwright test harness for this batch hit a genuine
tooling pitfall worth recording — `wait_until="networkidle"` is unreliable
against a Streamlit app specifically because Streamlit holds a persistent
WebSocket connection open, which can prevent the network from ever reading
as "idle." Switched the test helpers to `wait_until="load"` plus a fixed
settle delay; this is a test-infrastructure fix only, no application code
was affected.

## A judgement call worth flagging

The brief's `job.category` enum is `front_office | immigration | cit |
state`, but the locked catalogue has four pillars including **CAC**. Since
every CAC service needs a category too, `category` is auto-derived from the
chosen service's pillar (`cac`, `immigration`, `cit`, `state`); `front_office`
is used for the small number of jobs with no catalogue service — a dismissed
item, for instance. Flagging this in case a different mapping is intended
once the CAC module is planned.

## Not built yet (by design)

CIT, State Matters and CAC specialist modules — built on the same generic
document-checklist/expiry/module-specialist mechanisms Immigration just
established, plus whatever module-specific rule each one needs (its own
`core/cit.py` / `core/state.py`, following `core/immigration.py`'s pattern).
Any sensitive personal data (passport numbers, CERPAC numbers, uploaded
documents) stays out of this app by design — see The data boundary above —
and any module that needs to hold that data directly moves to a private
host at that point.
