import os
import logging
from flask import Flask, render_template, request, jsonify, abort
from notifier import send_telegram_notification
from service_notifier import send_telegram_service
from database import init_db, save_estimate, get_request, update_status
from datetime import date
from database import (set_deposit_paid, record_payment,
                      set_final_invoice_total, get_payment_summary)
from dotenv import load_dotenv

load_dotenv()

ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme")
MOBILIZATION_FEE = 150.0


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.ERROR, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

GALLERY_FOLDER = os.path.join(app.static_folder, "images", "gallery")
ALLOWED_EXTENSIONS = {".jpg", ".png"}


# ── DB init on startup ────────────────────────────────────────────────────────
with app.app_context():
    init_db()   # creates tables + runs column migrations; safe to call every time


"""
app_stripe_routes.py
--------------------
Async Flask routes to append / merge into app.py.

Prerequisites
-------------
1.  pip install "stripe[async]"
2.  Add STRIPE_SECRET_KEY (and optionally BASE_URL) to your .env
3.  Append the two migration entries to _MIGRATIONS in database.py:
        ("stripe_session_id",  "TEXT"),
        ("stripe_session_url", "TEXT"),
4.  Append the three helper functions from database_stripe_additions.py
    into database.py (save_stripe_session, get_stripe_session, clear_stripe_session).
5.  Add these imports to app.py:
        import asyncio
        import stripe as stripe_lib
        from stripe_payments import create_invoice_checkout_session
        from database import save_stripe_session, get_stripe_session, clear_stripe_session
6.  Copy the three routes below into app.py.
"""

import asyncio
import stripe as stripe_lib
from flask import redirect, jsonify
from stripe_payments import create_invoice_checkout_session
from database import (
    get_request, save_stripe_session,
    get_stripe_session, clear_stripe_session,
)

# ── Card payment: create / reuse Stripe Checkout Session ─────────────────────

@app.route("/invoice/<int:request_id>/checkout")
async def stripe_checkout(request_id):
    """
    Async route that issues (or reuses) a Stripe Checkout Session for the
    balance due on a final invoice, then redirects the customer to Stripe.

    Flow
    ----
    1.  Load the job from the DB — 404 if not found or no final total set.
    2.  Check whether a live Stripe session is already cached for this job.
        If yes, redirect straight to it (avoids creating duplicate sessions
        when the customer reloads the page or clicks the button twice).
    3.  If no cached session (or the cached one was expired/paid), create a
        new Checkout Session via the Stripe API (async).
    4.  Persist the new session ID + URL in the DB, then redirect.
    """
    req = dict(get_request(request_id) or abort(404))

    final_total = req.get("final_invoice_total")
    if not final_total:
        abort(404, description=(
            f"No final invoice total set for job #{request_id}. "
            "Use /admin/invoice/<id>/set-total first."
        ))

    amount_paid    = req.get("amount_paid") or 0.0
    payment_status = req.get("payment_status") or "unpaid"

    # Already fully paid — nothing to collect.
    if payment_status == "paid_in_full":
        return redirect(f"/invoice/{request_id}/final")

    # ── Try to reuse an existing live session ────────────────────────────────
    cached_session_id, cached_session_url = get_stripe_session(request_id)

    if cached_session_id and cached_session_url:
        # Verify the session is still open (not expired / already paid).
        try:
            existing = await stripe_lib.checkout.Session.retrieve_async(cached_session_id)
            if existing.status == "open":
                logger.info(
                    "Reusing existing Stripe session %s for job #%s",
                    cached_session_id, request_id,
                )
                return redirect(existing.url)
        except stripe_lib.error.StripeError as e:
            # Session gone or invalid — fall through to create a new one.
            logger.warning(
                "Cached Stripe session %s could not be retrieved: %s — creating new one.",
                cached_session_id, e,
            )
        # Clear the stale cache before creating a fresh session.
        clear_stripe_session(request_id)

    # ── Create a new Checkout Session ────────────────────────────────────────
    customer_name  = f"{req.get('first_name', '')} {req.get('last_name', '')}".strip()
    customer_email = req.get("email", "")
    invoice_num    = f"BWD-{req['id']:04d}-F"

    try:
        session = await create_invoice_checkout_session(
            request_id     = request_id,
            invoice_num    = invoice_num,
            final_total    = final_total,
            amount_paid    = amount_paid,
            customer_name  = customer_name,
            customer_email = customer_email,
        )
    except ValueError as e:
        # Balance is zero — redirect back to the invoice.
        logger.info("Checkout skipped for job #%s: %s", request_id, e)
        return redirect(f"/invoice/{request_id}/final")
    except stripe_lib.error.StripeError as e:
        logger.error("Stripe error creating session for job #%s: %s", request_id, e)
        abort(502, description="Payment gateway error — please try again or contact us.")

    # Persist session so re-visits reuse the same Stripe URL.
    save_stripe_session(request_id, session.id, session.url)

    return redirect(session.url, code=303)


