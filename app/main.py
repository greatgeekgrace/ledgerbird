import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, llm
from .ledger import build_ledger
from .paypal import PayPal, PayPalError

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Ledgerbird")
pp = PayPal()
EVENTS = deque(maxlen=200)
HITS = defaultdict(deque)
CHAT_LIMIT = int(os.environ.get("CHAT_LIMIT_PER_HOUR", "60"))


@app.exception_handler(PayPalError)
async def paypal_error(_, e: PayPalError):
    return JSONResponse({"error": str(e)}, status_code=502)


def limit(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    q = HITS[ip]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= CHAT_LIMIT:
        raise HTTPException(429, "Demo rate limit reached — try again in a little while.")
    q.append(now)


class ChatIn(BaseModel):
    history: list[dict]
    autopilot: bool = False


class ReminderIn(BaseModel):
    subject: str
    note: str


@app.get("/api/health")
async def health():
    return {"ok": True, "model": llm.MODEL, "paypal": "sandbox" if "sandbox" in os.environ.get("PAYPAL_API_BASE", "sandbox") else "live"}


@app.get("/api/ledger")
async def ledger():
    return build_ledger(await pp.list_invoices())


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    limit(request)
    return await agent.run(pp, body.history, autopilot=body.autopilot)


@app.post("/api/actions/{aid}/approve")
async def approve(aid: str):
    res = await agent.approve(pp, aid)
    if "error" in res:
        raise HTTPException(410, res["error"])
    return res


@app.post("/api/actions/{aid}/reject")
async def reject(aid: str):
    return agent.reject(aid)


@app.post("/api/invoices/{invoice_id}/draft-reminder")
async def draft_reminder(invoice_id: str, request: Request):
    limit(request)
    led = build_ledger(await pp.list_invoices())
    row = next((r for r in led["invoices"] if r["id"] == invoice_id), None)
    if not row:
        raise HTTPException(404, "Invoice not found")
    prof = next((c for c in led["clients"] if (c["email"] or c["client"]) == (row["email"] or row["client"])), None)
    prompt = (
        "Write a payment reminder email for this invoice. Return JSON with keys subject and note only.\n"
        f"Tone: {row['suggested_tone']}. Today: {led['today']}.\n"
        f"Invoice: {json.dumps({k: row[k] for k in ('number', 'client', 'amount', 'due_amount', 'currency', 'issued', 'due', 'days_overdue', 'items')})}\n"
        f"Client history: {json.dumps(prof)}\n"
        "40-110 words, first name greeting, mention amount, invoice number and due date, one clear next step, sign off as the freelancer without a name placeholder."
    )
    msg = await llm.chat([{"role": "system", "content": "You write concise, human, professional emails. Output only JSON."},
                          {"role": "user", "content": prompt}], temperature=0.5)
    text = (msg.get("content") or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        out = {"subject": f"Reminder: invoice {row['number']}", "note": text}
    return {"subject": out.get("subject", ""), "note": out.get("note", ""), "tone": row["suggested_tone"]}


@app.post("/api/invoices/{invoice_id}/remind")
async def remind(invoice_id: str, body: ReminderIn):
    await pp.remind(invoice_id, body.subject, body.note)
    EVENTS.append({"t": time.time(), "type": "REMINDER_SENT", "invoice_id": invoice_id})
    return {"ok": True}


@app.post("/api/invoices/{invoice_id}/send")
async def send(invoice_id: str):
    r = await pp.send(invoice_id)
    return {"ok": True, "href": r.get("href")}


@app.post("/api/paypal/webhook")
async def webhook(request: Request):
    event = await request.json()
    if not await pp.verify_webhook({k.lower(): v for k, v in request.headers.items()}, event):
        raise HTTPException(400, "signature verification failed")
    res = event.get("resource", {})
    inv = res.get("invoice", res)
    EVENTS.append({"t": time.time(), "type": event.get("event_type"), "invoice_id": inv.get("id"),
                   "number": inv.get("detail", {}).get("invoice_number"), "summary": event.get("summary")})
    return {"ok": True}


@app.get("/api/events")
async def events(since: float = 0):
    return [e for e in EVENTS if e["t"] > since]


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")
