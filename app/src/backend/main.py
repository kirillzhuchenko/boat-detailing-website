import os
from flask import (
    Flask, render_template,
    request, jsonify, abort, redirect)
from app.src.backend.sys_notif.notifier import send_telegram_notification
from app.src.backend.sys_notif.service_notifier import send_telegram_service
from db.database import (
    init_db, save_estimate, get_request, update_status,
    set_deposit_paid, record_payment,
    set_final_invoice_total, get_payment_summary,
    set_anode_unit_price, set_invoice_notes,
    DEFAULT_TAX_RATE,
)
from datetime import date
from dotenv import load_dotenv
from logger import logger

# ── Stripe imports ────────────────────────────────────────────────────────────
import stripe as stripe_lib
from stripe_payments import create_invoice_checkout_session
from db.database import (
    save_stripe_session, get_stripe_session, clear_stripe_session,
)

import math

load_dotenv()

ADMIN_KEY        = os.environ.get("ADMIN_KEY", "changeme")
MOBILIZATION_FEE = 100.0    # keep in sync with database.py / stripe_payments.py


frontend_dir = os.path.join(os.getcwd(), "../frontend")

app = Flask(__name__,
            template_folder = os.path.join(frontend_dir, "templates"),
            static_folder = os.path.join(frontend_dir, "static"),
            )


GALLERY_FOLDER = os.path.join(app.static_folder, "images", "gallery")
ALLOWED_EXTENSIONS = {".jpg", ".png"}

# ── DB init on startup ────────────────────────────────────────────────────────
with app.app_context():
    init_db()


# ── Sales-tax lookup ──────────────────────────────────────────────────────────
CITY_SALES_TAX: dict[str, float] = {
    "Tacoma": 10.5,
    "University Place": 10.3,
    "Fircrest": 10.3,
    "Lakewood": 10.1,
    "Steilacoom": 9.6,
    "DuPont": 9.9,
    "Milton": 10.2,
    "Fife": 10.3,
    "Edgewood": 10.3,
    "Puyallup": 10.2,
    "Sumner": 10.2,
    "Bonney Lake": 10.1,
    "Buckley": 9.9,
    "Orting": 9.9,
    "Gig Harbor": 9.1,
    "Ruston": 10.1,
    "Auburn": 10.3,
    "Pacific": 10.3,
    "Algona": 10.3,
    "Federal Way": 10.4,
    "Des Moines": 10.4,
    "Kent": 10.5,
    "Renton": 10.5,
    "Tukwila": 10.6,
    "SeaTac": 10.4,
    "Burien": 10.4,
    "Normandy Park": 10.4,
    "Seattle": 10.55,
    "Shoreline": 10.5,
    "Lake Forest Park": 10.5,
    "Kenmore": 10.4,
    "Bothell": 10.4,
    "Woodinville": 10.3,
    "Kirkland": 10.5,
    "Redmond": 10.3,
    "Bellevue": 10.3,
    "Mercer Island": 10.3,
    "Issaquah": 10.1,
    "Sammamish": 10.3,
    "Newcastle": 10.3,
    "Beaux Arts Village": 10.3,
    "Medina": 10.3,
    "Clyde Hill": 10.3,
    "Hunts Point": 10.3,
    "Yarrow Point": 10.3,
    "Carnation": 9.0,
    "Duvall": 9.3,
    "Snoqualmie": 10.0,
    "North Bend": 10.1,
    "Bremerton": 9.2,
    "Port Orchard": 9.3,
    "Poulsbo": 9.4,
    "Bainbridge Island": 9.2,
    "Silverdale": 9.2,
    "Everett": 10.4,
    "Mukilteo": 10.6,
    "Edmonds": 10.7,
    "Lynnwood": 10.7,
    "Mountlake Terrace": 10.6,
    "Brier": 10.6,
    "Mill Creek": 10.6,
    "Snohomish": 9.4,
    "Monroe": 9.3,
    "Sultan": 9.2,
    "Arlington": 9.4,
    "Marysville": 9.5,
    "Lake Stevens": 9.6,
    "Granite Falls": 9.4,
    "Stanwood": 8.9,
    "Woodway": 10.6,
}


