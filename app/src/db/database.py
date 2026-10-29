"""
database.py — PostgreSQL helper for Bluewater Detailing
--------------------------------------------------------
Usage:
    from database import init_db, save_estimate, update_status, get_request

Requires:
    pip install "psycopg[binary]"

Environment variable:
    DATABASE_URL  PostgreSQL connection string
                  (default: postgresql://localhost/bluewater)
                  e.g.  postgresql://user:password@host:5432/bluewater
"""

import os
import logging
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

import psycopg
from psycopg import sql
from psycopg.adapt import Loader
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DB_URL           = os.environ.get("DATABASE_URL", )
MOBILIZATION_FEE = 100.0    # keep in sync with app.py
DEFAULT_TAX_RATE = 10.4     # fallback when city is not in the lookup table

VALID_STATUSES = {
    "new", "contacted", "scheduled",
    "in_progress", "completed", "invoiced", "cancelled",
}

# Arbitrary constant used so only one process runs init_db at a time
# (e.g. several gunicorn workers starting together).
_INIT_LOCK_ID = 727274

# ── Columns that may not exist on older databases ─────────────────────────────
_MIGRATIONS = [
    ("deposit_paid",            "INTEGER NOT NULL DEFAULT 0"),
    ("deposit_paid_at",         "TIMESTAMPTZ"),
    ("deposit_payment_method",  "TEXT"),
    ("final_invoice_total",     "NUMERIC(12,2)"),
    ("amount_paid",             "NUMERIC(12,2) NOT NULL DEFAULT 0"),
    ("payment_status",          "TEXT NOT NULL DEFAULT 'unpaid'"),
    ("payment_method",          "TEXT"),
    ("payment_date",            "TIMESTAMPTZ"),
    ("payment_notes",           "TEXT"),
    ("stripe_session_id",       "TEXT"),
    ("stripe_session_url",      "TEXT"),
    # ── new columns ──
    ("city",                    "TEXT"),
    ("anode_unit_price",        "NUMERIC(12,2)"),
    ("tax_rate",                "NUMERIC(5,2) NOT NULL DEFAULT 10.4"),
    ("invoice_notes",           "TEXT"),
]

# Get the directory where database.py actually lives
current_dir = os.path.dirname(os.path.abspath(__file__))
# Construct an absolute path to schema.sql in the same folder
default_schema_path = os.path.join(current_dir, "schema.sql")

# ── NUMERIC → float ───────────────────────────────────────────────────────────
# psycopg returns NUMERIC as decimal.Decimal, which can't be mixed with floats
# (compute_balance etc.) or JSON-serialised by Flask. Load them as float instead,
# matching the old SQLite REAL behaviour.
class _NumericAsFloat(Loader):
    def load(self, data):
        return float(bytes(data))


psycopg.adapters.register_loader("numeric", _NumericAsFloat)


# ── Connection helper ─────────────────────────────────────────────────────────
@contextmanager
def get_conn():
    """Yield a connection with dict rows, auto-commit/rollback."""
    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema init ───────────────────────────────────────────────────────────────
