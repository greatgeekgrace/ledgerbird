import json
import time
import uuid
from datetime import date

from . import llm
from .ledger import build_ledger
from .paypal import PayPalError

CLIENT_FACING = {"send_invoice", "send_reminder", "cancel_invoice"}

TOOLS = [
    {"type": "function", "function": {
        "name": "get_ledger",
        "description": "Fetch every invoice from PayPal with status, amounts, due dates, days overdue, a 0-100 risk score, a suggested reminder tone, and per-client payment history (on-time rate, average days late, open balance). Call this before answering any question about money owed, clients or invoices.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "create_invoice_draft",
        "description": "Create a DRAFT PayPal invoice (nothing is sent to the client yet). Use for work the user describes or that you extract from a pasted email/chat thread.",
        "parameters": {"type": "object", "required": ["client_name", "client_email", "items"], "properties": {
            "client_name": {"type": "string"},
            "client_email": {"type": "string"},
            "currency": {"type": "string", "description": "ISO code, default USD"},
            "due_in_days": {"type": "integer", "description": "Payment terms in days, default 14"},
            "note": {"type": "string", "description": "Short note to the client shown on the invoice"},
            "items": {"type": "array", "items": {"type": "object", "required": ["name", "quantity", "unit_price"], "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "quantity": {"type": "number"},
                "unit_price": {"type": "number"},
                "unit": {"type": "string", "enum": ["HOURS", "QUANTITY", "AMOUNT"]}}}}}}}},
    {"type": "function", "function": {
        "name": "send_invoice",
        "description": "Send a DRAFT invoice to the client through PayPal (client gets an email with a pay link).",
        "parameters": {"type": "object", "required": ["invoice_id"], "properties": {
            "invoice_id": {"type": "string"},
            "message": {"type": "string", "description": "Optional personal message included in the email"}}}}},
    {"type": "function", "function": {
        "name": "send_reminder",
        "description": "Send a payment reminder for an unpaid invoice through PayPal. Write the subject and note yourself, matching the suggested tone and the client's history.",
        "parameters": {"type": "object", "required": ["invoice_id", "subject", "note"], "properties": {
            "invoice_id": {"type": "string"},
            "subject": {"type": "string"},
            "note": {"type": "string", "description": "The reminder text, 40-120 words, no placeholders"}}}}},
    {"type": "function", "function": {
        "name": "record_offline_payment",
        "description": "Mark an invoice as (partly) paid when the client paid outside PayPal, e.g. bank transfer or cash.",
        "parameters": {"type": "object", "required": ["invoice_id", "amount"], "properties": {
            "invoice_id": {"type": "string"},
            "amount": {"type": "number"},
            "method": {"type": "string", "enum": ["BANK_TRANSFER", "CASH", "CHECK", "CREDIT_CARD", "DEBIT_CARD", "WIRE_TRANSFER", "OTHER"]},
            "note": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "cancel_invoice",
        "description": "Cancel a SENT invoice (client is notified).",
        "parameters": {"type": "object", "required": ["invoice_id"], "properties": {
            "invoice_id": {"type": "string"}, "reason": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "delete_draft",
        "description": "Delete a DRAFT invoice that was never sent.",
        "parameters": {"type": "object", "required": ["invoice_id"], "properties": {"invoice_id": {"type": "string"}}}}},
]

SYSTEM = """You are Ledgerbird, an accounts-receivable assistant for a solo freelancer. Today is {today}.
You work directly on the freelancer's PayPal account through tools. Money and client relationships are at stake, so:
- Never invent client emails, rates or quantities. If something essential is missing, ask one short question.
- When the user pastes an email/chat thread, extract the agreed work, rates, quantities, currency and terms, then create a draft.
- Before writing a reminder, call get_ledger and use the invoice's suggested_tone and the client's history (on-time rate, average days late, lifetime value). Reliable long-term clients get warmth; repeat late payers get clarity and a concrete date. Never threaten, never guilt-trip, always include the amount, invoice number and due date.
- Sending, reminding and cancelling email the client. {approval}
- Be brief. Use plain sentences and short bullet lists. Format money like $1,250.00. Refer to invoices by number and client."""

APPROVAL_ON = "These go to an approval queue the user confirms with one click; after calling them tell the user it is waiting for approval."
APPROVAL_OFF = "Autopilot is ON: these execute immediately, so only call them when the user's request clearly covers it."

PENDING = {}


def _compact_ledger(led):
    inv = [{k: r[k] for k in ("id", "number", "status", "client", "email", "currency", "amount", "due_amount", "issued", "due", "paid_on", "days_overdue", "risk", "suggested_tone", "items")} for r in led["invoices"][:60]]
    return {"today": led["today"], "totals": led["totals"], "invoices": inv, "clients": led["clients"]}


async def execute(pp, name, args):
    if name == "get_ledger":
        return _compact_ledger(build_ledger(await pp.list_invoices()))
    if name == "create_invoice_draft":
        inv = await pp.create_draft(client_name=args["client_name"], client_email=args["client_email"], items=args["items"],
                                    currency=args.get("currency") or "USD", due_in_days=args.get("due_in_days") or 14, note=args.get("note"))
        return {"created": True, "invoice_id": inv["id"], "number": inv["detail"]["invoice_number"], "status": inv["status"], "total": inv.get("amount", {}).get("value")}
    if name == "send_invoice":
        r = await pp.send(args["invoice_id"], note=args.get("message"))
        return {"sent": True, "pay_link": r.get("href")}
    if name == "send_reminder":
        await pp.remind(args["invoice_id"], args["subject"], args["note"])
        return {"reminder_sent": True}
    if name == "record_offline_payment":
        inv = await pp.get_invoice(args["invoice_id"])
        await pp.record_payment(args["invoice_id"], args["amount"], inv["detail"]["currency_code"], args.get("method") or "BANK_TRANSFER", args.get("note"))
        return {"recorded": True}
    if name == "cancel_invoice":
        await pp.cancel(args["invoice_id"], args.get("reason"))
        return {"cancelled": True}
    if name == "delete_draft":
        await pp.delete(args["invoice_id"])
        return {"deleted": True}
    return {"error": f"unknown tool {name}"}


def _queue(name, args):
    for k in [k for k, v in PENDING.items() if time.time() - v["t"] > 3600]:
        PENDING.pop(k, None)
    aid = uuid.uuid4().hex[:12]
    PENDING[aid] = {"t": time.time(), "tool": name, "args": args}
    return aid


async def run(pp, history, autopilot=False, max_steps=8):
    msgs = [{"role": "system", "content": SYSTEM.format(today=date.today().isoformat(), approval=APPROVAL_OFF if autopilot else APPROVAL_ON)}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in history[-20:] if m.get("role") in ("user", "assistant")]
    trace, actions, changed = [], [], False
    for _ in range(max_steps):
        msg = await llm.chat(msgs, tools=TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"reply": msg.get("content") or "", "trace": trace, "actions": actions, "changed": changed}
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name in CLIENT_FACING and not autopilot:
                aid = _queue(name, args)
                actions.append({"id": aid, "tool": name, "args": args})
                result = {"status": "queued_for_user_approval", "approval_id": aid}
            else:
                try:
                    result = await execute(pp, name, args)
                    changed = changed or name != "get_ledger"
                except PayPalError as e:
                    result = {"error": str(e)}
            trace.append({"tool": name, "args": args, "ok": "error" not in result})
            msgs.append({"role": "tool", "tool_call_id": c.get("id", name), "name": name, "content": json.dumps(result)[:30000]})
    return {"reply": "I stopped after several steps — please check the board and tell me what to do next.", "trace": trace, "actions": actions, "changed": changed}


async def approve(pp, aid):
    item = PENDING.pop(aid, None)
    if not item:
        return {"error": "This approval has expired or was already handled."}
    return await execute(pp, item["tool"], item["args"])


def reject(aid):
    return {"dropped": PENDING.pop(aid, None) is not None}