def get_tax_rate(city: str) -> float:
    """Return the sales-tax rate for a city (case-insensitive). Falls back to DEFAULT_TAX_RATE."""
    if not city:
        return DEFAULT_TAX_RATE
    city_clean = city.strip()
    # Exact match first
    if city_clean in CITY_SALES_TAX:
        return CITY_SALES_TAX[city_clean]
    # Case-insensitive fallback
    lower = city_clean.lower()
    for k, v in CITY_SALES_TAX.items():
        if k.lower() == lower:
            return v
    return DEFAULT_TAX_RATE


# ── Invoice line-item builder: anode section ──────────────────────────────────
def _anode_line_items_estimate(req: dict) -> tuple[list[dict], float]:
    """
    Returns (line_items, subtotal_addition) for the anode section on the ESTIMATE.
    - Labor ($30/ea) is always included when anode service is requested.
    - Materials are shown as TBD only when we supply the anodes ('yes').
    - When customer has their own anodes ('no'), only labor is billed.
    """
    items: list[dict] = []
    total = 0.0

    if not req.get("svc_anode"):
        return items, total

    qty = sum(filter(None, [
        req.get("qty_keel"), req.get("qty_shaft"),
        req.get("qty_rudder"), req.get("qty_other"),
    ])) or 0

    if not qty:
        return items, total

    material = req.get("anode_material") or "standard"
    labor    = qty * 30.0
    total   += labor
    items.append({
        "svc":  "Anode Labor",
        "desc": f"{qty}× {material} — installation",
        "qty":  f"{qty} ea", "rate": "$30/ea", "amt": labor,
    })

    if req.get("anode_required") == "yes":
        items.append({
            "svc":  "Anode Materials",
            "desc": f"{qty}× {material} — parts (price TBD)",
            "qty":  f"{qty} ea", "rate": "TBD", "amt": None,
        })

    return items, total


def _anode_line_items_final(req: dict) -> tuple[list[dict], float]:
    """
    Returns (line_items, subtotal_addition) for the anode section on the FINAL INVOICE.
    - Labor ($30/ea) always included.
    - Materials: qty × anode_unit_price (if set by admin), else TBD.
    - When customer has their own anodes ('no'), only labor is billed.
    """
    items: list[dict] = []
    total = 0.0

    if not req.get("svc_anode"):
        return items, total

    qty = sum(filter(None, [
        req.get("qty_keel"), req.get("qty_shaft"),
        req.get("qty_rudder"), req.get("qty_other"),
    ])) or 0

    if not qty:
        return items, total

    material = req.get("anode_material") or "standard"
    labor    = qty * 30.0
    total   += labor
    items.append({
        "svc":  "Anode Labor",
        "desc": f"{qty}× {material} — installation",
        "qty":  f"{qty} ea", "rate": "$30/ea", "amt": labor,
    })

    if req.get("anode_required") == "yes":
        unit_price = req.get("anode_unit_price")
        if unit_price is not None:
            mat_amt = qty * float(unit_price)
            total  += mat_amt
            items.append({
                "svc":  "Anode Materials",
                "desc": f"{qty}× {material} — parts",
                "qty":  f"{qty} ea",
                "rate": f"${float(unit_price):.2f}/ea",
                "amt":  mat_amt,
            })
        else:
            items.append({
                "svc":  "Anode Materials",
                "desc": f"{qty}× {material} — parts (pending admin entry)",
                "qty":  f"{qty} ea", "rate": "TBD", "amt": None,
            })

    return items, total


# ── Gallery ───────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    images = get_gallery_images()
    return render_template("index.html", images=images)


def get_gallery_images():
    if not os.path.exists(GALLERY_FOLDER):
        os.makedirs(GALLERY_FOLDER)
        return []
    images = [
        f for f in os.listdir(GALLERY_FOLDER)
        if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS
    ]
    images.sort(key=lambda f: f.lower())
    return images


# ── Admin notification helpers ────────────────────────────────────────────────
def _job_header(req) -> str:
    name   = f"{req.get('first_name', '')} {req.get('last_name', '')}".strip()
    vessel = f"{req.get('vessel_year', '')} {req.get('vessel_make', '')} {req.get('vessel_model', '')}".strip()
    loa    = req.get("loa_ft")
    city   = req.get("city") or ""
    marina = req.get("marina") or "—"
    loa_str    = f" ({int(loa)} ft)" if loa else ""
    city_str   = f", {city}" if city else ""
    return (
        f"👤 *{name}*\n"
        f"🚢 {vessel}{loa_str}\n"
        f"📍 {marina}{city_str}"
    )