# ── Stripe webhook: keep payment status in sync ───────────────────────────────

@app.route("/stripe/webhook", methods=["POST"])
async def stripe_webhook():
    """
    Receive Stripe webhook events and update the job record accordingly.

    Events handled
    --------------
    checkout.session.completed  — record the payment and mark status.
    checkout.session.expired    — clear cached session so a new one is created.

    Set up in the Stripe Dashboard (or CLI):
        stripe listen --forward-to localhost:5000/stripe/webhook

    Required env var:
        STRIPE_WEBHOOK_SECRET — whsec_...
    """
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret     = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe_lib.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        logger.warning("Stripe webhook: invalid payload")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe_lib.error.SignatureVerificationError:
        logger.warning("Stripe webhook: invalid signature")
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event["type"]
    session    = event["data"]["object"]

    # Pull the job ID we stored in metadata when creating the session.
    request_id_str = (session.get("metadata") or {}).get("request_id")
    if not request_id_str:
        return jsonify({"status": "ignored — no request_id in metadata"}), 200

    request_id = int(request_id_str)

    if event_type == "checkout.session.completed":
        amount_received = (session.get("amount_total") or 0) / 100  # cents → dollars
        payment_method  = "card"  # Checkout Sessions use card

        logger.info(
            "Stripe webhook: checkout.session.completed for job #%s — $%.2f",
            request_id, amount_received,
        )

        try:
            result = record_payment(
                request_id,
                amount_received,
                method = payment_method,
                notes  = f"Stripe Checkout Session {session['id']}",
            )
            # Clear the cached session — it's been paid.
            clear_stripe_session(request_id)

            req = dict(get_request(request_id))
            _notify_payment(req, amount_received, payment_method, None, result)

        except (LookupError, ValueError) as e:
            logger.error("webhook: failed to record payment for job #%s: %s", request_id, e)
            return jsonify({"error": str(e)}), 500

    elif event_type == "checkout.session.expired":
        logger.info(
            "Stripe webhook: session expired for job #%s — clearing cache", request_id
        )
        try:
            clear_stripe_session(request_id)
        except LookupError:
            pass  # Job may already be gone; ignore.

    return jsonify({"status": "ok"}), 200


# ── Payment confirmed landing page ────────────────────────────────────────────

@app.route("/invoice/<int:request_id>/paid")
def payment_confirmed(request_id):
    """
    Success page shown after Stripe redirects the customer back.
    Renders the final invoice with the updated payment status.

    Stripe appends ?session_id=cs_... automatically via the success_url
    template variable {CHECKOUT_SESSION_ID}; we use it for display only.
    """
    req = dict(get_request(request_id) or abort(404))
    return render_template(
        "payment_confirmed.html",
        req        = req,
        invoice_num= f"BWD-{req['id']:04d}-F",
        session_id = request.args.get("session_id", ""),
    )



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


# ── Admin notification helpers ────────────────────────────────────────────────
def _job_header(req) -> str:
    """One-line vessel + customer summary reused across all admin messages."""
    name    = f"{req.get('first_name', '')} {req.get('last_name', '')}".strip()
    vessel  = f"{req.get('vessel_year', '')} {req.get('vessel_make', '')} {req.get('vessel_model', '')}".strip()
    loa     = req.get('loa_ft')
    marina  = req.get('marina') or '—'
    loa_str = f" ({int(loa)} ft)" if loa else ""
    return (
        f"👤 *{name}*\n"
        f"🚢 {vessel}{loa_str}\n"
        f"📍 {marina}"
    )


def _notify_status_change(req, old_status: str, new_status: str,
                           changed_by: str, notes: str | None) -> None:
    STATUS_EMOJI = {
        "new":         "🆕",
        "contacted":   "📞",
        "scheduled":   "📅",
        "in_progress": "🔧",
        "completed":   "✅",
        "invoiced":    "🧾",
        "cancelled":   "❌",
    }
    emoji = STATUS_EMOJI.get(new_status, "🔄")
    lines = [
        f"{emoji} *Status Update — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req),
        "",
        f"📊 `{old_status}` → `{new_status}`",
        f"👤 Changed by: {changed_by}",
    ]
    if notes:
        lines.append(f"📝 {notes}")
    _send(lines)


