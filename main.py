import os
import logging
from flask import Flask, render_template, request, jsonify, abort
from notifier import send_telegram_message
from database import init_db, save_estimate, get_request
from datetime import date


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


@app.route('/invoice/<int:request_id>')


def generate_invoice(request_id):
    req = dict(get_request(request_id) or abort(404))
    line_items, subtotal = [], 0.0
    loa = float(req.get('loa_ft') or 0)

    if req.get('svc_hull'):
        rate = 5 if loa < 40 else (8 if loa < 80 else 12)
        base = loa * rate
        fm = {'light': 1.0, 'mod': 1.75, 'exces': 2.5}.get(req.get('fouling_level'), 1.0)
        fd = {'Monthly': 0.25, 'Bi-monthly': 0.15}.get(req.get('frequency'), 0.0)
        amt = base * fm * (1 - fd)
        subtotal += amt
        desc = f"{int(loa)} ft LOA"
        if fm > 1: desc += f", +{int((fm - 1) * 100)}% fouling surcharge"
        if fd > 0: desc += f", −{int(fd * 100)}% recurring discount"
        line_items.append(
            {'svc': 'Hull Cleaning', 'desc': desc, 'qty': f'{int(loa)} ft', 'rate': f'${rate}/ft', 'amt': amt})

    if req.get('svc_anode') and req.get('anode_required') == 'yes':
        qty = sum(
            filter(None, [req.get('qty_keel'), req.get('qty_shaft'), req.get('qty_rudder'), req.get('qty_other')])) or 0
        if qty:
            a = qty * 50;
            subtotal += a
            line_items.append(
                {'svc': 'Anode Replacement', 'desc': f"{qty}× {req.get('anode_material') or 'standard'} anodes",
                 'qty': f'{qty} ea', 'rate': '$50/ea', 'amt': a})

    if req.get('svc_prop') and req.get('prop_inst_required') == 'yes':
        pt = req.get('prop_type')
        diam = float(req.get('prop_diam') or 0)
        pr = {'prop_fixed': 25, 'prop_fold': 30}.get(pt)
        if pr and diam:
            a = pr * diam;
            subtotal += a
            lb = 'Fixed-Blade' if pt == 'prop_fixed' else 'Folding'
            line_items.append({'svc': 'Propeller Service', 'desc': f'{int(diam)}" {lb} — removal & installation',
                               'qty': f'{int(diam)}"', 'rate': f'${pr}/in', 'amt': a})
        else:
            line_items.append(
                {'svc': 'Propeller Service', 'desc': 'Custom / contact for pricing', 'qty': '—', 'rate': 'TBD',
                 'amt': None})

    if req.get('svc_rudder'):
        line_items.append(
            {'svc': 'Rudder Inspection', 'desc': 'Visual inspection & written condition report', 'qty': '—',
             'rate': 'TBD', 'amt': None})

    if req.get('svc_other'):
        line_items.append(
            {'svc': 'Additional Service', 'desc': req.get('svc_other_detail') or '—', 'qty': '—', 'rate': 'TBD',
             'amt': None})


    if req.get('private_dock') == 'yes':
        subtotal += 25
        line_items.append(
            {'svc': 'Private Dock Surcharge', 'desc': 'Remote/private dock access', 'qty': '—', 'rate': '$25',
             'amt': 25})

    if req.get('exp_service') == 'yes':
        subtotal += 600
        line_items.append(
            {'svc': 'Expedited Service', 'desc': 'Priority scheduling', 'qty': '—', 'rate': '$600', 'amt': 600})

    subtotal += 150
    line_items.append({'svc': 'Crew Mobilization Fee', 'desc': 'Applied toward final invoice at completion', 'qty': '—',
                       'rate': '$150', 'amt': 150})

    return render_template(
        'invoice_dark_auto.html',
        req=req, line_items=line_items, subtotal=subtotal,
        balance_due=subtotal - 150,
        invoice_date=date.today().strftime('%B %d, %Y'),
        invoice_num=f"BWD-{req['id']:04d}",
    )


if __name__ == '__main__':
    app.run(debug=True)