def _notify_status_change(req, old_status, new_status, changed_by, notes):
    STATUS_EMOJI = {
        "new": "🆕", "contacted": "📞", "scheduled": "📅",
        "in_progress": "🔧", "completed": "✅", "invoiced": "🧾", "cancelled": "❌",
    }
    emoji = STATUS_EMOJI.get(new_status, "🔄")
    lines = [
        f"{emoji} *Status Update — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req), "",
        f"📊 `{old_status}` → `{new_status}`",
        f"👤 Changed by: {changed_by}",
    ]
    if notes:
        lines.append(f"📝 {notes}")
    _send(lines)


def _notify_deposit_paid(req, method):
    lines = [
        f"💰 *Deposit Collected — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req), "",
        f"💵 ${MOBILIZATION_FEE:.2f} via {method}",
        "🔐 Appointment secured",
    ]
    _send(lines)


def _notify_invoice_total_set(req, total):
    tr      = req.get("tax_rate") or DEFAULT_TAX_RATE
    tax     = total * (tr / 100.0)
    grand   = total + tax
    credit  = MOBILIZATION_FEE * (1.0 + tr / 100.0)
    balance = max(0.0, grand - credit)
    lines = [
        f"🧾 *Invoice Total Set — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req), "",
        f"💰 Pre-Tax Total:      ${total:.2f}",
        f"🏛  Sales Tax ({tr:.2f}%): +${tax:.2f}",
        f"💳 Deposit Credit:    −${credit:.2f}",
        f"⚖️  Balance Due:        ${balance:.2f}",
    ]
    _send(lines)


def _notify_payment(req, amount, method, notes, result):
    status      = result["status"]
    amount_paid = result["amount_paid"]
    balance     = result["balance_due"]
    status_line = (
        "✅ *Paid in Full — balance cleared*"
        if status == "paid_in_full"
        else f"⚖️  Balance Remaining: ${balance:.2f}"
    )
    lines = [
        f"💳 *Payment Recorded — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req), "",
        f"💵 \\+${amount:.2f} via {method}",
        f"📊 Total Paid: ${amount_paid:.2f}",
        status_line,
    ]
    if notes:
        lines.append(f"📝 {notes}")
    _send(lines)


def _send(lines):
    try:
        send_telegram_notification("\n".join(lines))
    except Exception as e:
        logger.error("Telegram admin notification failed: %s", e)


# ── Telegram message builders (customer-facing) ───────────────────────────────
def build_contact_message(first_name, last_name, phone, email, message):
    return (
        f"📬 *New Contact Form*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:* {first_name} {last_name}\n"
        f"📧 *Email:* {email}\n"
        f"📞 *Phone:* {phone or '—'}\n\n"
        f"💬 *Message:*\n{message}"
    )


def build_schedule_message(data, request_id: int, estimated_total: float | None):
    first_name = data.get("first_name", "")
    last_name  = data.get("last_name", "")
    phone      = data.get("phone") or "—"
    email      = data.get("email", "")
    loa        = data.get("loa_ft", "?")
    vessel     = f"{data.get('vessel_year','')} {data.get('vessel_make','')} {data.get('vessel_model','')}".strip()
    marina     = data.get("marina", "—")
    city       = data.get("city", "—") or "—"
    total_str  = f"${estimated_total:,.2f}" if estimated_total is not None else "N/A"

    svc_map = {
        "svc_hull":   "Hull Cleaning",
        "svc_anode":  "Anode Replacement",
        "svc_prop":   "Propeller Service",
        "svc_rudder": "Rudder Inspection",
        "svc_other":  data.get("svc_other_detail") or "Other",
    }
    services     = [label for key, label in svc_map.items() if key in data]
    services_str = ", ".join(services) if services else "—"

    return (
        f"📋 *New Service Request — #{request_id}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:* {first_name} {last_name}\n"
        f"📧 *Email:* {email}\n"
        f"📞 *Phone:* {phone}\n\n"
        f"🚢 *Vessel:* {vessel} ({loa} ft)\n"
        f"📍 *Marina:* {marina}, {city}\n\n"
        f"🔧 *Services:* {services_str}\n"
        f"💰 *Estimate:* {total_str}"
    )


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/contact", methods=["POST"])
def contact():
    first_name = request.form.get("first_name", "").strip()
    last_name  = request.form.get("last_name",  "").strip()
    phone      = request.form.get("phone",      "").strip()
    email      = request.form.get("email",      "").strip()
    message    = request.form.get("message",    "").strip()

    errors = []
    if not first_name: errors.append("First name is required.")
    if not last_name:  errors.append("Last name is required.")
    if not email:      errors.append("Email is required.")
    if not message:    errors.append("Message is required.")

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    try:
        text = build_contact_message(first_name, last_name, phone, email, message)
        send_telegram_service(text)
    except Exception as e:
        logger.error("Failed to send Telegram contact message: %s", e)
        return jsonify({"status": "error", "errors": ["Could not send your message. Please try again later."]}), 500

    return jsonify({"status": "ok", "message": "Thanks! We'll be in touch soon."})


