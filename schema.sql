-- ============================================================
--  Bluewater Detailing — SQLite Schema
--  Tables: estimate_requests, request_status_log
-- ============================================================

PRAGMA journal_mode = WAL;   -- better concurrency for Flask
PRAGMA foreign_keys = ON;


-- ── Main estimate / service request table ────────────────────
CREATE TABLE IF NOT EXISTS estimate_requests (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- ── Timestamps ──────────────────────────────────────────
    created_at              DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at              DATETIME NOT NULL DEFAULT (datetime('now')),

    -- ── Status workflow ─────────────────────────────────────
    -- Allowed: new | contacted | scheduled | in_progress | completed | invoiced | cancelled
    status                  TEXT NOT NULL DEFAULT 'new',
    status_notes            TEXT,           -- internal staff notes on current status

    -- ── Contact info ────────────────────────────────────────
    first_name              TEXT NOT NULL,
    last_name               TEXT NOT NULL,
    email                   TEXT NOT NULL,
    phone                   TEXT NOT NULL,

    -- ── Vessel info ─────────────────────────────────────────
    vessel_name             TEXT NOT NULL,
    vessel_year             TEXT NOT NULL,
    vessel_make             TEXT NOT NULL,
    vessel_model            TEXT NOT NULL,
    vessel_type             TEXT NOT NULL,  -- Yacht | Sailboat | Powerboat | Trawler | Other
    vessel_type_other       TEXT,           -- populated when vessel_type = 'Other'
    hull_material           TEXT NOT NULL,  -- Fiberglass | Aluminum | Steel | Wood | Other
    hull_material_other     TEXT,
    loa_ft                  REAL NOT NULL,  -- Length Overall in feet
    drive_type              TEXT,           -- Inboard/Single | Inboard/Twin | Outboard | IPS | Sail drive | Other
    marina                  TEXT NOT NULL,  -- marina / yard / private dock name
    slip                    TEXT,           -- slip / berth number

    -- ── Services requested ──────────────────────────────────
    svc_hull                INTEGER NOT NULL DEFAULT 0,  -- 1 = requested
    svc_anode               INTEGER NOT NULL DEFAULT 0,
    svc_prop                INTEGER NOT NULL DEFAULT 0,
    svc_rudder              INTEGER NOT NULL DEFAULT 0,
    svc_other               INTEGER NOT NULL DEFAULT 0,
    svc_other_detail        TEXT,
    frequency               TEXT,   -- one-time | Monthly | Bi-monthly | Bi-weekly | Weekly

    -- ── Service conditions ──────────────────────────────────
    private_dock            TEXT NOT NULL,  -- yes | no
    fouling_level           TEXT NOT NULL,  -- light | mod | exces | other
    fouling_other           TEXT,
    exp_service             TEXT NOT NULL,  -- yes | no

    -- ── Anode details ────────────────────────────────────────
    anode_required          TEXT NOT NULL,  -- yes | no (no = customer supplies)
    anode_material          TEXT,           -- zinc | aluminum | magnesium | other
    anode_material_other    TEXT,
    anode_loc_keel          INTEGER NOT NULL DEFAULT 0,
    qty_keel                INTEGER,
    anode_loc_shaft         INTEGER NOT NULL DEFAULT 0,
    qty_shaft               INTEGER,
    anode_loc_rudder        INTEGER NOT NULL DEFAULT 0,
    qty_rudder              INTEGER,
    anode_loc_other         INTEGER NOT NULL DEFAULT 0,
    anode_location_other    TEXT,
    qty_other               INTEGER,

    -- ── Propeller details ────────────────────────────────────
    prop_inst_required      TEXT NOT NULL,  -- yes | no
    prop_type               TEXT,           -- prop_fixed | prop_fold | other
    prop_type_other         TEXT,
    prop_diam               REAL,           -- diameter in inches

    -- ── Paint ───────────────────────────────────────────────
    paint_type              TEXT NOT NULL,  -- antifouling | bariercoating | primer | topcoat | other
    paint_type_other        TEXT,
    paint_condition         TEXT NOT NULL,  -- good | fair | poor | peeling | blistering | other
    paint_condition_other   TEXT,

    -- ── Misc ────────────────────────────────────────────────
    notes                   TEXT,
    referral                TEXT,           -- how they heard about us

    -- ── Financials ──────────────────────────────────────────
    estimated_total         REAL,           -- frontend-calculated estimate in USD
    final_invoice_total     REAL            -- filled in after job is completed
);


-- ── Status audit log ─────────────────────────────────────────
-- Every time status changes, a row is inserted here automatically
-- (or by application code — see database.py).
CREATE TABLE IF NOT EXISTS request_status_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER NOT NULL REFERENCES estimate_requests(id) ON DELETE CASCADE,
    old_status      TEXT,
    new_status      TEXT NOT NULL,
    changed_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    changed_by      TEXT,   -- staff name / system
    log_notes       TEXT    -- optional context for the transition
);


-- ── Indexes ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_requests_status     ON estimate_requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_created    ON estimate_requests(created_at);
CREATE INDEX IF NOT EXISTS idx_requests_email      ON estimate_requests(email);
CREATE INDEX IF NOT EXISTS idx_status_log_request  ON request_status_log(request_id);


-- ── Trigger: keep updated_at fresh on every row update ───────
CREATE TRIGGER IF NOT EXISTS trg_requests_updated_at
AFTER UPDATE ON estimate_requests
FOR EACH ROW
BEGIN
    UPDATE estimate_requests
    SET updated_at = datetime('now')
    WHERE id = OLD.id;
END;


-- ── Trigger: auto-log every status change ────────────────────
CREATE TRIGGER IF NOT EXISTS trg_status_change_log
AFTER UPDATE OF status ON estimate_requests
FOR EACH ROW
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO request_status_log (request_id, old_status, new_status, changed_by)
    VALUES (OLD.id, OLD.status, NEW.status, 'system');
END;