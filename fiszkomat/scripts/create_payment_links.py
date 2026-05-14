"""Create Stripe Payment Links for the existing farmakologia decks.

LIVE-MONEY SCRIPT. Read this before running:
  - Uses the LIVE STRIPE_SECRET from fiszkomat/.env.
  - Creates real products + prices + payment links on the operator's
    Stripe account (acct_1TWdPL6tmZVUSc0E, PLN, charges + payouts ENABLED).
  - Idempotency: re-running creates duplicates. If you re-run, deactivate
    the old products/links in the Stripe dashboard first.
  - Customers are asked for their email at Stripe Checkout. Stripe
    emails the receipt; operator gets a "payment succeeded" notification
    with the customer's email. Operator manually emails the .apkg back.
  - Pre-existing decks the links point to:
      test_docs/out/zaj08.apkg  (Układ Oddechowy)
      test_docs/out/zaj13.apkg  (Toksykologia)

Run:
  cd fiszkomat
  source .venv/bin/activate
  python scripts/create_payment_links.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    import stripe
except ImportError:
    print("error: stripe SDK not installed. run `pip install stripe`.")
    sys.exit(1)

stripe.api_key = os.environ["STRIPE_SECRET"]
SUPPORT_EMAIL = os.environ.get("FISZKOMAT_SUPPORT_EMAIL", "kontakt@fiszkomat.pl")

# Sanity-check key shape before any write call.
if not stripe.api_key.startswith("sk_live_"):
    print(f"warn: STRIPE_SECRET is not a live key ({stripe.api_key[:8]}...).")
    if input("continue anyway? [y/N] ").strip().lower() != "y":
        sys.exit(1)


DECKS = [
    {
        "slug": "zaj08-uklad-oddechowy",
        "name": "Fiszki Anki — Farmakologia ZAJĘCIA 8 (Układ Oddechowy)",
        "description": (
            "Talia Anki z farmakologii układu oddechowego (ZAJĘCIA 8). "
            "Pełny zestaw fiszek: grupa leków, mechanizm, wskazania, "
            "przeciwwskazania. Format .apkg — działa w Anki Desktop, "
            "AnkiWeb, AnkiDroid i AnkiMobile. Po opłaceniu plik wysyłamy "
            "na podany adres e-mail w ciągu 24 godzin."
        ),
        "amount_grosze": 500,  # 5 PLN
        "deck_file": "zaj08.apkg",
    },
    {
        "slug": "zaj13-toksykologia",
        "name": "Fiszki Anki — Farmakologia ZAJĘCIA 13 (Toksykologia)",
        "description": (
            "Talia Anki z toksykologii — mechanizmy działania trucizn "
            "i postępowanie w zatruciach (ZAJĘCIA 13). Pełny zestaw "
            "fiszek: substancja, mechanizm zatrucia, leczenie, odtrutki. "
            "Format .apkg — działa w Anki Desktop, AnkiWeb, AnkiDroid "
            "i AnkiMobile. Po opłaceniu plik wysyłamy na podany adres "
            "e-mail w ciągu 24 godzin."
        ),
        "amount_grosze": 500,  # 5 PLN
        "deck_file": "zaj13.apkg",
    },
]

THANK_YOU = (
    "Dziękujemy za zakup! Talia Anki zostanie wysłana na podany "
    "podczas płatności adres e-mail w ciągu 24 godzin. "
    f"W razie problemów napisz na {SUPPORT_EMAIL}."
)


def main() -> None:
    results = []
    for d in DECKS:
        prod = stripe.Product.create(
            name=d["name"],
            description=d["description"],
            metadata={"slug": d["slug"], "deck_file": d["deck_file"]},
        )
        price = stripe.Price.create(
            unit_amount=d["amount_grosze"],
            currency="pln",
            product=prod.id,
        )
        link = stripe.PaymentLink.create(
            line_items=[{"price": price.id, "quantity": 1}],
            after_completion={
                "type": "hosted_confirmation",
                "hosted_confirmation": {"custom_message": THANK_YOU},
            },
            metadata={"slug": d["slug"], "deck_file": d["deck_file"]},
        )
        results.append(
            {
                "slug": d["slug"],
                "product": prod.id,
                "price": price.id,
                "payment_link": link.id,
                "url": link.url,
            }
        )

    print()
    print("=" * 70)
    print("CREATED — share these URLs to start collecting payments")
    print("=" * 70)
    for r in results:
        print()
        print(f"  {r['slug']}")
        print(f"    product:      {r['product']}")
        print(f"    price:        {r['price']}")
        print(f"    payment_link: {r['payment_link']}")
        print(f"    URL:          {r['url']}")
    print()
    print("Next: share the URL(s) in target channels (WMS Facebook groups,")
    print("year-level chats). When a customer pays, Stripe sends you their")
    print("email — reply with the .apkg attached from test_docs/out/.")


if __name__ == "__main__":
    main()