@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.form.to_dict()

    required = {
        "first_name":    "First name",
        "last_name":     "Last name",
        "email":         "Email",
        "phone":         "Phone",
        "vessel_name":   "Vessel name",
        "vessel_year":   "Vessel year",
        "vessel_make":   "Vessel make",
        "vessel_model":  "Vessel model",
        "vessel_type":   "Vessel type",
        "hull_material": "Hull material",
        "loa_ft":        "Length overall",
        "marina":        "Marina / dock",
        "city":          "City",
        "anode_required":"Anode required",
        "paintType":     "Paint type",
        "paintCond":     "Paint condition",
    }
    errors = [f"{label} is required." for field, label in required.items() if not data.get(field)]

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    estimated_total = None
    raw_total = data.get("estimated_total", "").strip()
    if raw_total:
        try:
            estimated_total = float(raw_total)
        except ValueError:
            pass

    city     = data.get("city", "").strip()
    tax_rate = get_tax_rate(city)

    try:
        request_id = save_estimate(data, estimated_total=estimated_total, tax_rate=tax_rate)
    except Exception as e:
        logger.error("Failed to save estimate to DB: %s", e)
        return jsonify({"status": "error", "errors": ["Could not save your request. Please try again later."]}), 500

    try:
        text = build_schedule_message(data, request_id, estimated_total)
        send_telegram_service(text)
    except Exception as e:
        logger.error("Failed to send Telegram estimate message: %s", e)

    return jsonify({
        "status":     "ok",
        "message":    "Request received! We'll be in touch within one business day.",
        "request_id": request_id,
    })


@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")


@app.route("/terms-of-service")
def terms_of_service():
    return render_template("terms_of_service.html")


