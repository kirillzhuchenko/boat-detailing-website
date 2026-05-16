"""
database.py — SQLite helper for Bluewater Detailing
----------------------------------------------------
Usage:
    from database import init_db, save_estimate, update_status, get_request

Environment variable:
    DATABASE_URL  path to the .db file  (default: instance/bluewater.db)
"""

import os
import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("DATABASE_URL", "instance/bluewater.db")

MOBILIZATION_FEE = 150.0   # keep in sync with your rates

VALID_STATUSES = {
    "new", "contacted", "scheduled",
    "in_progress", "completed", "invoiced", "cancelled",
}

# ── Columns that may not exist on older databases ─────────────────────────────
# Each tuple: (column_name, full SQL definition)
# init_db() checks these on every startup and adds any that are missing.
_MIGRATIONS = [
    ("deposit_paid",            "INTEGER NOT NULL DEFAULT 0"),
    ("deposit_paid_at",         "DATETIME"),
    ("deposit_payment_method",  "TEXT"),
    ("final_invoice_total",     "REAL"),
    ("amount_paid",             "REAL NOT NULL DEFAULT 0"),
    ("payment_status",          "TEXT NOT NULL DEFAULT 'unpaid'"),
    ("payment_method",          "TEXT"),
    ("payment_date",            "DATETIME"),
    ("payment_notes",           "TEXT"),
]


