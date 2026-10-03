from collections import defaultdict
from datetime import date

OPEN = {"SENT", "UNPAID", "PARTIALLY_PAID", "SCHEDULED", "PAYMENT_PENDING"}
PAID = {"PAID", "MARKED_AS_PAID"}


def _d(s):
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def normalize(inv, today=None):
    today = today or date.today()
    d = inv.get("detail", {})
    rec = (inv.get("primary_recipients") or [{}])[0].get("billing_info", {})
    name = rec.get("name", {})
    client = rec.get("business_name") or " ".join(x for x in [name.get("given_name"), name.get("surname")] if x) or rec.get("email_address", "Unknown")
    amount = float(inv.get("amount", {}).get("value", 0) or 0)
    due_amount = float(inv.get("due_amount", {}).get("value", amount) or 0)
    paid_amount = float(inv.get("payments", {}).get("paid_amount", {}).get("value", 0) or 0)
    status = inv.get("status", "DRAFT")
    issued = _d(d.get("invoice_date", ""))
    due = _d(d.get("payment_term", {}).get("due_date", "")) or issued
    pay_dates = [_d(t.get("payment_date", "")) for t in inv.get("payments", {}).get("transactions", [])]
    pay_dates = [p for p in pay_dates if p]
    paid_on = max(pay_dates) if pay_dates else None
    days_overdue = (today - due).days if (status in OPEN and due and today > due) else 0
    days_late_paid = (paid_on - due).days if (status in PAID and paid_on and due) else None
    return {
        "id": inv["id"],
        "number": d.get("invoice_number"),
        "status": status,
        "client": client,
        "email": rec.get("email_address"),
        "currency": d.get("currency_code", "USD"),
        "amount": round(amount, 2),
        "due_amount": round(due_amount if status not in PAID else 0, 2),
        "paid_amount": round(paid_amount, 2),
        "issued": issued.isoformat() if issued else None,
        "due": due.isoformat() if due else None,
        "paid_on": paid_on.isoformat() if paid_on else None,
        "days_overdue": days_overdue,
        "days_late_paid": days_late_paid,
        "items": [{"name": i.get("name"), "qty": i.get("quantity"), "unit_price": i.get("unit_amount", {}).get("value")} for i in inv.get("items", [])],
        "note": d.get("note"),
        "pay_link": d.get("metadata", {}).get("recipient_view_url"),
    }


def client_profiles(rows):
    by = defaultdict(list)
    for r in rows:
        by[(r["email"] or r["client"]).lower()].append(r)
    out = {}
    for key, rs in by.items():
        paid = [r for r in rs if r["days_late_paid"] is not None]
        late = [r for r in paid if r["days_late_paid"] > 0]
        open_ = [r for r in rs if r["status"] in OPEN]
        out[key] = {
            "client": rs[0]["client"],
            "email": rs[0]["email"],
            "invoices": len(rs),
            "paid_invoices": len(paid),
            "on_time_rate": round(1 - len(late) / len(paid), 2) if paid else None,
            "avg_days_late": round(sum(max(0, r["days_late_paid"]) for r in paid) / len(paid), 1) if paid else None,
            "lifetime_paid": round(sum(r["paid_amount"] or r["amount"] for r in rs if r["status"] in {"PAID", "MARKED_AS_PAID"}), 2),
            "open_balance": round(sum(r["due_amount"] for r in open_), 2),
            "max_days_overdue": max([r["days_overdue"] for r in open_], default=0),
        }
    return out


def risk(row, profile):
    """0-100: how likely this invoice needs intervention now."""
    if row["status"] not in OPEN:
        return 0
    score = min(60, row["days_overdue"] * 2)
    if profile and profile["on_time_rate"] is not None:
        score += int((1 - profile["on_time_rate"]) * 25)
    elif row["days_overdue"]:
        score += 10
    score += min(15, int(row["due_amount"] / 200))
    return min(100, score)


def tone_for(row, profile):
    od = row["days_overdue"]
    reliable = profile and profile["on_time_rate"] is not None and profile["on_time_rate"] >= 0.8
    if od <= 0:
        return "friendly heads-up"
    if od <= 7:
        return "warm nudge" if reliable else "friendly but clear"
    if od <= 30:
        return "polite but firm" if reliable else "firm"
    return "final notice, professional"


def build_ledger(invoices, today=None):
    today = today or date.today()
    rows = [normalize(i, today) for i in invoices]
    profiles = client_profiles(rows)
    for r in rows:
        p = profiles.get((r["email"] or r["client"]).lower())
        r["risk"] = risk(r, p)
        r["suggested_tone"] = tone_for(r, p) if r["status"] in OPEN else None
    rows.sort(key=lambda r: (-r["risk"], r["due"] or ""))
    month = today.strftime("%Y-%m")
    paid = [r for r in rows if r["status"] in PAID]
    open_ = [r for r in rows if r["status"] in OPEN]
    totals = {
        "outstanding": round(sum(r["due_amount"] for r in open_), 2),
        "overdue": round(sum(r["due_amount"] for r in open_ if r["days_overdue"] > 0), 2),
        "overdue_count": sum(1 for r in open_ if r["days_overdue"] > 0),
        "paid_this_month": round(sum(r["paid_amount"] or r["amount"] for r in paid if (r["paid_on"] or "").startswith(month)), 2),
        "drafts": sum(1 for r in rows if r["status"] == "DRAFT"),
        "avg_days_to_pay": round(sum((_d(r["paid_on"]) - _d(r["issued"])).days for r in paid if r["paid_on"] and r["issued"]) / len(paid), 1) if paid else None,
    }
    return {"today": today.isoformat(), "totals": totals, "invoices": rows, "clients": list(profiles.values())}