# ── Estimate invoice (pre-service) ───────────────────────────────────────────
@app.route("/invoice/<int:request_id>")
def generate_invoice(request_id):
    req        = dict(get_request(request_id) or abort(404))
    line_items = []
    subtotal   = 0.0
    loa        = float(req.get("loa_ft") or 0)

    # Hull Cleaning — minimum $150
    if req.get("svc_hull"):
        rate = 5 if loa < 40 else (8 if loa < 80 else 12)
        base = loa * rate
        fm   = {"light": 1.0, "mod": 1.5, "exces": 2.0}.get(req.get("fouling_level"), 1.0)
        fd   = {"Monthly": 0.25, "Bi-monthly": 0.15}.get(req.get("frequency"), 0.0)
        raw  = base * fm * (1 - fd)
        amt  = max(150.0, raw)
        subtotal += amt

        desc = f"{int(loa)} ft LOA"
        if fm > 1:   desc += f", +{int((fm - 1) * 100)}% fouling surcharge"
        if fd > 0:   desc += f", −{int(fd * 100)}% recurring discount"
        if raw < 150.0: desc += " (minimum $150 applied)"

        line_items.append({
            "svc": "Hull Cleaning", "desc": desc,
            "qty": f"{int(loa)} ft", "rate": f"${rate}/ft", "amt": amt,
        })

    # Anode — labor + materials (TBD)
    anode_items, anode_sub = _anode_line_items_estimate(req)
    line_items += anode_items
    subtotal   += anode_sub

    # Propeller Service
    if req.get("svc_prop") and req.get("prop_inst_required") == "yes":
        pt   = req.get("prop_type")
        diam = float(req.get("prop_diam") or 0)
        pr   = {"prop_fixed": 25, "prop_fold": 30}.get(pt)
        if pr and diam:
            a = pr * diam; subtotal += a
            lb = "Fixed-Blade" if pt == "prop_fixed" else "Folding"
            line_items.append({
                "svc": "Propeller Service",
                "desc": f'{int(diam)}" {lb} — removal & installation',
                "qty": f'{int(diam)}"', "rate": f"${pr}/in", "amt": a,
            })
        else:
            line_items.append({
                "svc": "Propeller Service", "desc": "Custom / contact for pricing",
                "qty": "—", "rate": "TBD", "amt": None,
            })

    # Rudder Inspection
    if req.get("svc_rudder"):
        line_items.append({
            "svc": "Rudder Inspection",
            "desc": "Visual inspection & written condition report",
            "qty": "—", "rate": "TBD", "amt": None,
        })

    # Other services
    if req.get("svc_other"):
        line_items.append({
            "svc": "Additional Service",
            "desc": req.get("svc_other_detail") or "—",
            "qty": "—", "rate": "TBD", "amt": None,
        })

    # Private Dock Surcharge
    if req.get("private_dock") == "yes":
        subtotal += 25
        line_items.append({
            "svc": "Private Dock Surcharge", "desc": "Remote/private dock access",
            "qty": "—", "rate": "$25", "amt": 25,
        })

    # Expedited Service
    if req.get("exp_service") == "yes":
        subtotal += 600
        line_items.append({
            "svc": "Expedited Service", "desc": "Priority scheduling",
            "qty": "—", "rate": "$600", "amt": 600,
        })

    # Mobilization Fee (shown as a line item; also deducted in totals box)
    subtotal += MOBILIZATION_FEE
    line_items.append({
        "svc": "Crew Mobilization Fee",
        "desc": "Applied toward final invoice at completion",
        "qty": "—", "rate": f"${MOBILIZATION_FEE:.0f}", "amt": MOBILIZATION_FEE,
    })

    return render_template(
        "invoice_dark_auto.html",
        req=req, line_items=line_items,
        subtotal=subtotal,
        mobilization_fee=MOBILIZATION_FEE,
        balance_due=subtotal - MOBILIZATION_FEE,
        invoice_date=date.today().strftime("%B %d, %Y"),
        invoice_num=f"BWD-{req['id']:04d}",
    )


# ── Mobilization / deposit invoice ────────────────────────────────────────────
@app.route("/invoice/<int:request_id>/deposit")
def mobilization_invoice(request_id):
    req      = dict(get_request(request_id) or abort(404))
    city     = req.get("city") or ""
    tax_rate = req.get("tax_rate") or get_tax_rate(city)
    tax_amt  = round(MOBILIZATION_FEE * (tax_rate / 100.0), 2)
    total_due = MOBILIZATION_FEE + tax_amt

    return render_template(
        "invoice_mobilization_auto.html",
        req=req,
        paid=bool(req.get("deposit_paid")),
        inv_date=date.today().strftime("%B %d, %Y"),
        inv_num=f"BWD-{req['id']:04d}-D",
        job_num=f"BWD-{req['id']:04d}",
        mobilization_fee=MOBILIZATION_FEE,
        tax_rate=tax_rate,
        tax_amt=tax_amt,
        total_due=total_due,
    )


