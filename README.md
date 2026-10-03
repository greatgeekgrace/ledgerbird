# Ledgerbird

**An AI accounts-receivable agent for freelancers, built on PayPal Invoicing.**

Freelancers lose real money to late payments — not because clients refuse to pay, but because chasing is awkward, so it doesn't happen. Ledgerbird watches your PayPal invoices, turns "sounds good, go ahead!" emails into invoices, and writes the follow-ups you keep putting off — in the right tone for each client, based on how that client has actually paid you before.

> Runs entirely on the **PayPal sandbox**. No real money moves.

## What it does

- **Bill from a conversation.** Paste an email or chat thread where a client agreed to work. The agent extracts the client, line items, rates, quantities, currency and payment terms, then creates a PayPal draft invoice.
- **Collections queue, ranked by risk.** Every open invoice gets a 0–100 risk score from days overdue, amount, and the client's own payment history (on-time rate, average days late). The board shows what to chase first.
- **Reminders that fit the relationship.** A long-time client who always pays on time and is 4 days late gets a warm nudge; a repeat late payer 3 weeks over gets a clear, firm note with a date. The agent drafts it, you edit, PayPal sends it with the pay link.
- **Human-in-the-loop by default.** Anything that emails a client (send, remind, cancel) goes into an approval queue — one click to approve. Flip **Autopilot** on to let the agent act directly.
- **Ask anything.** "Who owes me money?", "Chase everyone overdue", "Mark Tom's invoice paid, he sent a bank transfer", "Give me a cash-flow summary".
- **Live payment events.** A verified PayPal webhook (`INVOICING.INVOICE.PAID`) pops a toast and refreshes the board when a client pays.

## How PayPal is used

| Capability | PayPal API |
|---|---|
| Read every invoice with status, amounts, payments | `GET /v2/invoicing/invoices`, `GET /v2/invoicing/invoices/{id}` |
| Create invoices from extracted work | `POST /v2/invoicing/generate-next-invoice-number`, `POST /v2/invoicing/invoices` |
| Send / remind / cancel | `POST /v2/invoicing/invoices/{id}/send`, `/remind`, `/cancel` |
| Record off-PayPal payments | `POST /v2/invoicing/invoices/{id}/payments` |
| Delete unused drafts | `DELETE /v2/invoicing/invoices/{id}` |
| Payment notifications | Webhooks + `POST /v1/notifications/verify-webhook-signature` |
| Auth | OAuth 2.0 client credentials |

PayPal is the single source of truth — Ledgerbird keeps no database. Client payment habits are computed from the invoice history PayPal already has.

## How AI is used

- A tool-calling agent (`app/agent.py`) with seven tools over the PayPal API: `get_ledger`, `create_invoice_draft`, `send_invoice`, `send_reminder`, `record_offline_payment`, `cancel_invoice`, `delete_draft`.
- Client-facing tools are intercepted and turned into approval cards unless Autopilot is on.
- Reminder drafting combines the invoice, the computed tone (`app/ledger.py`) and the client's history.
- Model: Google Gemini (`gemini-3.5-flash` by default) through its OpenAI-compatible endpoint, so any OpenAI-compatible model can be swapped in with `LLM_BASE_URL` / `LLM_MODEL`.

## Architecture

```
browser (static/)  ──►  FastAPI (app/main.py)
                          ├── agent.py   tool-calling loop + approval queue
                          ├── ledger.py  risk score, tone, client profiles
                          ├── paypal.py  Invoicing v2 + webhooks client
                          └── llm.py     OpenAI-compatible chat client
```

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export PAYPAL_CLIENT_ID=...        # sandbox REST app (developer.paypal.com → Apps & Credentials)
export PAYPAL_CLIENT_SECRET=...
export LLM_API_KEY=...             # Google AI Studio API key
.venv/bin/python -m scripts.seed_demo      # optional: realistic demo clients & invoices
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000.

Optional: set `PAYPAL_WEBHOOK_ID` after creating a sandbox webhook pointing to `https://<your-host>/api/paypal/webhook` with the `Invoicing` events.

## Deploy

`render.yaml` deploys the app as a free Render web service. Set the secret environment variables in the Render dashboard.

## Trying the hosted demo

1. Open the app — the board is already populated with demo clients who pay on time, pay late, or have gone quiet.
2. Click **Who owes me?** then **Chase overdue** and approve one reminder.
3. Click **Bill from an email**, send it, then ask the agent to send the new invoice.
4. Open **Pay page ↗** on any invoice and pay it with a PayPal sandbox *personal* account to watch the board update.

## License

MIT — see [LICENSE](LICENSE).
