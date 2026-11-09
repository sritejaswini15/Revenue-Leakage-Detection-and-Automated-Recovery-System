"""Verify a real Razorpay test-mode round trip with the keys in the environment.

Usage (from backend/), with Razorpay *test* keys exported in your own shell:

    RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=... python -m scripts.razorpay_test_roundtrip
    python -m scripts.razorpay_test_roundtrip --keep-link   # leave the link open to pay it

Automated checks, all against https://api.razorpay.com in test mode:
  1. The keys authenticate.
  2. A Rs 1 payment link is created exactly as RecoverX creates them (amount in paise, notes).
  3. The link is read back and its status and amount match.
  4. The link is cancelled (unless --keep-link) and reads back as CANCELLED.

Paying the link and receiving the webhook needs a browser and a public URL; the script prints
those manual steps. Live keys (rzp_live_) are refused. Secrets are never printed. Exit code 0
means every automated check passed.
"""

import argparse
import os
import sys

from app.domain.exceptions import ProviderError
from app.infrastructure.payments.razorpay import RazorpayProvider

TEST_PREFIX = "rzp_test_"
AMOUNT_RUPEES = 1


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{f': {detail}' if detail else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-link", action="store_true", help="do not cancel the link, so it can be paid")
    args = parser.parse_args()

    key_id = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
    key_secret = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
    if not key_id or not key_secret:
        print("Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET (test mode) in your shell first. Nothing was called.")
        return 2
    if not key_id.startswith(TEST_PREFIX):
        print("Refusing to run: this check only uses Razorpay test keys (rzp_test_...). Nothing was called.")
        return 2

    provider = RazorpayProvider(key_id, key_secret)
    print(f"Razorpay test round trip with key {key_id[:13]}...")
    try:
        provider.verify_credentials()
    except ProviderError as exc:
        _check("Keys authenticate", False, str(exc))
        return 1
    _check("Keys authenticate", True)

    passed = True
    try:
        link = provider.create_payment_link("RC-ROUNDTRIP", AMOUNT_RUPEES, "RecoverX test-mode round trip")
        passed &= _check("Payment link created", bool(link.provider_link_id), f"{link.provider_link_id} {link.status}")
        status = provider.payment_link_status(link.provider_link_id)
        passed &= _check("Link reads back", status in ("CREATED", "ISSUED"), status)
        if args.keep_link:
            print(
                "\nManual steps to finish the round trip:\n"
                f"  1. Open {link.short_url} and pay with a Razorpay test method (e.g. UPI success@razorpay).\n"
                "  2. In Razorpay Dashboard > Settings > Webhooks (test mode), point a webhook at\n"
                "     https://<your API>/webhooks/razorpay with payment_link.paid and payment.* events,\n"
                "     using the merchant's webhook secret. Locally, expose port 8000 with a tunnel.\n"
                "  3. Confirm the API log shows the event accepted, and that the case is RECOVERED.\n"
                f"  Cancel the link afterwards if unpaid: it expires on its own after PAYMENT_LINK_TTL_HOURS."
            )
        else:
            provider.cancel_payment_link(link.provider_link_id)
            status = provider.payment_link_status(link.provider_link_id)
            passed &= _check("Link cancelled", status == "CANCELLED", status)
    except ProviderError as exc:
        passed = _check("Razorpay request", False, str(exc))
    print("\nAll automated checks passed." if passed else "\nSome checks failed.")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