def init_db(schema_path: str = default_schema_path) -> None:
    """
    1. Create tables from the schema file if they don't exist.
    2. Apply any column migrations that are missing from an older DB.
    Safe to call on every startup.
    """
    with open(schema_path, "r") as f:
        schema_sql = f.read()

    with get_conn() as conn:
        # Serialise concurrent startups; released automatically at commit.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_INIT_LOCK_ID,))
        conn.execute(schema_sql)

        existing = {
            row["column_name"]
            for row in conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name = 'estimate_requests'"""
            )
        }
        for col_name, col_def in _MIGRATIONS:
            if col_name not in existing:
                # Safe: both values come from the hardcoded _MIGRATIONS list (never user
                # input), and the column name is quoted with sql.Identifier.
                stmt = sql.SQL(
                    "ALTER TABLE estimate_requests ADD COLUMN IF NOT EXISTS {} {}"
                ).format(sql.Identifier(col_name), sql.SQL(col_def))
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                conn.execute(stmt)  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                logger.info("Migration: added column '%s' to estimate_requests", col_name)

    logger.info("Database initialised")

init_db()
# ── Save a new estimate request ───────────────────────────────────────────────
def save_estimate(
    form: dict,
    estimated_total: float | None = None,
    tax_rate: float = DEFAULT_TAX_RATE,
) -> int:
    """
    Insert a new estimate_requests row from the POST form dict.
    Returns the new row id.

    tax_rate is computed by the caller (app.py) from the customer's city
    and passed in so database.py doesn't need the city→rate lookup table.
    """

    def txt(key):
        return form.get(key, "").strip() or None

    def flag(key):
        return 1 if key in form else 0

    def num(key):
        v = form.get(key, "").strip()
        try:
            return float(v) if "." in v else int(v)
        except (ValueError, TypeError):
            return None

    query = """
    INSERT INTO estimate_requests (
        -- contact
        first_name, last_name, email, phone,

        -- vessel
        vessel_name, vessel_year, vessel_make, vessel_model,
        vessel_type, vessel_type_other,
        hull_material, hull_material_other,
        loa_ft, drive_type, marina, city, slip,

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
        estimated_total, tax_rate
    ) VALUES (
        %(first_name)s, %(last_name)s, %(email)s, %(phone)s,
        %(vessel_name)s, %(vessel_year)s, %(vessel_make)s, %(vessel_model)s,
        %(vessel_type)s, %(vessel_type_other)s,
        %(hull_material)s, %(hull_material_other)s,
        %(loa_ft)s, %(drive_type)s, %(marina)s, %(city)s, %(slip)s,
        %(svc_hull)s, %(svc_anode)s, %(svc_prop)s, %(svc_rudder)s, %(svc_other)s, %(svc_other_detail)s,
        %(frequency)s, %(private_dock)s, %(fouling_level)s, %(fouling_other)s, %(exp_service)s,
        %(anode_required)s, %(anode_material)s, %(anode_material_other)s,
        %(anode_loc_keel)s, %(qty_keel)s,
        %(anode_loc_shaft)s, %(qty_shaft)s,
        %(anode_loc_rudder)s, %(qty_rudder)s,
        %(anode_loc_other)s, %(anode_location_other)s, %(qty_other)s,
        %(prop_inst_required)s, %(prop_type)s, %(prop_type_other)s, %(prop_diam)s,
        %(paint_type)s, %(paint_type_other)s, %(paint_condition)s, %(paint_condition_other)s,
        %(notes)s, %(referral)s,
        %(estimated_total)s, %(tax_rate)s
    )
    RETURNING id
    """

    params = {
        # contact
        "first_name":            txt("first_name"),
        "last_name":             txt("last_name"),
        "email":                 txt("email"),
        "phone":                 txt("phone"),
        # vessel
        "vessel_name":           txt("vessel_name"),
        "vessel_year":           txt("vessel_year"),
        "vessel_make":           txt("vessel_make"),
        "vessel_model":          txt("vessel_model"),
        "vessel_type":           txt("vessel_type"),
        "vessel_type_other":     txt("vesselType-other"),
        "hull_material":         txt("hull_material"),
        "hull_material_other":   txt("hullMat-other"),
        "loa_ft":                num("loa_ft"),
        "drive_type":            txt("drive_type"),
        "marina":                txt("marina"),
        "city":                  txt("city"),
        "slip":                  txt("slip"),
        # services
        "svc_hull":              flag("svc_hull"),
        "svc_anode":             flag("svc_anode"),
        "svc_prop":              flag("svc_prop"),
        "svc_rudder":            flag("svc_rudder"),
        "svc_other":             flag("svc_other"),
        "svc_other_detail":      txt("svc_other_detail"),
        "frequency":             txt("frequency"),
        "private_dock":          txt("privateDock"),
        "fouling_level":         txt("fouling_level"),
        "fouling_other":         txt("fouling_other"),
        "exp_service":           txt("expService"),
        # anode
        "anode_required":        txt("anode_required"),
        "anode_material":        txt("anode_material"),
        "anode_material_other":  txt("anode_material_other"),
        "anode_loc_keel":        flag("anode_loc_keel"),
        "qty_keel":              num("qty_keel"),
        "anode_loc_shaft":       flag("anode_loc_shaft"),
        "qty_shaft":             num("qty_shaft"),
        "anode_loc_rudder":      flag("anode_loc_rudder"),
        "qty_rudder":            num("qty_rudder"),
        "anode_loc_other":       flag("anode_loc_other"),
        "anode_location_other":  txt("anode_location_other"),
        "qty_other":             num("qty_other"),
        # prop
        "prop_inst_required":    txt("prop_inst_required"),
        "prop_type":             txt("propType"),
        "prop_type_other":       txt("prop_other"),
        "prop_diam":             num("propDiam"),
        # paint
        "paint_type":            txt("paintType"),
        "paint_type_other":      txt("paint-type-other"),
        "paint_condition":       txt("paintCond"),
        "paint_condition_other": txt("paintCond-other"),
        # misc
        "notes":                 txt("notes"),
        "referral":              txt("referral"),
        # financials
        "estimated_total":       estimated_total,
        "tax_rate":              tax_rate,
    }

    with get_conn() as conn:
        new_id = conn.execute(query, params).fetchone()["id"]
        conn.execute(
            """INSERT INTO request_status_log (request_id, old_status, new_status, changed_by)
               VALUES (%s, NULL, 'new', 'system')""",
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
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {VALID_STATUSES}")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM estimate_requests WHERE id = %s", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        old_status = row["status"]
        conn.execute(
            "UPDATE estimate_requests SET status = %s, status_notes = %s WHERE id = %s",
            (new_status, log_notes, request_id),
        )
        conn.execute(
            """INSERT INTO request_status_log
               (request_id, old_status, new_status, changed_by, log_notes)
               VALUES (%s, %s, %s, %s, %s)""",
            (request_id, old_status, new_status, changed_by, log_notes),
        )

    logger.info(
        "Request id=%s status: %s → %s (by %s)", request_id, old_status, new_status, changed_by
    )


# ── Read helpers ──────────────────────────────────────────────────────────────
def get_request(request_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM estimate_requests WHERE id = %s", (request_id,)
        ).fetchone()


def get_requests_by_status(status: str) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM estimate_requests WHERE status = %s ORDER BY created_at DESC",
            (status,),
        ).fetchall()


def get_status_log(request_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM request_status_log WHERE request_id = %s ORDER BY changed_at ASC",
            (request_id,),
        ).fetchall()


# ── Deposit helpers ───────────────────────────────────────────────────────────
def set_deposit_paid(request_id: int, method: str = "unspecified") -> None:
    """Mark the mobilization fee as collected."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT deposit_paid FROM estimate_requests WHERE id = %s",
            (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            """UPDATE estimate_requests
               SET deposit_paid = 1,
                   deposit_paid_at = now(),
                   deposit_payment_method = %s
               WHERE id = %s""",
            (method, request_id)
        )
    logger.info("Deposit marked paid for request id=%s via %s", request_id, method)


# ── Invoice helpers ───────────────────────────────────────────────────────────
def compute_balance(
    final_invoice_total: float,
    amount_paid: float,
    tax_rate: float = DEFAULT_TAX_RATE,
) -> float:
    """
    Balance remaining after deposit credit (with tax) and any additional payments.

    final_invoice_total  — pre-tax service total set by admin
    amount_paid          — payments collected toward the final balance (not the deposit)
    tax_rate             — applicable sales-tax rate in percent (e.g. 10.5)

    The mobilization fee was collected at booking *including tax*, so the
    full tax-inclusive deposit is credited here automatically.
    """
    tax        = final_invoice_total * (tax_rate / 100.0)
    grand      = final_invoice_total + tax
    mob_credit = MOBILIZATION_FEE * (1.0 + tax_rate / 100.0)
    return max(0.0, grand - mob_credit - amount_paid)


def set_final_invoice_total(request_id: int, total: float) -> None:
    """
    Set the confirmed pre-tax final invoice total for a completed job.
    Call this before record_payment().
    """
    if total <= 0:
        raise ValueError("Final invoice total must be greater than zero.")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM estimate_requests WHERE id = %s", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            "UPDATE estimate_requests SET final_invoice_total = %s WHERE id = %s",
            (total, request_id),
        )

    logger.info("Final invoice total set to $%.2f for request id=%s", total, request_id)