# ── Final (post-service) invoice ──────────────────────────────────────────────
@app.route("/invoice/<int:request_id>/final")
def generate_final_invoice(request_id):
    req = dict(get_request(request_id) or abort(404))

    final_total = req.get("final_invoice_total")
    if not final_total:
        abort(404, description=f"No final invoice total set for job #{request_id}. "
                               "Use /admin/invoice/<id>/set-total first.")

    city     = req.get("city") or ""
    tax_rate = req.get("tax_rate") or get_tax_rate(city)
    tax_amt  = round(final_total * (tax_rate / 100.0), 2)
    grand    = final_total + tax_amt
    mob_credit = round(MOBILIZATION_FEE * (1.0 + tax_rate / 100.0), 2)

    amount_paid    = req.get("amount_paid") or 0.0
    payment_status = req.get("payment_status") or "unpaid"
    balance_due    = max(0.0, grand - mob_credit - amount_paid)

    # ── Rebuild line items ────────────────────────────────────────────────────
    line_items = []
    subtotal   = 0.0
    loa        = float(req.get("loa_ft") or 0)

    # Hull Cleaning — minimum $150
    if req.get("svc_hull"):
        rate = 5 if loa < 40 else (8 if loa < 80 else 12)
        base = loa * rate
        fm   = {"light": 1.0, "mod": 1.5, "exces": 2.0}.get(req.get("fouling_level"), 1.0)
        fd   = {"Monthly": 0.25, "Bi-monthly": 0.15}.get(req.get("frequency"), 0.0)
        raw  = base * fm * (1 - fd)
        amt  = max(150.0, raw)
        subtotal += amt

        desc = f"{int(loa)} ft LOA"
        if fm > 1:      desc += f", +{int((fm - 1) * 100)}% fouling surcharge"
        if fd > 0:      desc += f", −{int(fd * 100)}% recurring discount"
        if raw < 150.0: desc += " (minimum $150 applied)"

        line_items.append({
            "svc": "Hull Cleaning", "desc": desc,
            "qty": f"{int(loa)} ft", "rate": f"${rate}/ft", "amt": amt,
        })

    # Anode — labor + materials (admin-set price)
    anode_items, anode_sub = _anode_line_items_final(req)
    line_items += anode_items
    subtotal   += anode_sub

    # Propeller Service
    if req.get("svc_prop") and req.get("prop_inst_required") == "yes":
        pt   = req.get("prop_type")
        diam = float(req.get("prop_diam") or 0)
        pr   = {"prop_fixed": 25, "prop_fold": 30}.get(pt)
        if pr and diam:
            a = pr * diam; subtotal += a
            lb = "Fixed-Blade" if pt == "prop_fixed" else "Folding"
            line_items.append({
                "svc": "Propeller Service",
                "desc": f'{int(diam)}" {lb} — removal & installation',
                "qty": f'{int(diam)}"', "rate": f"${pr}/in", "amt": a,
            })
        else:
            line_items.append({
                "svc": "Propeller Service", "desc": "Custom / contact for pricing",
                "qty": "—", "rate": "TBD", "amt": None,
            })

    # Rudder Inspection
    if req.get("svc_rudder"):
        line_items.append({
            "svc": "Rudder Inspection",
            "desc": "Visual inspection & written condition report",
            "qty": "—", "rate": "TBD", "amt": None,
        })

    # Other services
    if req.get("svc_other"):
        line_items.append({
            "svc": "Additional Service",
            "desc": req.get("svc_other_detail") or "—",
            "qty": "—", "rate": "TBD", "amt": None,
        })

    # Private Dock Surcharge
    if req.get("private_dock") == "yes":
        subtotal += 25
        line_items.append({
            "svc": "Private Dock Surcharge", "desc": "Remote/private dock access",
            "qty": "—", "rate": "$25", "amt": 25,
        })

    # Expedited Service
    if req.get("exp_service") == "yes":
        subtotal += 600
        line_items.append({
            "svc": "Expedited Service", "desc": "Priority scheduling",
            "qty": "—", "rate": "$600", "amt": 600,
        })

    return render_template(
        "invoice_final_auto.html",
        req=req,
        line_items=line_items,
        estimated_total=req.get("estimated_total"),
        final_total=final_total,
        mobilization_fee=MOBILIZATION_FEE,
        tax_rate=tax_rate,
        tax_amt=tax_amt,
        grand_total=grand,
        mob_credit=mob_credit,
        amount_paid=amount_paid,
        balance_due=balance_due,
        payment_status=payment_status,
        invoice_date=date.today().strftime("%B %d, %Y"),
        invoice_num=f"BWD-{req['id']:04d}-F",
    )