# ── Connection helper ─────────────────────────────────────────────────────────
@contextmanager
def get_conn():
    """Yield a connection with Row factory and FK enforcement, auto-commit/rollback."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema init ───────────────────────────────────────────────────────────────
def init_db(schema_path: str = "schema.sql") -> None:
    """
    1. Create tables from schema.sql if they don't exist.
    2. Apply any column migrations that are missing from an older DB.
    Safe to call on every startup.
    """
    with open(schema_path, "r") as f:
        sql = f.read()

    with get_conn() as conn:
        # Step 1 — create tables / triggers / indexes
        conn.executescript(sql)

        # Step 2 — add any columns that don't exist yet
        existing = {row[1] for row in conn.execute("PRAGMA table_info(estimate_requests)")}
        for col_name, col_def in _MIGRATIONS:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE estimate_requests ADD COLUMN {col_name} {col_def}"
                )
                logger.info("Migration: added column '%s' to estimate_requests", col_name)

    logger.info("Database initialised at %s", DB_PATH)


# ── Save a new estimate request ───────────────────────────────────────────────
def save_estimate(form: dict, estimated_total: float | None = None) -> int:
    """
    Insert a new estimate_requests row from the POST form dict.
    Returns the new row id.

    The form dict keys match the HTML name= attributes.
    Missing optional fields default to None / 0.
    """

    def txt(key):
        return form.get(key, "").strip() or None

    def flag(key):
        """Checkbox: present in FormData means checked (value doesn't matter)."""
        return 1 if key in form else 0

    def num(key):
        v = form.get(key, "").strip()
        try:
            return float(v) if "." in v else int(v)
        except (ValueError, TypeError):
            return None

    sql = """
    INSERT INTO estimate_requests (
        -- contact
        first_name, last_name, email, phone,

        -- vessel
        vessel_name, vessel_year, vessel_make, vessel_model,
        vessel_type, vessel_type_other,
        hull_material, hull_material_other,
        loa_ft, drive_type, marina, slip,

        -- services
        svc_hull, svc_anode, svc_prop, svc_rudder, svc_other, svc_other_detail,
        frequency, private_dock, fouling_level, fouling_other, exp_service,

        -- anode
        anode_required, anode_material, anode_material_other,
        anode_loc_keel, qty_keel,
        anode_loc_shaft, qty_shaft,
        anode_loc_rudder, qty_rudder,
        anode_loc_other, anode_location_other, qty_other,

        -- prop
        prop_inst_required, prop_type, prop_type_other, prop_diam,

        -- paint
        paint_type, paint_type_other, paint_condition, paint_condition_other,

        -- misc
        notes, referral,

        -- financials
        estimated_total
    ) VALUES (
        :first_name, :last_name, :email, :phone,
        :vessel_name, :vessel_year, :vessel_make, :vessel_model,
        :vessel_type, :vessel_type_other,
        :hull_material, :hull_material_other,
        :loa_ft, :drive_type, :marina, :slip,
        :svc_hull, :svc_anode, :svc_prop, :svc_rudder, :svc_other, :svc_other_detail,
        :frequency, :private_dock, :fouling_level, :fouling_other, :exp_service,
        :anode_required, :anode_material, :anode_material_other,
        :anode_loc_keel, :qty_keel,
        :anode_loc_shaft, :qty_shaft,
        :anode_loc_rudder, :qty_rudder,
        :anode_loc_other, :anode_location_other, :qty_other,
        :prop_inst_required, :prop_type, :prop_type_other, :prop_diam,
        :paint_type, :paint_type_other, :paint_condition, :paint_condition_other,
        :notes, :referral,
        :estimated_total
    )
    """

    params = {
        # contact
        "first_name":           txt("first_name"),
        "last_name":            txt("last_name"),
        "email":                txt("email"),
        "phone":                txt("phone"),

        # vessel
        "vessel_name":          txt("vessel_name"),
        "vessel_year":          txt("vessel_year"),
        "vessel_make":          txt("vessel_make"),
        "vessel_model":         txt("vessel_model"),
        "vessel_type":          txt("vessel_type"),
        "vessel_type_other":    txt("vesselType-other"),
        "hull_material":        txt("hull_material"),
        "hull_material_other":  txt("hullMat-other"),
        "loa_ft":               num("loa_ft"),
        "drive_type":           txt("drive_type"),
        "marina":               txt("marina"),
        "slip":                 txt("slip"),

        # services
        "svc_hull":             flag("svc_hull"),
        "svc_anode":            flag("svc_anode"),
        "svc_prop":             flag("svc_prop"),
        "svc_rudder":           flag("svc_rudder"),
        "svc_other":            flag("svc_other"),
        "svc_other_detail":     txt("svc_other_detail"),
        "frequency":            txt("frequency"),
        "private_dock":         txt("privateDock"),
        "fouling_level":        txt("fouling_level"),
        "fouling_other":        txt("fouling_other"),
        "exp_service":          txt("expService"),

        # anode
        "anode_required":       txt("anode_required"),
        "anode_material":       txt("anode_material"),
        "anode_material_other": txt("anode_material_other"),
        "anode_loc_keel":       flag("anode_loc_keel"),
        "qty_keel":             num("qty_keel"),
        "anode_loc_shaft":      flag("anode_loc_shaft"),
        "qty_shaft":            num("qty_shaft"),
        "anode_loc_rudder":     flag("anode_loc_rudder"),
        "qty_rudder":           num("qty_rudder"),
        "anode_loc_other":      flag("anode_loc_other"),
        "anode_location_other": txt("anode_location_other"),
        "qty_other":            num("qty_other"),

        # prop
        "prop_inst_required":   txt("prop_inst_required"),
        "prop_type":            txt("propType"),
        "prop_type_other":      txt("prop_other"),
        "prop_diam":            num("propDiam"),

        # paint
        "paint_type":           txt("paintType"),
        "paint_type_other":     txt("paint-type-other"),
        "paint_condition":      txt("paintCond"),
        "paint_condition_other": txt("paintCond-other"),

        # misc
        "notes":                txt("notes"),
        "referral":             txt("referral"),

        # financials
        "estimated_total":      estimated_total,
    }

    with get_conn() as conn:
        cur = conn.execute(sql, params)
        new_id = cur.lastrowid
        # Seed the status log with the initial 'new' entry
        conn.execute(
            """INSERT INTO request_status_log (request_id, old_status, new_status, changed_by)
               VALUES (?, NULL, 'new', 'system')""",
            (new_id,),
        )

    logger.info("Saved estimate request id=%s for %s %s", new_id, params["first_name"], params["last_name"])
    return new_id


# ── Status update ─────────────────────────────────────────────────────────────
def update_status(
    request_id: int,
    new_status: str,
    changed_by: str = "staff",
    log_notes: str | None = None,
) -> None:
    """
    Move a request to a new status.
    The DB trigger auto-logs the change, but we add changed_by / log_notes
    by inserting into the log directly so the trigger row gets overwritten.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {VALID_STATUSES}")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM estimate_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        old_status = row["status"]
        conn.execute(
            "UPDATE estimate_requests SET status = ?, status_notes = ? WHERE id = ?",
            (new_status, log_notes, request_id),
        )
        conn.execute(
            """INSERT INTO request_status_log
               (request_id, old_status, new_status, changed_by, log_notes)
               VALUES (?, ?, ?, ?, ?)""",
            (request_id, old_status, new_status, changed_by, log_notes),
        )

    logger.info(
        "Request id=%s status: %s → %s (by %s)", request_id, old_status, new_status, changed_by
    )


# ── Read helpers ──────────────────────────────────────────────────────────────
def get_request(request_id: int) -> sqlite3.Row | None:
    """Return a single estimate_requests row or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM estimate_requests WHERE id = ?", (request_id,)
        ).fetchone()


