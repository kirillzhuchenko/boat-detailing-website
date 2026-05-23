"""
stripe_payments.py — Async Stripe Checkout Session helper
----------------------------------------------------------
Creates a single-use Stripe Checkout Session for the balance
due on a final invoice (tax-inclusive).

Environment variables:
    STRIPE_SECRET_KEY   — sk_live_... or sk_test_...
    STRIPE_SUCCESS_URL  — full URL to redirect after payment  (optional)
    STRIPE_CANCEL_URL   — full URL to redirect on cancellation (optional)
    BASE_URL            — your public domain, e.g. https://bluewateryacht.com
"""

import os
import logging
import stripe

logger = logging.getLogger(__name__)

stripe.api_key     = os.environ.get("STRIPE_SECRET_KEY", "")
stripe.api_version = "2025-09-30.clover"
stripe.default_http_client = stripe.HTTPXClient()

MOBILIZATION_FEE = 100.0   # keep in sync with app.py / database.py
DEFAULT_TAX_RATE = 10.4    # fallback when city not in lookup table
BASE_URL         = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")


async def create_invoice_checkout_session(
    *,
    request_id:     int,
    invoice_num:    str,
    final_total:    float,        # pre-tax service total
    amount_paid:    float,
    customer_name:  str,
    customer_email: str,
    tax_rate:       float = DEFAULT_TAX_RATE,
) -> stripe.checkout.Session:
    """
    Create a single-use Stripe Checkout Session for the outstanding
    tax-inclusive balance on a final invoice.

    Balance calculation
    -------------------
        tax        = final_total × tax_rate / 100
        grand      = final_total + tax
        mob_credit = MOBILIZATION_FEE × (1 + tax_rate / 100)
        balance    = grand − mob_credit − amount_paid

    The mobilization fee was collected at booking including tax, so the
    full tax-inclusive deposit is credited automatically.
    """
    tax        = final_total * (tax_rate / 100.0)
    grand      = final_total + tax
    mob_credit = MOBILIZATION_FEE * (1.0 + tax_rate / 100.0)
    balance_due = max(0.0, grand - mob_credit - amount_paid)

    if balance_due <= 0:
        raise ValueError(
            f"Invoice {invoice_num} has no outstanding balance "
            f"(final_total={final_total:.2f}, tax_rate={tax_rate:.2f}%, "
            f"mob_credit={mob_credit:.2f}, amount_paid={amount_paid:.2f})."
        )

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
        mode="payment",
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": f"Bluewater Yacht Detailing — {invoice_num}",
                        "description": (
                            f"Final invoice balance (includes {tax_rate:.2f}% sales tax). "
                            f"The Crew Mobilization Fee of ${MOBILIZATION_FEE:.0f} paid at "
                            f"booking has been credited toward this total."
                        ),
                    },
                },
            }
        ],
        customer_email=customer_email,
        success_url=success_url,
        cancel_url=cancel_url,
        name_collection={
            "individual": {
                "enabled": True,
                "optional": False,
            }
        },
        metadata={
            "request_id":     str(request_id),
            "invoice_num":    invoice_num,
            "balance_due":    f"{balance_due:.2f}",
            "tax_rate":       f"{tax_rate:.2f}",
            "customer_name":  customer_name,
        },
        payment_method_types=["card"],
        billing_address_collection="auto",
    )

    logger.info(
        "Stripe Checkout Session created: id=%s invoice=%s amount=$%.2f url=%s",
        session.id, invoice_num, balance_due, session.url,
    )
    return session


def _dollars_to_cents(amount: float) -> int:
    return round(amount * 100)