# ── Stripe: create / reuse Checkout Session ───────────────────────────────────
@app.route("/invoice/<int:request_id>/checkout")
async def stripe_checkout(request_id):
    req = dict(get_request(request_id) or abort(404))

    final_total = req.get("final_invoice_total")
    if not final_total:
        abort(404, description=f"No final invoice total set for job #{request_id}.")

    city     = req.get("city") or ""
    tax_rate = req.get("tax_rate") or get_tax_rate(city)

    amount_paid    = req.get("amount_paid") or 0.0
    payment_status = req.get("payment_status") or "unpaid"

    if payment_status == "paid_in_full":
        return redirect(f"/invoice/{request_id}/final")

    # Try to reuse cached session
    cached_session_id, cached_session_url = get_stripe_session(request_id)
    if cached_session_id and cached_session_url:
        try:
            existing = await stripe_lib.checkout.Session.retrieve_async(cached_session_id)
            if existing.status == "open":
                return redirect(existing.url)
        except stripe_lib.error.StripeError as e:
            logger.warning("Cached session %s invalid: %s", cached_session_id, e)
        clear_stripe_session(request_id)

    customer_name  = f"{req.get('first_name', '')} {req.get('last_name', '')}".strip()
    customer_email = req.get("email", "")
    invoice_num    = f"BWD-{req['id']:04d}-F"

    try:
        session = await create_invoice_checkout_session(
            request_id=request_id,
            invoice_num=invoice_num,
            final_total=final_total,
            amount_paid=amount_paid,
            customer_name=customer_name,
            customer_email=customer_email,
            tax_rate=tax_rate,
        )
    except ValueError as e:
        logger.info("Checkout skipped for job #%s: %s", request_id, e)
        return redirect(f"/invoice/{request_id}/final")
    except stripe_lib.error.StripeError as e:
        logger.error("Stripe error for job #%s: %s", request_id, e)
        abort(502, description="Payment gateway error — please try again or contact us.")

    save_stripe_session(request_id, session.id, session.url)
    return redirect(session.url, code=303)


# ── Stripe webhook ────────────────────────────────────────────────────────────
@app.route("/stripe/webhook", methods=["POST"])
async def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret     = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe_lib.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe_lib.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event["type"]
    session    = event["data"]["object"]
    request_id_str = (session.get("metadata") or {}).get("request_id")
    if not request_id_str:
        return jsonify({"status": "ignored — no request_id in metadata"}), 200

    request_id = int(request_id_str)

    if event_type == "checkout.session.completed":
        amount_received = (session.get("amount_total") or 0) / 100
        try:
            result = record_payment(request_id, amount_received, method="card",
                                    notes=f"Stripe Checkout Session {session['id']}")
            clear_stripe_session(request_id)
            req = dict(get_request(request_id))
            _notify_payment(req, amount_received, "card", None, result)
        except (LookupError, ValueError) as e:
            logger.error("webhook: failed to record payment for job #%s: %s", request_id, e)
            return jsonify({"error": str(e)}), 500

    elif event_type == "checkout.session.expired":
        try:
            clear_stripe_session(request_id)
        except LookupError:
            pass

    return jsonify({"status": "ok"}), 200


# ── Payment confirmed landing page ────────────────────────────────────────────
@app.route("/invoice/<int:request_id>/paid")
def payment_confirmed(request_id):
    req = dict(get_request(request_id) or abort(404))
    return render_template(
        "payment_confirmed.html",
        req=req,
        invoice_num=f"BWD-{req['id']:04d}-F",
        session_id=request.args.get("session_id", ""),
    )


# ── Admin helpers ─────────────────────────────────────────────────────────────
def _check_admin(req):
    return req.form.get("key") == ADMIN_KEY


_NON_FINITE = {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}

def _parse_money(raw, field, *, allow_zero=True, max_value=1_000_000.0):
    """Parse a user-supplied dollar amount; reject NaN/inf, negatives, and absurd values."""
    raw = (raw or "0").strip()
    if raw.lower() in _NON_FINITE:           # explicit guard (also satisfies Semgrep)
        raise ValueError(f"{field} must be a valid number")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{field} must be a valid number") from None
    if not math.isfinite(value):             # catches any spelling the set missed
        raise ValueError(f"{field} must be a valid number")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{field} must be {'non-negative' if allow_zero else 'greater than zero'}")
    if value > max_value:
        raise ValueError(f"{field} is too large")
    return round(value, 2)

