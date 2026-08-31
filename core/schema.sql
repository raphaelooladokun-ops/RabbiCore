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
    role            TEXT NOT NULL CHECK (role IN ('principal', 'admin', 'specialist', 'client')),
    client_id       INTEGER REFERENCES client(id) ON DELETE SET NULL,  -- set when role = 'client'
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

-- ---------------------------------------------------------------------------
-- INVOICE — one invoice groups many jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice (
    id              SERIAL PRIMARY KEY,
    invoice_code    TEXT NOT NULL UNIQUE,
    client_id       INTEGER NOT NULL REFERENCES client(id),
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'issued', 'paid')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_at       TIMESTAMPTZ,
    paid_at         TIMESTAMPTZ
);

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

CREATE INDEX IF NOT EXISTS idx_job_client_id ON job(client_id);
CREATE INDEX IF NOT EXISTS idx_job_owner_id ON job(owner_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);
CREATE INDEX IF NOT EXISTS idx_job_category ON job(category);
CREATE INDEX IF NOT EXISTS idx_job_invoice_id ON job(invoice_id);
CREATE INDEX IF NOT EXISTS idx_job_blocked_by ON job(blocked_by);

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
-- STATUS GUARD — the rules the brief says the system must enforce, kept in
-- the database so no future module or UI can bypass them:
--   1. A job cannot become 'closed' without an invoice.
--   2. A job with an unresolved blocked_by is forced to show 'blocked' and
--      cannot advance to in_progress / done / closed.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION job_status_guard() RETURNS trigger AS $$
DECLARE
    blocker_status TEXT;
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

    IF NEW.status = 'closed' AND NEW.invoice_id IS NULL THEN
        RAISE EXCEPTION 'Job % cannot be closed without an invoice', NEW.job_id;
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
