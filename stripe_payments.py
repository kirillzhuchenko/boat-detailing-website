"""
stripe_payments.py — Async Stripe Checkout Session helper
----------------------------------------------------------
Creates a single-use Stripe Checkout Session for the balance
due on a final invoice.

Requirements:
    pip install --upgrade stripe   # must be v13.0.1+ for name_collection support
    pip install asgiref            # for Flask async support

Environment variables:
    STRIPE_SECRET_KEY   — sk_live_... or sk_test_...
    STRIPE_SUCCESS_URL  — full URL to redirect after payment  (optional, default shown below)
    STRIPE_CANCEL_URL   — full URL to redirect on cancellation (optional, default shown below)
    BASE_URL            — your public domain, e.g. https://bluewateryacht.com
"""

import os
import logging
import stripe

logger = logging.getLogger(__name__)

# ── Stripe configuration ──────────────────────────────────────────────────────
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# Pin to API version 2025-09-30.clover — required for name_collection support.
stripe.api_version = "2025-09-30.clover"

# Configure the async HTTP client (requires stripe[async] / httpx)
stripe.default_http_client = stripe.HTTPXClient()

MOBILIZATION_FEE = 150.0   # keep in sync with app.py / database.py
BASE_URL         = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")


# ── Public interface ──────────────────────────────────────────────────────────

async def create_invoice_checkout_session(
    *,
    request_id:     int,
    invoice_num:    str,
    final_total:    float,
    amount_paid:    float,
    customer_name:  str,
    customer_email: str,
) -> stripe.checkout.Session:
    """
    Create a single-use Stripe Checkout Session for the outstanding
    balance on a final invoice.

    Balance calculation
    -------------------
        balance_due = final_total − mobilization_fee − amount_paid

    The mobilization fee ($150) was already collected as a deposit and
    is credited here automatically.

    Name collection
    ---------------
    Uses the name_collection parameter (API v2025-09-30.clover+) to
    explicitly prompt the customer for their full name before payment.
    The collected name is available at:
        session.collected_information.customer_details.individual_name

    Returns
    -------
    stripe.checkout.Session
        Access .url for the hosted payment page, .id to store in the DB.

    Raises
    ------
    stripe.error.StripeError — on any API-level failure.
    ValueError               — if the computed balance is zero or negative.
    """
    balance_due = max(0.0, final_total - MOBILIZATION_FEE - amount_paid)

    if balance_due <= 0:
        raise ValueError(
            f"Invoice {invoice_num} has no outstanding balance "
            f"(final_total={final_total:.2f}, mobilization={MOBILIZATION_FEE:.2f}, "
            f"amount_paid={amount_paid:.2f})."
        )

    # Stripe amounts are in the smallest currency unit (cents for USD).
    amount_cents = _dollars_to_cents(balance_due)

    success_url = (
        os.environ.get("STRIPE_SUCCESS_URL")
        or f"{BASE_URL}/invoice/{request_id}/paid?session_id={{CHECKOUT_SESSION_ID}}"
    )
    cancel_url = (
        os.environ.get("STRIPE_CANCEL_URL")
        or f"{BASE_URL}/invoice/{request_id}/final"
    )

    session = await stripe.checkout.Session.create_async(
        # ── Payment mode ────────────────────────────────────────────────────
        mode="payment",

        # ── What the customer is paying for ─────────────────────────────────
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": f"Bluewater Yacht Detailing — {invoice_num}",
                        "description": (
                            f"This is the final invoice for services rendered by IP ZHUCHENKO LLC (Bluewater Yacht Detailing). "
                            f"The Crew Mobilization Fee of ${MOBILIZATION_FEE:.0f} paid at booking has been "
                            f"credited toward this total. Any scope changes were discussed and approved by the "
                            f"customer prior to completion. Payment is due upon receipt."
                        ),
                    },
                },
            }
        ],

        # ── Pre-fill customer email on the Stripe-hosted page ────────────────
        customer_email=customer_email,

        # ── Redirect URLs ────────────────────────────────────────────────────
        success_url=success_url,
        cancel_url=cancel_url,

        # ── Collect customer's full name (requires API v2025-09-30.clover+) ──
        # Shows a "Full name" field on the checkout page before card entry.
        # Collected value is stored in:
        #   session.collected_information.customer_details.individual_name
        name_collection={
            "individual": {
                "enabled": True,
                "optional": False,   # "required" | "optional"
            }
        },

        # ── Metadata stored on the Stripe object ─────────────────────────────
        metadata={
            "request_id":     str(request_id),
            "invoice_num":    invoice_num,
            "balance_due":    f"{balance_due:.2f}",
            "customer_name":  customer_name,
        },

        # ── Payment method options ────────────────────────────────────────────
        payment_method_types=["card"],

        # ── Collect billing address (improves card auth rates) ────────────────
        billing_address_collection="auto",
    )

    logger.info(
        "Stripe Checkout Session created: id=%s invoice=%s amount=$%.2f url=%s",
        session.id, invoice_num, balance_due, session.url,
    )
    return session


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dollars_to_cents(amount: float) -> int:
    """Convert a dollar float to the integer cent value Stripe expects."""
    return round(amount * 100)