def _notify_deposit_paid(req, method: str) -> None:
    lines = [
        f"💰 *Deposit Collected — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req),
        "",
        f"💵 ${MOBILIZATION_FEE:.2f} via {method}",
        "🔐 Appointment secured",
    ]
    _send(lines)


def _notify_invoice_total_set(req, total: float) -> None:
    balance = max(0.0, total - MOBILIZATION_FEE)
    lines = [
        f"🧾 *Invoice Total Set — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req),
        "",
        f"💰 Final Total:      ${total:.2f}",
        f"💳 Deposit Credit:  −${MOBILIZATION_FEE:.2f}",
        f"⚖️  Balance Due:      ${balance:.2f}",
    ]
    _send(lines)


def _notify_payment(req, amount: float, method: str,
                    notes: str | None, result: dict) -> None:
    status      = result["status"]
    amount_paid = result["amount_paid"]
    balance     = result["balance_due"]

    if status == "paid_in_full":
        status_line = "✅ *Paid in Full — balance cleared*"
    else:
        status_line = f"⚖️  Balance Remaining: ${balance:.2f}"

    lines = [
        f"💳 *Payment Recorded — BWD\\-{req['id']:04d}*",
        "━━━━━━━━━━━━━━━━━━━━",
        _job_header(req),
        "",
        f"💵 \\+${amount:.2f} via {method}",
        f"📊 Total Paid: ${amount_paid:.2f}",
        status_line,
    ]
    if notes:
        lines.append(f"📝 {notes}")
    _send(lines)


def _send(lines: list[str]) -> None:
    """Join lines and fire telegram; silently logs on failure."""
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
    first_name = data.get('first_name', '')
    last_name  = data.get('last_name', '')
    phone      = data.get('phone') or '—'
    email      = data.get('email', '')
    loa        = data.get('loa_ft', '?')
    vessel     = f"{data.get('vessel_year','')} {data.get('vessel_make','')} {data.get('vessel_model','')}".strip()
    marina     = data.get('marina', '—')
    total_str  = f"${estimated_total:,.2f}" if estimated_total is not None else "N/A"

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
        send_telegram_service(text)
    except Exception as e:
        logger.error("Failed to send Telegram contact message: %s", e)
        return jsonify({"status": "error", "errors": ["Could not send your message. Please try again later."]}), 500

    return jsonify({"status": "ok", "message": "Thanks! We'll be in touch soon."})


@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.form.to_dict()

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

    estimated_total = None
    raw_total = data.get("estimated_total", "").strip()
    if raw_total:
        try:
            estimated_total = float(raw_total)
        except ValueError:
            pass

    try:
        request_id = save_estimate(data, estimated_total=estimated_total)
    except Exception as e:
        logger.error("Failed to save estimate to DB: %s", e)
        return jsonify({"status": "error", "errors": ["Could not save your request. Please try again later."]}), 500

    try:
        text = build_schedule_message(data, request_id, estimated_total)
        send_telegram_service(text)
    except Exception as e:
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
        fm = {'light': 1.0, 'mod': 1.5, 'exces': 2.0}.get(req.get('fouling_level'), 1.0)
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
            a = qty * 50; subtotal += a
            line_items.append(
                {'svc': 'Anode Replacement', 'desc': f"{qty}× {req.get('anode_material') or 'standard'} anodes",
                 'qty': f'{qty} ea', 'rate': '$50/ea', 'amt': a})

    if req.get('svc_prop') and req.get('prop_inst_required') == 'yes':
        pt = req.get('prop_type')
        diam = float(req.get('prop_diam') or 0)
        pr = {'prop_fixed': 25, 'prop_fold': 30}.get(pt)
        if pr and diam:
            a = pr * diam; subtotal += a
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


@app.route('/invoice/<int:request_id>/deposit')
def mobilization_invoice(request_id):
    req = dict(get_request(request_id) or abort(404))
    return render_template(
        'invoice_mobilization_auto.html',
        req      = req,
        paid     = bool(req.get('deposit_paid')),
        inv_date = date.today().strftime('%B %d, %Y'),
        inv_num  = f"BWD-{req['id']:04d}-D",
        job_num  = f"BWD-{req['id']:04d}",
    )