def get_requests_by_status(status: str) -> list[sqlite3.Row]:
    """Return all requests matching a given status, newest first."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM estimate_requests WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()


def get_status_log(request_id: int) -> list[sqlite3.Row]:
    """Return the full audit trail for a request, oldest first."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM request_status_log WHERE request_id = ? ORDER BY changed_at ASC",
            (request_id,),
        ).fetchall()


# ── Deposit helpers ───────────────────────────────────────────────────────────
def set_deposit_paid(request_id: int, method: str = "unspecified") -> None:
    """Mark the mobilization fee as collected."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT deposit_paid FROM estimate_requests WHERE id = ?",
            (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            """UPDATE estimate_requests
               SET deposit_paid = 1,
                   deposit_paid_at = datetime('now'),
                   deposit_payment_method = ?
               WHERE id = ?""",
            (method, request_id)
        )
    logger.info("Deposit marked paid for request id=%s via %s", request_id, method)


# ── Invoice helpers ───────────────────────────────────────────────────────────
def compute_balance(final_invoice_total: float, amount_paid: float) -> float:
    """
    Balance remaining after deposit credit and any payments made.

    final_invoice_total  — the full job cost (already includes mobilization fee)
    amount_paid          — cash collected toward the final balance (not the deposit)

    The deposit is always credited automatically.
    """
    return max(0.0, final_invoice_total - MOBILIZATION_FEE - amount_paid)


def set_final_invoice_total(request_id: int, total: float) -> None:
    """
    Set the confirmed final invoice total for a completed job.
    Call this before record_payment().
    """
    if total <= 0:
        raise ValueError("Final invoice total must be greater than zero.")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM estimate_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            "UPDATE estimate_requests SET final_invoice_total = ? WHERE id = ?",
            (total, request_id),
        )

    logger.info("Final invoice total set to $%.2f for request id=%s", total, request_id)


def record_payment(
    request_id: int,
    amount: float,
    method: str = "unspecified",
    notes: str | None = None,
) -> dict:
    """
    Record a payment (full or partial) against the final invoice.

    - Adds `amount` to the running amount_paid total.
    - Recomputes the balance and sets payment_status automatically:
        unpaid       → no payment yet
        partial      → payment received but balance remains
        paid_in_full → balance is zero or below

    Returns a dict with the updated figures:
        {
            "request_id":   int,
            "amount_paid":  float,
            "balance_due":  float,
            "status":       str,
        }
    """
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    with get_conn() as conn:
        row = conn.execute(
            """SELECT final_invoice_total, amount_paid
               FROM estimate_requests WHERE id = ?""",
            (request_id,),
        ).fetchone()

        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        if row["final_invoice_total"] is None:
            raise ValueError(
                f"Request id={request_id} has no final_invoice_total set yet. "
                "Set the final invoice total before recording payment."
            )

        new_amount_paid = (row["amount_paid"] or 0.0) + amount
        balance         = compute_balance(row["final_invoice_total"], new_amount_paid)

        if balance <= 0:
            status = "paid_in_full"
        elif new_amount_paid > 0:
            status = "partial"
        else:
            status = "unpaid"

        conn.execute(
            """UPDATE estimate_requests
               SET amount_paid    = ?,
                   payment_status = ?,
                   payment_method = ?,
                   payment_date   = datetime('now'),
                   payment_notes  = ?
               WHERE id = ?""",
            (new_amount_paid, status, method, notes, request_id),
        )

    logger.info(
        "Payment recorded for id=%s: +$%.2f via %s | status=%s | balance=$%.2f",
        request_id, amount, method, status, balance,
    )

    return {
        "request_id":  request_id,
        "amount_paid": new_amount_paid,
        "balance_due": balance,
        "status":      status,
    }


def get_payment_summary(request_id: int) -> dict:
    """
    Return a full payment summary for a request — useful for
    generating invoices or checking status.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT final_invoice_total, deposit_paid,
                      amount_paid, payment_status,
                      payment_method, payment_date, payment_notes
               FROM estimate_requests WHERE id = ?""",
            (request_id,),
        ).fetchone()

    if not row:
        raise LookupError(f"Request id={request_id} not found.")

    fit         = row["final_invoice_total"]
    amount_paid = row["amount_paid"] or 0.0
    balance     = compute_balance(fit, amount_paid) if fit is not None else None

    return {
        "request_id":           request_id,
        "final_invoice_total":  fit,
        "deposit_paid":         bool(row["deposit_paid"]),
        "amount_paid":          amount_paid,
        "balance_due":          balance,
        "payment_status":       row["payment_status"] or "unpaid",
        "payment_method":       row["payment_method"],
        "payment_date":         row["payment_date"],
        "payment_notes":        row["payment_notes"],
    }