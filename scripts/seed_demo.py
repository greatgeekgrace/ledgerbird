"""Populate a PayPal sandbox account with realistic freelancer invoices.

Usage: PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... python -m scripts.seed_demo [--reset]
"""
import asyncio
import sys
from datetime import date, timedelta

from app.paypal import PayPal, PayPalError

T = date.today()
D = lambda n: (T - timedelta(days=n)).isoformat()

# (client, email, items, issued_days_ago, terms_days, paid_days_after_issue or None, send?)
DEMO = [
    ("Priya Nair", "priya@northwind-demo.example", [("Landing page copy edit", 6, 85, "HOURS")], 95, 14, 9, True),
    ("Priya Nair", "priya@northwind-demo.example", [("Pricing page redesign", 10, 85, "HOURS")], 62, 14, 12, True),
    ("Priya Nair", "priya@northwind-demo.example", [("Design QA sprint", 8, 85, "HOURS")], 30, 14, 11, True),
    ("Priya Nair", "priya@northwind-demo.example", [("Mobile nav prototype", 9, 85, "HOURS")], 18, 14, None, True),
    ("Marcus Lee", "marcus@blueharbor-demo.example", [("Brand guidelines v1", 1, 1400, "AMOUNT")], 110, 14, 41, True),
    ("Marcus Lee", "marcus@blueharbor-demo.example", [("Social templates (set of 12)", 12, 60, "QUANTITY")], 70, 14, 33, True),
    ("Marcus Lee", "marcus@blueharbor-demo.example", [("Pitch deck redesign", 1, 1850, "AMOUNT")], 37, 14, None, True),
    ("Ana Souza", "ana@kettleco-demo.example", [("E-commerce product photography", 1, 2400, "AMOUNT"), ("Retouching", 30, 15, "QUANTITY")], 55, 14, None, True),
    ("Tom Becker", "tom@ridgeway-demo.example", [("Monthly retainer — September", 1, 900, "AMOUNT")], 33, 30, 28, True),
    ("Tom Becker", "tom@ridgeway-demo.example", [("Monthly retainer — October", 1, 900, "AMOUNT")], 3, 30, None, True),
    ("Dev Patel", "dev@sproutlabs-demo.example", [("Logo exploration", 5, 95, "HOURS")], 0, 14, None, False),
]


async def reset(pp):
    for inv in await pp.list_invoices():
        try:
            if inv["status"] in ("DRAFT", "SCHEDULED"):
                await pp.delete(inv["id"])
            elif inv["status"] in ("SENT", "UNPAID", "PARTIALLY_PAID"):
                await pp.cancel(inv["id"], "Demo reset")
        except PayPalError as e:
            print("skip", inv["id"], e)


async def main():
    pp = PayPal()
    if "--reset" in sys.argv:
        await reset(pp)
    for client, email, items, ago, terms, paid_after, send in DEMO:
        inv = await pp.create_draft(
            client_name=client, client_email=email, currency="USD", due_in_days=terms, invoice_date=D(ago),
            items=[{"name": n, "quantity": q, "unit_price": p, "unit": u} for n, q, p, u in items],
            note="Thank you for the work together!",
        )
        if send:
            await pp.send(inv["id"])
        if paid_after is not None:
            await pp.record_payment(inv["id"], inv["amount"]["value"], "USD", "BANK_TRANSFER", paid_on=D(ago - paid_after))
        print(inv["detail"]["invoice_number"], client, inv["amount"]["value"], "paid" if paid_after is not None else ("sent" if send else "draft"))


if __name__ == "__main__":
    asyncio.run(main())