# ── Final (post-service) invoice ─────────────────────────────────────────────
@app.route('/invoice/<int:request_id>/final')
def generate_final_invoice(request_id):
    """
    Final invoice rendered after work is completed.
    Requires final_invoice_total to have been set via /admin/invoice/<id>/set-total.
    Reflects actual work performed, deposit credit, and any payments already received.
    """
    req = dict(get_request(request_id) or abort(404))

    final_total = req.get('final_invoice_total')
    if not final_total:
        abort(404, description=f"No final invoice total set for job #{request_id}. "
                               "Use /admin/invoice/<id>/set-total first.")

    amount_paid    = req.get('amount_paid') or 0.0
    payment_status = req.get('payment_status') or 'unpaid'
    balance_due    = max(0.0, final_total - MOBILIZATION_FEE - amount_paid)

    # ── Rebuild line items from form data (same logic as estimate) ───────────
    # These appear as a "services performed" reference; the totals block
    # is driven entirely by final_total / amount_paid from the DB.
    line_items, subtotal = [], 0.0
    loa = float(req.get('loa_ft') or 0)

    if req.get('svc_hull'):
        rate = 5 if loa < 40 else (8 if loa < 80 else 12)
        base = loa * rate
        fm   = {'light': 1.0, 'mod': 1.5, 'exces': 2.0}.get(req.get('fouling_level'), 1.0)
        fd   = {'Monthly': 0.25, 'Bi-monthly': 0.15}.get(req.get('frequency'), 0.0)
        amt  = base * fm * (1 - fd)
        subtotal += amt
        desc = f"{int(loa)} ft LOA"
        if fm > 1: desc += f", +{int((fm - 1) * 100)}% fouling surcharge"
        if fd > 0: desc += f", −{int(fd * 100)}% recurring discount"
        line_items.append({
            'svc': 'Hull Cleaning', 'desc': desc,
            'qty': f'{int(loa)} ft', 'rate': f'${rate}/ft', 'amt': amt,
        })

    if req.get('svc_anode') and req.get('anode_required') == 'yes':
        qty = sum(filter(None, [
            req.get('qty_keel'), req.get('qty_shaft'),
            req.get('qty_rudder'), req.get('qty_other'),
        ])) or 0
        if qty:
            a = qty * 50; subtotal += a
            line_items.append({
                'svc': 'Anode Replacement',
                'desc': f"{qty}× {req.get('anode_material') or 'standard'} anodes",
                'qty': f'{qty} ea', 'rate': '$50/ea', 'amt': a,
            })

    if req.get('svc_prop') and req.get('prop_inst_required') == 'yes':
        pt   = req.get('prop_type')
        diam = float(req.get('prop_diam') or 0)
        pr   = {'prop_fixed': 25, 'prop_fold': 30}.get(pt)
        if pr and diam:
            a = pr * diam; subtotal += a
            lb = 'Fixed-Blade' if pt == 'prop_fixed' else 'Folding'
            line_items.append({
                'svc': 'Propeller Service',
                'desc': f'{int(diam)}" {lb} — removal & installation',
                'qty': f'{int(diam)}"', 'rate': f'${pr}/in', 'amt': a,
            })
        else:
            line_items.append({
                'svc': 'Propeller Service',
                'desc': 'Custom / contact for pricing',
                'qty': '—', 'rate': 'TBD', 'amt': None,
            })

    if req.get('svc_rudder'):
        line_items.append({
            'svc': 'Rudder Inspection',
            'desc': 'Visual inspection & written condition report',
            'qty': '—', 'rate': 'TBD', 'amt': None,
        })

    if req.get('svc_other'):
        line_items.append({
            'svc': 'Additional Service',
            'desc': req.get('svc_other_detail') or '—',
            'qty': '—', 'rate': 'TBD', 'amt': None,
        })

    if req.get('private_dock') == 'yes':
        subtotal += 25
        line_items.append({
            'svc': 'Private Dock Surcharge', 'desc': 'Remote/private dock access',
            'qty': '—', 'rate': '$25', 'amt': 25,
        })

    if req.get('exp_service') == 'yes':
        subtotal += 600
        line_items.append({
            'svc': 'Expedited Service', 'desc': 'Priority scheduling',
            'qty': '—', 'rate': '$600', 'amt': 600,
        })

    return render_template(
        'invoice_final_auto.html',
        req            = req,
        line_items     = line_items,
        estimated_total= req.get('estimated_total'),   # original estimate for comparison
        final_total    = final_total,
        mobilization_fee = MOBILIZATION_FEE,
        amount_paid    = amount_paid,
        balance_due    = balance_due,
        payment_status = payment_status,
        invoice_date   = date.today().strftime('%B %d, %Y'),
        invoice_num    = f"BWD-{req['id']:04d}-F",
    )


def _check_admin(req):
    """Return True if the request carries the correct admin key."""
    return req.form.get("key") == ADMIN_KEY


