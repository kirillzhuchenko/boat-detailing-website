import os
import logging
from flask import Flask, render_template, request, jsonify
from notifier import send_telegram_message
from database import init_db, save_estimate


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.ERROR, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

GALLERY_FOLDER = os.path.join(app.static_folder, "images", "gallery")
ALLOWED_EXTENSIONS = {".jpg", ".png"}


# ── DB init on startup ────────────────────────────────────────────────────────
with app.app_context():
    init_db()   # creates tables if they don't exist; safe to call every time


# ── Gallery ───────────────────────────────────────────────────────────────────
@app.route('/')
def home():
    images = get_gallery_images()
    return render_template('index.html', images=images)


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


# ── Telegram message builders ─────────────────────────────────────────────────
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
    first_name = data.get('first_name', '')
    last_name  = data.get('last_name', '')
    phone      = data.get('phone') or '—'
    email      = data.get('email', '')
    loa        = data.get('loa_ft', '?')
    vessel     = f"{data.get('vessel_year','')} {data.get('vessel_make','')} {data.get('vessel_model','')}".strip()
    marina     = data.get('marina', '—')
    total_str  = f"${estimated_total:,.2f}" if estimated_total is not None else "N/A"

    # Build a readable services list
    svc_map = {
        'svc_hull':   'Hull Cleaning',
        'svc_anode':  'Anode Replacement',
        'svc_prop':   'Propeller Service',
        'svc_rudder': 'Rudder Inspection',
        'svc_other':  data.get('svc_other_detail') or 'Other',
    }
    services = [label for key, label in svc_map.items() if key in data]
    services_str = ', '.join(services) if services else '—'

    return (
        f"📋 *New Service Request — #{request_id}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:* {first_name} {last_name}\n"
        f"📧 *Email:* {email}\n"
        f"📞 *Phone:* {phone}\n\n"
        f"🚢 *Vessel:* {vessel} ({loa} ft)\n"
        f"📍 *Marina:* {marina}\n\n"
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
        send_telegram_message(text)
    except Exception as e:
        logger.error("Failed to send Telegram contact message: %s", e)
        return jsonify({"status": "error", "errors": ["Could not send your message. Please try again later."]}), 500

    return jsonify({"status": "ok", "message": "Thanks! We'll be in touch soon."})


@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.form.to_dict()

    # ── Server-side validation ────────────────────────────────
    required = {
        'first_name':    'First name',
        'last_name':     'Last name',
        'email':         'Email',
        'phone':         'Phone',
        'vessel_name':   'Vessel name',
        'vessel_year':   'Vessel year',
        'vessel_make':   'Vessel make',
        'vessel_model':  'Vessel model',
        'vessel_type':   'Vessel type',
        'hull_material': 'Hull material',
        'loa_ft':        'Length overall',
        'marina':        'Marina / dock',
        'anode_required':'Anode required',
        'paintType':     'Paint type',
        'paintCond':     'Paint condition',
    }
    errors = [f"{label} is required." for field, label in required.items() if not data.get(field)]

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    # ── Parse estimated total sent by the frontend ────────────
    # The frontend JS calculates this — we trust it as an advisory figure only.
    # Pass it as a hidden field named `estimated_total` from the JS before submit,
    # or compute it server-side in the future.
    estimated_total = None
    raw_total = data.get("estimated_total", "").strip()
    if raw_total:
        try:
            estimated_total = float(raw_total)
        except ValueError:
            pass

    # ── Save to DB ────────────────────────────────────────────
    try:
        request_id = save_estimate(data, estimated_total=estimated_total)
    except Exception as e:
        logger.error("Failed to save estimate to DB: %s", e)
        return jsonify({"status": "error", "errors": ["Could not save your request. Please try again later."]}), 500

    # ── Notify via Telegram ───────────────────────────────────
    try:
        text = build_schedule_message(data, request_id, estimated_total)
        send_telegram_message(text)
    except Exception as e:
        # Don't fail the whole request just because Telegram is down
        logger.error("Failed to send Telegram estimate message: %s", e)

    return jsonify({
        "status": "ok",
        "message": "Request received! We'll be in touch within one business day.",
        "request_id": request_id,
    })


@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')


@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms_of_service.html')


if __name__ == '__main__':
    app.run(debug=True)