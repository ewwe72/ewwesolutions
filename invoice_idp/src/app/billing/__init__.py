"""Billing — Stripe customer + Checkout-Session-based prepaid credit.

V1.3 PAYG model: an Org has a `credit_balance_grosze` integer (PLN
groszy, so 50 = 0,50 PLN). Each successful extraction debits 50
groszy. The user tops up via a Stripe Checkout session — webhook
credits the balance.

`stripe_client` is the only module that imports the Stripe SDK. Routes
import the Protocol, not the concrete client, so the rest of the
codebase stays SDK-agnostic and the Console implementation runs in
tests / dev without a network.
"""
