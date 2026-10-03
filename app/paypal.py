import asyncio
import os
import time
from datetime import date, timedelta

import httpx

BASE = os.environ.get("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
FINAL = {"PAID", "MARKED_AS_PAID", "CANCELLED", "REFUNDED", "MARKED_AS_REFUNDED"}


class PayPalError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        detail = body.get("details") or []
        msg = body.get("message") or body.get("error_description") or str(body)
        if detail:
            msg += " — " + "; ".join(d.get("description") or d.get("issue", "") for d in detail)
        super().__init__(f"PayPal {status}: {msg}")


class PayPal:
    def __init__(self):
        self.client_id = os.environ["PAYPAL_CLIENT_ID"]
        self.secret = os.environ["PAYPAL_CLIENT_SECRET"]
        self._token = None
        self._exp = 0
        self._final = {}
        self.http = httpx.AsyncClient(base_url=BASE, timeout=30)

    async def token(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await self.http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.secret),
        )
        if r.status_code != 200:
            raise PayPalError(r.status_code, r.json())
        j = r.json()
        self._token, self._exp = j["access_token"], time.time() + j["expires_in"]
        return self._token

    async def call(self, method, path, json=None, params=None):
        headers = {"Authorization": f"Bearer {await self.token()}", "Prefer": "return=representation"}
        if method == "POST" and json is None:
            json = {}
        r = await self.http.request(method, path, json=json, params=params, headers=headers)
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {"message": r.text}
            raise PayPalError(r.status_code, body)
        return r.json() if r.content else {}

    # ---- Invoicing v2 ----

    async def list_invoices(self, pages=4):
        summaries = []
        for page in range(1, pages + 1):
            j = await self.call("GET", "/v2/invoicing/invoices", params={"page": page, "page_size": 100, "total_required": "true", "fields": "all"})
            items = j.get("items", [])
            summaries.extend(items)
            if len(items) < 100:
                break
        sem = asyncio.Semaphore(8)

        async def full(s):
            cached = self._final.get(s["id"])
            if cached and cached["status"] == s["status"]:
                return cached
            async with sem:
                inv = await self.get_invoice(s["id"])
            if inv["status"] in FINAL:
                self._final[inv["id"]] = inv
            return inv

        return await asyncio.gather(*[full(s) for s in summaries])

    async def get_invoice(self, invoice_id):
        return await self.call("GET", f"/v2/invoicing/invoices/{invoice_id}")

    async def next_number(self):
        return (await self.call("POST", "/v2/invoicing/generate-next-invoice-number"))["invoice_number"]

    async def create_draft(self, *, client_name, client_email, items, currency="USD", due_in_days=14,
                           note=None, invoice_date=None, memo=None):
        given, _, family = client_name.strip().partition(" ")
        inv_date = date.fromisoformat(invoice_date) if invoice_date else date.today()
        body = {
            "detail": {
                "invoice_number": await self.next_number(),
                "currency_code": currency,
                "invoice_date": inv_date.isoformat(),
                "payment_term": {"due_date": (inv_date + timedelta(days=int(due_in_days))).isoformat()},
                **({"note": note[:4000]} if note else {}),
                **({"memo": memo[:500]} if memo else {}),
            },
            "primary_recipients": [{
                "billing_info": {
                    "name": {"given_name": given or client_name, **({"surname": family} if family else {})},
                    "email_address": client_email,
                }
            }],
            "items": [{
                "name": it["name"][:200],
                **({"description": it["description"][:1000]} if it.get("description") else {}),
                "quantity": str(it.get("quantity", 1)),
                "unit_amount": {"currency_code": currency, "value": f"{float(it['unit_price']):.2f}"},
                "unit_of_measure": it.get("unit", "QUANTITY").upper() if it.get("unit", "").upper() in ("HOURS", "AMOUNT", "QUANTITY") else "QUANTITY",
            } for it in items],
            "configuration": {"allow_tip": False, "tax_inclusive": False},
        }
        return await self.call("POST", "/v2/invoicing/invoices", json=body)

    async def send(self, invoice_id, subject=None, note=None):
        body = {"send_to_recipient": True, "send_to_invoicer": True}
        if subject:
            body["subject"] = subject[:4000]
        if note:
            body["note"] = note[:4000]
        return await self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/send", json=body)

    async def remind(self, invoice_id, subject, note):
        return await self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                               json={"subject": subject[:4000], "note": note[:4000], "send_to_recipient": True, "send_to_invoicer": True})

    async def record_payment(self, invoice_id, amount, currency, method="BANK_TRANSFER", note=None, paid_on=None):
        body = {"method": method, "payment_date": paid_on or date.today().isoformat(),
                "amount": {"currency_code": currency, "value": f"{float(amount):.2f}"}}
        if note:
            body["note"] = note[:2000]
        return await self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/payments", json=body)

    async def cancel(self, invoice_id, note=None):
        return await self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel",
                               json={"subject": "Invoice cancelled", "note": note or "This invoice has been cancelled.",
                                     "send_to_recipient": True, "send_to_invoicer": True})

    async def delete(self, invoice_id):
        return await self.call("DELETE", f"/v2/invoicing/invoices/{invoice_id}")

    async def verify_webhook(self, headers, event):
        webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID")
        if not webhook_id:
            return False
        body = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        j = await self.call("POST", "/v1/notifications/verify-webhook-signature", json=body)
        return j.get("verification_status") == "SUCCESS"
