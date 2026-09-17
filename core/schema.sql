-- Rabbi Core — shared spine schema
-- Every future module (immigration, CIT, CAC, state matters) is built on these
-- four tables + the service catalogue. Category-specific detail never lives
-- here — it attaches via job_extension. Safe to re-run: everything is
-- CREATE ... IF NOT EXISTS / CREATE OR REPLACE.

-- ---------------------------------------------------------------------------
-- CLIENT
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    rc_number       TEXT,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- STAFF — every human who logs in
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staff (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('principal', 'admin', 'specialist', 'client', 'super_admin')),
    client_id       INTEGER REFERENCES client(id) ON DELETE SET NULL,  -- set when role = 'client'
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Widening migration: the day-to-day operations manager role — near-full
-- operational visibility, final financial/irreversible authority stays
-- with EC (principal) and super_admin. Safe to re-run: this is already the
-- widest version of the constraint, so re-applying it is a no-op even
-- once rows with role = 'manager' exist.
ALTER TABLE staff DROP CONSTRAINT IF EXISTS staff_role_check;
ALTER TABLE staff ADD CONSTRAINT staff_role_check
    CHECK (role IN ('principal', 'admin', 'specialist', 'client', 'super_admin', 'manager'));

-- ---------------------------------------------------------------------------
-- SERVICE CATALOGUE — the locked 43-service catalogue (CAC / Immigration /
-- CIT / State). `fields` describes the Type/Routing/Location/Scheme
-- sub-fields the capture screen must render when this service is picked.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_catalogue (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    pillar          TEXT NOT NULL CHECK (pillar IN ('CAC', 'Immigration', 'CIT', 'State')),
    name            TEXT NOT NULL,
    fields          JSONB NOT NULL DEFAULT '[]'::jsonb,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

-- NULL for a one-off service; 'monthly'/'yearly' for one that recurs on a
-- schedule (e.g. Monthly VAT Returns, Annual Return) — CIT is the first
-- module with recurring obligations, but this lives on the generic service
-- catalogue, not a CIT-specific table, so State's own recurring filings
-- reuse the exact same mechanism (models.create_next_cycle_job) later.
ALTER TABLE service_catalogue ADD COLUMN IF NOT EXISTS recurring_frequency TEXT;

-- Widening migration: admin/super_admin can add a new service that doesn't
-- fit the 4 locked pillars — it goes under a 5th, "Other" pillar/category,
-- selectable in Capture like any other from the moment it's created.
ALTER TABLE service_catalogue DROP CONSTRAINT IF EXISTS service_catalogue_pillar_check;
ALTER TABLE service_catalogue ADD CONSTRAINT service_catalogue_pillar_check
    CHECK (pillar IN ('CAC', 'Immigration', 'CIT', 'State', 'Other'));

-- ---------------------------------------------------------------------------
-- INVOICE — one invoice groups many jobs. Admin creates (submits for
-- approval); principal approves. A job can only close once its invoice is
-- approved (or paid) — see the status-guard trigger below.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice (
    id              SERIAL PRIMARY KEY,
    invoice_code    TEXT NOT NULL UNIQUE,
    client_id       INTEGER NOT NULL REFERENCES client(id),
    status          TEXT NOT NULL DEFAULT 'pending_approval'
                        CHECK (status IN ('pending_approval', 'approved', 'paid')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_at       TIMESTAMPTZ,
    paid_at         TIMESTAMPTZ
);

ALTER TABLE invoice ADD COLUMN IF NOT EXISTS created_by INTEGER REFERENCES staff(id);
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS approved_by INTEGER REFERENCES staff(id);
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS invoice_date DATE NOT NULL DEFAULT CURRENT_DATE;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS rejected_by INTEGER REFERENCES staff(id);
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ;

-- "Sent to client" is tracked separately from status (pending/approved/paid)
-- since it's an orthogonal bookkeeping step, not a lifecycle stage — an
-- approved invoice gets sent, then later paid; sent-ness doesn't gate either.
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS sent_by INTEGER REFERENCES staff(id);

-- Marking an invoice paid requires a payment reference + date (enforced in
-- the UI/model layer, not a NOT NULL here, since these stay NULL for every
-- invoice that isn't yet paid).
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS payment_reference TEXT;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS payment_date DATE;

-- Migration for installs created before the admin-creates / principal-approves
-- flow: map the old draft/issued statuses onto the new ones, and widen to
-- allow 'rejected' (principal sends an invoice back to admin with a reason
-- instead of restructuring it themselves). Drop the old constraint FIRST so
-- the old values are still legal while we remap them. No-op once migrated.
ALTER TABLE invoice DROP CONSTRAINT IF EXISTS invoice_status_check;
UPDATE invoice SET status = 'pending_approval' WHERE status = 'draft';
UPDATE invoice SET status = 'approved' WHERE status = 'issued';
ALTER TABLE invoice ADD CONSTRAINT invoice_status_check
    CHECK (status IN ('pending_approval', 'approved', 'paid', 'rejected'));
ALTER TABLE invoice ALTER COLUMN status SET DEFAULT 'pending_approval';

-- ---------------------------------------------------------------------------
-- INVOICE UNAPPROVAL LOG — EC/super_admin removing an already-granted
-- approval (sends the invoice back to pending_approval). Kept as its own
-- log rather than overloading rejection_reason/rejected_by, which is a
-- distinct admin<->principal workflow step, not this one; a reason is
-- always required so this action is never silent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice_unapproval_log (
    id              SERIAL PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    reason          TEXT NOT NULL,
    actor_id        INTEGER REFERENCES staff(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoice_unapproval_log_invoice ON invoice_unapproval_log(invoice_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- JOB — the central object of the whole suite
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job (
    id                  SERIAL PRIMARY KEY,
    job_id              TEXT NOT NULL UNIQUE,          -- human-readable, e.g. JOB-2026-0014
    client_id           INTEGER REFERENCES client(id),  -- nullable: a dismissed item may not be tied to a client
    category            TEXT NOT NULL CHECK (category IN ('front_office', 'cac', 'immigration', 'cit', 'state')),
    service_type        TEXT REFERENCES service_catalogue(code),  -- null only for a dismissed, non-catalogue item
    title               TEXT NOT NULL,
    description         TEXT,
    owner_id            INTEGER REFERENCES staff(id),
    status              TEXT NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'in_progress', 'blocked', 'done', 'closed', 'dismissed')),
    source              TEXT NOT NULL CHECK (source IN ('client_email', 'team_group_forward')),
    created_by          INTEGER REFERENCES staff(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sla_date            DATE,
    blocked_by          INTEGER REFERENCES job(id),
    invoice_id          INTEGER REFERENCES invoice(id),
    waiting_on_client    TEXT,          -- plain-language note: what we need from the client, and by when
    internal_notes       TEXT,          -- staff-only, never shown on the client view
    dismissed_reason      TEXT,

    -- A job cannot be closed without being invoiced first (belt-and-braces:
    -- also re-checked in the status-guard trigger below on every write path).
    CONSTRAINT job_closed_requires_invoice CHECK (status <> 'closed' OR invoice_id IS NOT NULL)
);

-- Widening migration for installs created before client_id became nullable
-- (dismissed items may not be tied to a client); no-op if already nullable.
ALTER TABLE job ALTER COLUMN client_id DROP NOT NULL;

-- Widening migration: 'other' is the job.category for any service added
-- under the new "Other Services" pillar (see service_catalogue above).
ALTER TABLE job DROP CONSTRAINT IF EXISTS job_category_check;
ALTER TABLE job ADD CONSTRAINT job_category_check
    CHECK (category IN ('front_office', 'cac', 'immigration', 'cit', 'state', 'other'));

-- Widening migration: a CSV-imported job (the bulk upload feature) is
-- neither a client email nor a team-group forward — it's its own source,
-- alongside the two organic capture sources. No-op once migrated.
ALTER TABLE job DROP CONSTRAINT IF EXISTS job_source_check;
ALTER TABLE job ADD CONSTRAINT job_source_check
    CHECK (source IN ('client_email', 'team_group_forward', 'bulk_import'));

-- The reason captured when a job is marked done or blocked (required by the
-- UI for those two transitions) — holds the reason for the CURRENT status,
-- overwritten on the next status change. Not a full history log, by design.
ALTER TABLE job ADD COLUMN IF NOT EXISTS status_reason TEXT;

-- Rabbi invoices up front, before work starts: a job can't move new ->
-- in_progress until it's invoiced and that invoice is approved (see the
-- guard trigger below) — UNLESS a principal explicitly overrides that for
-- this one job, recorded here for audit. The specialist still has to click
-- Start work themselves; the override only lifts the gate.
ALTER TABLE job ADD COLUMN IF NOT EXISTS start_override_by INTEGER REFERENCES staff(id);
ALTER TABLE job ADD COLUMN IF NOT EXISTS start_override_at TIMESTAMPTZ;
ALTER TABLE job ADD COLUMN IF NOT EXISTS start_override_reason TEXT;

-- super_admin-only soft delete: a hidden job stays in the database (audit
-- trail, referential integrity) but every normal read path excludes it —
-- see models._JOB_SELECT — so it's invisible to every role and absent from
-- every count/stat, exactly like a real delete would be, without losing
-- the record. Only the dedicated Hidden Jobs view (super_admin only) reads
-- past this filter.
ALTER TABLE job ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE job ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;
ALTER TABLE job ADD COLUMN IF NOT EXISTS hidden_by INTEGER REFERENCES staff(id);

-- started_at/completed_at: set ONCE, the first time a job crosses into
-- in_progress / done (see models.set_status) — never overwritten by a later
-- transition (a job that's blocked then resumed keeps its original
-- started_at; one marked done then later re-closed keeps its original
-- completed_at). This is the shared timestamp pair behind both the
-- specialist workload report and each job's own elapsed-time badge.
ALTER TABLE job ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE job ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- One-time backfill for jobs that already existed before this feature —
-- status_changed_at is the best available approximation for rows with no
-- real history to draw on. Guarded by "IS NULL" so it only ever fires once
-- per row; every transition from here on sets these for real in set_status.
UPDATE job SET started_at = status_changed_at
    WHERE started_at IS NULL AND status IN ('in_progress', 'blocked', 'done', 'closed');
UPDATE job SET completed_at = status_changed_at
    WHERE completed_at IS NULL AND status IN ('done', 'closed');

CREATE INDEX IF NOT EXISTS idx_job_client_id ON job(client_id);
CREATE INDEX IF NOT EXISTS idx_job_owner_id ON job(owner_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);
CREATE INDEX IF NOT EXISTS idx_job_category ON job(category);
CREATE INDEX IF NOT EXISTS idx_job_invoice_id ON job(invoice_id);
CREATE INDEX IF NOT EXISTS idx_job_blocked_by ON job(blocked_by);
CREATE INDEX IF NOT EXISTS idx_job_hidden ON job(hidden) WHERE hidden = TRUE;

-- ---------------------------------------------------------------------------
-- JOB EXTENSION — category-specific fields attach here, never on job itself.
-- One row per job holding the Type/Routing/Location/Scheme values chosen
-- for that job's service. Future specialist modules add their own detail
-- tables the same way, or reuse this one — the core table never changes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_extension (
    job_id          INTEGER PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- JOB EXPENSE — a simple running list of costs tied to a job. Admin adds
-- them; principal can see them. Not accounting — just a log.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_expense (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    expense_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    created_by      INTEGER REFERENCES staff(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_expense_job_id ON job_expense(job_id);

-- ---------------------------------------------------------------------------
-- JOB COMMENT — a lightweight comment thread on a job. Anyone with access to
-- the job can post; kept simple on purpose (no edits, no threading).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_comment (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    author_id       INTEGER NOT NULL REFERENCES staff(id),
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_comment_job_id ON job_comment(job_id);

-- ---------------------------------------------------------------------------
-- INVOICE LINE — a real invoice's line items. One line per job on the
-- invoice: description + amount, editable by the principal at review time,
-- but the set of jobs (which rows exist here) is admin-only to change —
-- that's what makes "which jobs are on this invoice" an accountable,
-- admin-owned decision rather than something the approver can quietly edit.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice_line (
    id              SERIAL PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    job_id          INTEGER NOT NULL REFERENCES job(id),
    description     TEXT NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount >= 0)
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_invoice_id ON invoice_line(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_line_job_id ON invoice_line(job_id);

-- ---------------------------------------------------------------------------
-- NOTIFICATION — so each role is alerted rather than having to discover
-- things. link_type/link_id point at a job or an invoice (no single FK is
-- possible across two target tables); resolved in the application layer.
-- `kind` lets the SLA sweep dedupe against itself without suppressing other
-- notification kinds for the same job.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification (
    id              SERIAL PRIMARY KEY,
    staff_id        INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    link_type       TEXT NOT NULL CHECK (link_type IN ('job', 'invoice')),
    link_id         INTEGER NOT NULL,
    message         TEXT NOT NULL,
    read            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notification_staff_id ON notification(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_unread ON notification(staff_id) WHERE NOT read;
CREATE INDEX IF NOT EXISTS idx_notification_dedup ON notification(staff_id, kind, link_type, link_id);

-- ---------------------------------------------------------------------------
-- CODE EDIT LOG — the audit trail for manually correcting a job or invoice
-- number (EC/admin/super_admin only). Every job_id/invoice_code is normally
-- auto-generated and never touched again; this exists purely for the rare
-- "this was set wrong, fix it" case, and exists so that correction itself
-- is never silent. entity_type/entity_id mirrors notification's own
-- link_type/link_id pattern — no single FK is possible across two target
-- tables (job and invoice).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS code_edit_log (
    id              SERIAL PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('job', 'invoice')),
    entity_id       INTEGER NOT NULL,
    old_code        TEXT NOT NULL,
    new_code        TEXT NOT NULL,
    changed_by      INTEGER REFERENCES staff(id),
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_code_edit_log_entity ON code_edit_log(entity_type, entity_id, changed_at DESC);

-- ---------------------------------------------------------------------------
-- STATUS GUARD — the rules the brief says the system must enforce, kept in
-- the database so no future module or UI can bypass them:
--   1. A job cannot become 'closed' without an invoice, and that invoice
--      must be approved (or paid) — not just attached.
--   2. A job with an unresolved blocked_by is forced to show 'blocked' and
--      cannot advance to in_progress / done / closed.
--   3. A job cannot move new -> in_progress ("Start work") until it has
--      been invoiced and that invoice is approved (or paid) — unless a
--      principal has recorded a start_override for this job.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION job_status_guard() RETURNS trigger AS $$
DECLARE
    blocker_status TEXT;
    invoice_status_val TEXT;
BEGIN
    IF NEW.blocked_by IS NOT NULL AND NEW.status <> 'dismissed' THEN
        SELECT status INTO blocker_status FROM job WHERE id = NEW.blocked_by;

        IF blocker_status IS NOT NULL AND blocker_status NOT IN ('done', 'closed') THEN
            IF NEW.status IN ('in_progress', 'done', 'closed') THEN
                RAISE EXCEPTION 'Job % is blocked by unresolved job % and cannot move to %',
                    NEW.job_id, NEW.blocked_by, NEW.status;
            END IF;
            NEW.status := 'blocked';
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.status = 'in_progress' AND OLD.status = 'new'
       AND NEW.start_override_by IS NULL THEN
        IF NEW.invoice_id IS NULL THEN
            RAISE EXCEPTION 'Job % must be invoiced before work can start (or a principal override)', NEW.job_id;
        END IF;
        SELECT status INTO invoice_status_val FROM invoice WHERE id = NEW.invoice_id;
        IF invoice_status_val IS NULL OR invoice_status_val NOT IN ('approved', 'paid') THEN
            RAISE EXCEPTION 'Job % cannot start until its invoice is approved (or a principal override)', NEW.job_id;
        END IF;
    END IF;

    IF NEW.status = 'closed' THEN
        IF NEW.invoice_id IS NULL THEN
            RAISE EXCEPTION 'Job % cannot be closed without an invoice', NEW.job_id;
        END IF;

        SELECT status INTO invoice_status_val FROM invoice WHERE id = NEW.invoice_id;
        IF invoice_status_val NOT IN ('approved', 'paid') THEN
            RAISE EXCEPTION 'Job % cannot be closed until its invoice is approved', NEW.job_id;
        END IF;
    END IF;

    NEW.updated_at := now();

    IF TG_OP = 'INSERT' THEN
        NEW.status_changed_at := now();
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_changed_at := now();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_job_status_guard ON job;
CREATE TRIGGER trg_job_status_guard
    BEFORE INSERT OR UPDATE ON job
    FOR EACH ROW EXECUTE FUNCTION job_status_guard();

-- ---------------------------------------------------------------------------
-- DOCUMENT TYPE — the master catalogue of document/permit types a job's
-- checklist can reference. Generic across every module: immigration seeds
-- the first batch of rows here, CIT/State add their own the same way.
-- Never holds the document itself or a sensitive number — just what kind of
-- document it is, and whether that kind expires.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_type (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    has_expiry      BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------------------
-- SERVICE DOCUMENT REQUIREMENT — which document types a service's checklist
-- requires, optionally narrowed to one Type-field variant of that service
-- (e.g. E-CERPAC Renewal adds Old CERPAC Card that Out-of-Country doesn't).
-- variant = '*' means the requirement applies to every variant of the
-- service (or the service has no Type field at all) — a real sentinel
-- rather than NULL, so the (service_code, variant, document_type_code)
-- unique constraint actually prevents duplicate rows across bootstrap runs
-- (Postgres never treats two NULLs as conflicting).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_document_requirement (
    id                  SERIAL PRIMARY KEY,
    service_code        TEXT NOT NULL REFERENCES service_catalogue(code),
    variant             TEXT NOT NULL DEFAULT '*',
    document_type_code  TEXT NOT NULL REFERENCES document_type(code),
    UNIQUE (service_code, variant, document_type_code)
);

-- ---------------------------------------------------------------------------
-- JOB DOCUMENT — one row per required (or added) document on a specific job,
-- materialized from service_document_requirement when the job is created,
-- then ticked off as received. THE DATA BOUNDARY: this table never stores
-- the document file or a sensitive identifier (passport number, DOB, CERPAC
-- number) — only that a document of this type was received, and its expiry
-- date if it has one. The real file/number lives externally, referenced
-- only by the fact that a checklist item is ticked.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_document (
    id                  SERIAL PRIMARY KEY,
    job_id              INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    document_type_code  TEXT NOT NULL REFERENCES document_type(code),
    received            BOOLEAN NOT NULL DEFAULT FALSE,
    received_at         DATE,
    expiry_date         DATE,
    UNIQUE (job_id, document_type_code)
);

CREATE INDEX IF NOT EXISTS idx_job_document_job_id ON job_document(job_id);

-- ---------------------------------------------------------------------------
-- MODULE SPECIALIST — which staff handle a given category's jobs, so Capture
-- can route/pre-select the right owner. A soft nudge, not a hard filter —
-- any active staff can still be picked as owner.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS module_specialist (
    id              SERIAL PRIMARY KEY,
    category        TEXT NOT NULL CHECK (category IN ('cac', 'immigration', 'cit', 'state')),
    staff_id        INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    UNIQUE (category, staff_id)
);

ALTER TABLE module_specialist DROP CONSTRAINT IF EXISTS module_specialist_category_check;
ALTER TABLE module_specialist ADD CONSTRAINT module_specialist_category_check
    CHECK (category IN ('cac', 'immigration', 'cit', 'state', 'other'));