def set_anode_unit_price(request_id: int, price: float) -> None:
    """
    Set the per-anode material cost for a completed job.
    Used to display the anode materials line item on the final invoice.
    price may be 0 if the customer supplied their own anodes.
    """
    if price < 0:
        raise ValueError("Anode unit price cannot be negative.")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM estimate_requests WHERE id = %s", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            "UPDATE estimate_requests SET anode_unit_price = %s WHERE id = %s",
            (price, request_id),
        )

    logger.info("Anode unit price set to $%.2f for request id=%s", price, request_id)


def set_invoice_notes(request_id: int, notes: str) -> None:
    """
    Set (or replace) the diver/admin notes shown on the final invoice.
    Used to explain any scope changes, extra work performed, or conditions
    encountered during service. Passing an empty string clears the field.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM estimate_requests WHERE id = %s", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            "UPDATE estimate_requests SET invoice_notes = %s WHERE id = %s",
            (notes.strip() or None, request_id),
        )

    logger.info("Invoice notes updated for request id=%s", request_id)


def record_payment(
    request_id: int,
    amount: float,
    method: str = "unspecified",
    notes: str | None = None,
) -> dict:
    """
    Record a full or partial payment against the final (pre-tax) invoice.

    Returns a dict with updated figures:
        {
            "request_id":   int,
            "amount_paid":  float,
            "balance_due":  float,   # tax-inclusive balance
            "status":       str,
        }
    """
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    with get_conn() as conn:
        # FOR UPDATE locks the row so two simultaneous payments can't
        # both read the same amount_paid and overwrite each other.
        row = conn.execute(
            """SELECT final_invoice_total, amount_paid, tax_rate
               FROM estimate_requests WHERE id = %s FOR UPDATE""",
            (request_id,),
        ).fetchone()

        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        if row["final_invoice_total"] is None:
            raise ValueError(
                f"Request id={request_id} has no final_invoice_total set yet. "
                "Set the final invoice total before recording payment."
            )

        tr              = row["tax_rate"] if row["tax_rate"] is not None else DEFAULT_TAX_RATE
        new_amount_paid = (row["amount_paid"] or 0.0) + amount
        balance         = compute_balance(row["final_invoice_total"], new_amount_paid, tr)

        if balance <= 0:
            status = "paid_in_full"
        elif new_amount_paid > 0:
            status = "partial"
        else:
            status = "unpaid"

        conn.execute(
            """UPDATE estimate_requests
               SET amount_paid    = %s,
                   payment_status = %s,
                   payment_method = %s,
                   payment_date   = now(),
                   payment_notes  = %s
               WHERE id = %s""",
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
    """Return a full payment summary for a request."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT final_invoice_total, deposit_paid,
                      amount_paid, payment_status,
                      payment_method, payment_date, payment_notes,
                      tax_rate
               FROM estimate_requests WHERE id = %s""",
            (request_id,),
        ).fetchone()

    if not row:
        raise LookupError(f"Request id={request_id} not found.")

    fit         = row["final_invoice_total"]
    amount_paid = row["amount_paid"] or 0.0
    tr          = row["tax_rate"] if row["tax_rate"] is not None else DEFAULT_TAX_RATE
    balance     = compute_balance(fit, amount_paid, tr) if fit is not None else None

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
        "tax_rate":             tr,
    }


# ── Stripe Checkout Session helpers ──────────────────────────────────────────
def save_stripe_session(request_id: int, session_id: str, session_url: str) -> None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM estimate_requests WHERE id = %s", (request_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"Request id={request_id} not found.")

        conn.execute(
            """UPDATE estimate_requests
               SET stripe_session_id  = %s,
                   stripe_session_url = %s
               WHERE id = %s""",
            (session_id, session_url, request_id),
        )

    logger.info(
        "Stripe session saved for request id=%s: session_id=%s",
        request_id, session_id,
    )


def get_stripe_session(request_id: int) -> tuple[str | None, str | None]:
    # NOTE: the uploaded file was cut off at this function; this body is a
    # reconstruction. Compare it with your original and adjust if needed.
    with get_conn() as conn:
        row = conn.execute(
            """SELECT stripe_session_id, stripe_session_url
               FROM estimate_requests WHERE id = %s""",
            (request_id,),
        ).fetchone()

    if not row:
        return None, None
    return row["stripe_session_id"], row["stripe_session_url"]

init_db()