import os
import logging
from flask import Flask, render_template, request, jsonify
from notifier import send_telegram_message


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.ERROR, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

GALLERY_FOLDER = os.path.join(app.static_folder, "images", "gallery")
ALLOWED_EXTENSIONS = {".jpg", ".png"}

@app.route('/')

def home():

    images = get_gallery_images()
    return render_template('index.html', images=images)


def get_gallery_images():
    """
    Scans GALLERY_FOLDER and returns a sorted list of image filenames.
    Only .jpg and .png files are included.
    Files are sorted alphabetically by filename (A → Z).
    """
    if not os.path.exists(GALLERY_FOLDER):
        os.makedirs(GALLERY_FOLDER)  # Create the folder if it doesn't exist yet
        return []

    images = [
        f for f in os.listdir(GALLERY_FOLDER)
        if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS
    ]

    images.sort(key=lambda f: f.lower())  # Case-insensitive alphabetical sort
    return images


def build_contact_message(first_name, last_name, phone, email, message):
    return (
        f"📬 *New Contact Form*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:* {first_name} {last_name}\n"
        f"📧 *Email:* {email}\n"
        f"📞 *Phone:* {phone or '—'}\n\n"
        f"💬 *Message:*\n{message}"
    )

def build_schedule_message(data):
    first_name = data.get('first_name', '')
    last_name  = data.get('last_name', '')
    phone      = data.get('phone') or '—'
    email      = data.get('email', '')

    return (
        f"📬 *New Service Request*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Name:* {first_name} {last_name}\n"
        f"📧 *Email:* {email}\n"
        f"📞 *Phone:* {phone}\n"
    )

@app.route("/contact", methods=["POST"])
def contact():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    message = request.form.get("message", "").strip()

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


@app.route("/schedule", methods=["POST"])
def schedule():
    data = request.form.to_dict()

    required = {
        'first_name': 'First name',
        'last_name': 'Last name',
        'email': 'Email',
        'phone': 'Phone',
        'vessel_name': 'Vessel name',
        'vessel_year': 'Vessel year',
        'vessel_make': 'Vessel make',
        'vessel_model': 'Vessel model',
        'vessel_type': 'Vessel type',
        'hull_material': 'Hull material',
        'loa_ft': 'Length overall',
        'marina': 'Marina / dock',
        'anode_required': 'Anode required',
        'paintType': 'Paint type',
        'paintCond': 'Paint condition',
    }
    errors = [f"{label} is required." for field, label in required.items() if not data.get(field)]

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    try:
        text = build_schedule_message(data)
        send_telegram_message(text)
    except Exception as e:
        logger.error("Failed to send Telegram schedule message: %s", e)
        return jsonify({"status": "error", "errors": ["Could not submit your request. Please try again later."]}), 500

    return jsonify({"status": "ok", "message": "Request received! We'll be in touch within one business day."})

@app.route('/privacy-policy')
def privacy_policy():
   return render_template('privacy_policy.html')

@app.route('/terms-of-service')
def terms_of_service():
   return render_template('terms_of_service.html')

if __name__ == '__main__':
    app.run(debug=True)