@app.route("/admin/request/<int:request_id>/status", methods=["POST"])
def admin_update_status(request_id):
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    new_status = request.form.get("status", "").strip()
    changed_by = request.form.get("changed_by", "staff").strip()
    notes      = request.form.get("notes", "").strip() or None

    try:
        req        = dict(get_request(request_id) or abort(404))
        old_status = req["status"]
        update_status(request_id, new_status, changed_by=changed_by, log_notes=notes)
        _notify_status_change(req, old_status, new_status, changed_by, notes)
        return jsonify({
            "status": "ok", "request_id": request_id,
            "old_status": old_status, "new_status": new_status,
            "message": f"Status updated: {old_status} → {new_status}",
        })
    except (LookupError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/admin/invoice/<int:request_id>/set-total", methods=["POST"])
def admin_set_total(request_id):
    """
    Set the confirmed pre-tax job total before recording payment.

    POST params:  key, total
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        total = _parse_money(request.form.get("total"), "total", allow_zero=False)
        req   = dict(get_request(request_id) or abort(404))
        set_final_invoice_total(request_id, total)
        _notify_invoice_total_set(req, total)
        return jsonify({
            "status": "ok", "request_id": request_id,
            "final_invoice_total": total,
            "message": f"Final pre-tax total set to ${total:.2f} for job #{request_id}.",
        })
    except (LookupError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/admin/invoice/<int:request_id>/set-anode-price", methods=["POST"])
def admin_set_anode_price(request_id):
    """
    Set the per-anode material cost for display on the final invoice.

    POST params:  key, price  (price=0 if customer supplied their own)

    Example:
        curl -X POST http://localhost:5000/admin/invoice/5/set-anode-price \\
             -d "key=changeme&price=45"
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        price = _parse_money(request.form.get("price"), "price")
        set_anode_unit_price(request_id, price)
        return jsonify({
            "status": "ok", "request_id": request_id,
            "anode_unit_price": price,
            "message": f"Anode unit price set to ${price:.2f} for job #{request_id}.",
        })
    except (LookupError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/admin/invoice/<int:request_id>/set-notes", methods=["POST"])
def admin_set_invoice_notes(request_id):
    """
    Set or update the diver/admin notes shown on the final invoice.
    Use this to explain scope changes, conditions encountered, or
    any extra work performed beyond the original estimate.

    POST params:
        key   — admin password
        notes — free-text note (send empty string to clear)

    Example:
        curl -X POST http://localhost:5000/admin/invoice/5/set-notes \\
             -d "key=changeme&notes=Fouling was heavier than estimated on the keel area. Extra pass required. 2 shaft anodes replaced instead of 1."
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    notes = request.form.get("notes", "").strip()
    try:
        set_invoice_notes(request_id, notes)
        return jsonify({
            "status":     "ok",
            "request_id": request_id,
            "message":    "Invoice notes updated." if notes else "Invoice notes cleared.",
        })
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/admin/invoice/<int:request_id>/pay", methods=["POST"])
def admin_record_payment(request_id):
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        amount = _parse_money(request.form.get("amount"), "amount", allow_zero=False)
        method = request.form.get("method", "unspecified")
        notes  = request.form.get("notes", "").strip() or None
        req    = dict(get_request(request_id) or abort(404))

        result = record_payment(request_id, amount, method=method, notes=notes)
        _notify_payment(req, amount, method, notes, result)

        return jsonify({
            "status": "ok",
            "request_id":     result["request_id"],
            "amount_paid":    f"${result['amount_paid']:.2f}",
            "balance_due":    f"${result['balance_due']:.2f}",
            "payment_status": result["status"],
            "message": (
                "Paid in full — balance cleared."
                if result["status"] == "paid_in_full"
                else f"Partial payment recorded. Balance remaining: ${result['balance_due']:.2f}"
            ),
        })
    except (LookupError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/admin/invoice/<int:request_id>/status", methods=["GET"])
def admin_payment_status(request_id):
    if request.args.get("key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        summary = get_payment_summary(request_id)
        return jsonify(summary)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/admin/deposit/<int:request_id>/mark-paid", methods=["POST"])
def mark_deposit_paid(request_id):
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    method = request.form.get("method", "unspecified")
    try:
        req = dict(get_request(request_id) or abort(404))
        set_deposit_paid(request_id, method=method)
        _notify_deposit_paid(req, method)
        return jsonify({
            "status": "ok", "request_id": request_id,
            "message": f"Deposit marked paid via {method} for job #{request_id}.",
        })
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


if __name__ == "__main__":
    app.run(

    )