# ── Update job status ─────────────────────────────────────────────────────────
@app.route("/admin/request/<int:request_id>/status", methods=["POST"])
def admin_update_status(request_id):
    """
    Move a job to a new status and notify via Telegram.

    POST params:
        key         — admin password
        status      — new status (new | contacted | scheduled | in_progress |
                                   completed | invoiced | cancelled)
        changed_by  — staff name (optional, default: 'staff')
        notes       — optional context for the transition

    Example:
        curl -X POST http://localhost:5000/admin/request/5/status \\
             -d "key=changeme&status=completed&changed_by=Alex&notes=Clean hull, replaced 3 anodes"
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    new_status = request.form.get("status", "").strip()
    changed_by = request.form.get("changed_by", "staff").strip()
    notes      = request.form.get("notes", "").strip() or None

    try:
        req = dict(get_request(request_id) or abort(404))
        old_status = req["status"]
        update_status(request_id, new_status, changed_by=changed_by, log_notes=notes)
        _notify_status_change(req, old_status, new_status, changed_by, notes)
        return jsonify({
            "status":     "ok",
            "request_id": request_id,
            "old_status": old_status,
            "new_status": new_status,
            "message":    f"Status updated: {old_status} → {new_status}",
        })
    except (LookupError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


# ── Set final invoice total ───────────────────────────────────────────────────
@app.route("/admin/invoice/<int:request_id>/set-total", methods=["POST"])
def admin_set_total(request_id):
    """
    Set the confirmed job total before recording payment.

    POST params:
        key    — admin password
        total  — final invoice amount in dollars (e.g. 340.00)

    Example:
        curl -X POST http://localhost:5000/admin/invoice/5/set-total \\
             -d "key=changeme&total=340"
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        total = float(request.form.get("total", 0))
        req   = dict(get_request(request_id) or abort(404))
        set_final_invoice_total(request_id, total)
        _notify_invoice_total_set(req, total)
        return jsonify({
            "status":              "ok",
            "request_id":          request_id,
            "final_invoice_total": total,
            "message":             f"Final total set to ${total:.2f} for job #{request_id}.",
        })
    except (LookupError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


# ── Record a payment ──────────────────────────────────────────────────────────
@app.route("/admin/invoice/<int:request_id>/pay", methods=["POST"])
def admin_record_payment(request_id):
    """
    Record a full or partial payment toward the final invoice.
    Can be called multiple times — amounts accumulate.

    POST params:
        key     — admin password
        amount  — amount collected this time (e.g. 95.00)
        method  — venmo | zelle | cash | card | check | other
        notes   — optional free-text note

    Example (full payment):
        curl -X POST http://localhost:5000/admin/invoice/5/pay \\
             -d "key=changeme&amount=190&method=venmo"

    Example (partial):
        curl -X POST http://localhost:5000/admin/invoice/5/pay \\
             -d "key=changeme&amount=100&method=cash&notes=first installment"
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        amount = float(request.form.get("amount", 0))
        method = request.form.get("method", "unspecified")
        notes  = request.form.get("notes", "").strip() or None
        req    = dict(get_request(request_id) or abort(404))

        result = record_payment(request_id, amount, method=method, notes=notes)
        _notify_payment(req, amount, method, notes, result)

        return jsonify({
            "status":         "ok",
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


# ── Check payment status ──────────────────────────────────────────────────────
@app.route("/admin/invoice/<int:request_id>/status", methods=["GET"])
def admin_payment_status(request_id):
    """
    Get the current payment summary for any job.

    Query params:
        key — admin password

    Example:
        curl "http://localhost:5000/admin/invoice/5/status?key=changeme"
    """
    if request.args.get("key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        summary = get_payment_summary(request_id)
        return jsonify(summary)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


# ── Mark deposit paid ─────────────────────────────────────────────────────────
@app.route("/admin/deposit/<int:request_id>/mark-paid", methods=["POST"])
def mark_deposit_paid(request_id):
    """
    Mark the $150 mobilization fee as collected.

    POST params:
        key    — admin password
        method — payment method

    Example:
        curl -X POST http://localhost:5000/admin/deposit/5/mark-paid \\
             -d "key=changeme&method=venmo"
    """
    if not _check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    method = request.form.get("method", "unspecified")
    try:
        req = dict(get_request(request_id) or abort(404))
        set_deposit_paid(request_id, method=method)
        _notify_deposit_paid(req, method)
        return jsonify({
            "status":     "ok",
            "request_id": request_id,
            "message":    f"Deposit marked paid via {method} for job #{request_id}.",
        })
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


if __name__ == '__main__':
    app.run(debug=True)