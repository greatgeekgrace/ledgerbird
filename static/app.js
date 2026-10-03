const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, cur = "USD") => new Intl.NumberFormat("en-US", { style: "currency", currency: cur }).format(v || 0);

let LEDGER = null;
let FILTER = "attention";
let HISTORY = [];
let lastEvent = Date.now() / 1000;

try { $("#autopilot").checked = localStorage.getItem("lb-autopilot") === "1"; } catch {}
$("#autopilot").addEventListener("change", (e) => { try { localStorage.setItem("lb-autopilot", e.target.checked ? "1" : "0"); } catch {} });

function toast(text, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = text;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 6000);
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || `Request failed (${r.status})`);
  return j;
}

function md(text) {
  const lines = esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").split("\n");
  let html = "", inList = false;
  for (const l of lines) {
    const m = l.match(/^\s*[-*•]\s+(.*)/) || l.match(/^\s*\d+\.\s+(.*)/);
    if (m) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${m[1]}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    const h = l.match(/^\s*#{1,4}\s+(.*)/);
    if (h) { html += `<p><b>${h[1]}</b></p>`; continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(l)) continue;
    if (l.trim()) html += `<p>${l}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

function statusPill(r) {
  if (r.status === "DRAFT") return `<span class="pill draft">Draft</span>`;
  if (r.status === "PAID" || r.status === "MARKED_AS_PAID") return `<span class="pill good">Paid ${r.paid_on ? esc(r.paid_on) : ""}</span>`;
  if (r.status === "CANCELLED") return `<span class="pill draft">Cancelled</span>`;
  if (r.days_overdue > 0) return `<span class="pill bad">${r.days_overdue} days overdue</span>`;
  if (r.status === "PARTIALLY_PAID") return `<span class="pill">Partly paid · due ${esc(r.due)}</span>`;
  return `<span class="pill">Due ${esc(r.due)}</span>`;
}

const OPEN = new Set(["SENT", "UNPAID", "PARTIALLY_PAID", "SCHEDULED", "PAYMENT_PENDING"]);
const filters = {
  attention: (r) => OPEN.has(r.status) && (r.days_overdue > 0 || r.risk >= 30),
  open: (r) => OPEN.has(r.status),
  draft: (r) => r.status === "DRAFT",
  paid: (r) => r.status === "PAID" || r.status === "MARKED_AS_PAID",
  all: () => true,
};

function renderBoard() {
  if (!LEDGER) return;
  const t = LEDGER.totals;
  $("#k-out").textContent = money(t.outstanding);
  $("#k-over").textContent = money(t.overdue);
  $("#k-overc").textContent = t.overdue_count ? `${t.overdue_count} invoice${t.overdue_count > 1 ? "s" : ""}` : "";
  $("#k-paid").textContent = money(t.paid_this_month);
  $("#k-days").textContent = t.avg_days_to_pay ?? "—";
  const rows = LEDGER.invoices.filter(filters[FILTER]);
  $("#list").innerHTML = rows.length ? rows.map((r) => `
    <article class="inv" data-id="${esc(r.id)}">
      <div><div class="who">${esc(r.client)}</div><div class="meta">#${esc(r.number)} · ${esc(r.items.map((i) => i.name).join(", ")).slice(0, 90)}</div></div>
      <div class="amt">${money(r.status === "DRAFT" || !OPEN.has(r.status) ? r.amount : r.due_amount, r.currency)}</div>
      <div class="acts">
        ${statusPill(r)}
        ${OPEN.has(r.status) ? `<span class="risk" title="Collection risk ${r.risk}/100"><i style="width:${r.risk}%"></i></span>` : ""}
        <span style="flex:1"></span>
        ${OPEN.has(r.status) ? `<button class="btn" data-act="remind">✉︎ Draft reminder</button>` : ""}
        ${r.status === "DRAFT" ? `<button class="btn primary" data-act="send">Send via PayPal</button>` : ""}
        ${r.pay_link ? `<a class="btn" href="${esc(r.pay_link)}" target="_blank" rel="noopener">Pay page ↗</a>` : ""}
      </div>
    </article>`).join("") : `<div class="empty">${FILTER === "attention" ? "Nothing needs chasing right now. 🎉" : "No invoices here yet."}</div>`;
  const cl = [...LEDGER.clients].sort((a, b) => b.open_balance - a.open_balance);
  $("#clients").innerHTML = `<table><tr><th>Client</th><th>On-time</th><th>Avg. late</th><th>Open</th><th>Lifetime</th></tr>${cl.map((c) => `
    <tr><td>${esc(c.client)}</td><td>${c.on_time_rate == null ? "—" : Math.round(c.on_time_rate * 100) + "%"}</td><td>${c.avg_days_late == null ? "—" : c.avg_days_late + "d"}</td><td>${money(c.open_balance)}</td><td>${money(c.lifetime_paid)}</td></tr>`).join("")}</table>`;
}

async function loadLedger() {
  try { LEDGER = await api("/api/ledger"); renderBoard(); }
  catch (e) { $("#list").innerHTML = `<div class="empty">Couldn't reach PayPal: ${esc(e.message)}</div>`; }
}

$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  FILTER = b.dataset.f;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  renderBoard();
});

let remId = null;
$("#list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const id = b.closest(".inv").dataset.id;
  const row = LEDGER.invoices.find((r) => r.id === id);
  b.disabled = true;
  try {
    if (b.dataset.act === "send") {
      await api(`/api/invoices/${id}/send`, { method: "POST" });
      toast(`Invoice #${row.number} sent to ${row.client}`);
      await loadLedger();
    } else {
      const old = b.textContent; b.textContent = "Writing…";
      const d = await api(`/api/invoices/${id}/draft-reminder`, { method: "POST" });
      b.textContent = old;
      remId = id;
      $("#remWho").textContent = `${row.client} · #${row.number}`;
      $("#remTone").textContent = d.tone;
      $("#remSubj").value = d.subject; $("#remNote").value = d.note;
      $("#remDlg").showModal();
    }
  } catch (err) { toast(err.message, true); }
  b.disabled = false;
});

$("#remDlg").addEventListener("close", async () => {
  if ($("#remDlg").returnValue !== "send" || !remId) return;
  try {
    await api(`/api/invoices/${remId}/remind`, { method: "POST", body: JSON.stringify({ subject: $("#remSubj").value, note: $("#remNote").value }) });
    toast("Reminder sent through PayPal");
  } catch (err) { toast(err.message, true); }
  remId = null;
});

function addMsg(role, html) {
  const d = document.createElement("div");
  d.className = `msg ${role}`;
  d.innerHTML = html;
  $("#log").append(d);
  $("#log").scrollTop = $("#log").scrollHeight;
  return d;
}

function describe(a) {
  const r = LEDGER?.invoices.find((x) => x.id === a.args.invoice_id);
  const label = r ? `#${esc(r.number)} · ${esc(r.client)} · ${money(r.due_amount || r.amount, r.currency)}` : esc(a.args.invoice_id);
  if (a.tool === "send_reminder") return `<h4>Send reminder — ${label}</h4><b>${esc(a.args.subject)}</b><pre>${esc(a.args.note)}</pre>`;
  if (a.tool === "send_invoice") return `<h4>Send invoice — ${label}</h4>${a.args.message ? `<pre>${esc(a.args.message)}</pre>` : ""}`;
  if (a.tool === "cancel_invoice") return `<h4>Cancel invoice — ${label}</h4>${a.args.reason ? `<pre>${esc(a.args.reason)}</pre>` : ""}`;
  return `<h4>${esc(a.tool)}</h4>`;
}

function addApproval(a) {
  const d = document.createElement("div");
  d.className = "approval";
  d.innerHTML = `${describe(a)}<div class="row"><button class="primary" data-ok>Approve</button><button class="ghost" data-no>Dismiss</button></div>`;
  d.querySelector("[data-ok]").onclick = async (ev) => {
    ev.target.disabled = true;
    try { await api(`/api/actions/${a.id}/approve`, { method: "POST" }); d.innerHTML = `${describe(a)}<p class="muted">✓ Done — PayPal emailed the client.</p>`; loadLedger(); }
    catch (err) { toast(err.message, true); ev.target.disabled = false; }
  };
  d.querySelector("[data-no]").onclick = async () => { await api(`/api/actions/${a.id}/reject`, { method: "POST" }); d.remove(); };
  $("#log").append(d);
  $("#log").scrollTop = $("#log").scrollHeight;
}

const TOOL_LABEL = { get_ledger: "read PayPal invoices", create_invoice_draft: "created draft", send_invoice: "send invoice", send_reminder: "reminder", record_offline_payment: "recorded payment", cancel_invoice: "cancel", delete_draft: "deleted draft" };

async function ask(text) {
  HISTORY.push({ role: "user", content: text });
  addMsg("user", esc(text));
  const typing = addMsg("bot", `<span class="typing"><span></span><span></span><span></span></span>`);
  $("#sendBtn").disabled = true;
  try {
    const res = await api("/api/chat", { method: "POST", body: JSON.stringify({ history: HISTORY, autopilot: $("#autopilot").checked }) });
    HISTORY.push({ role: "assistant", content: res.reply });
    const trace = res.trace.length ? `<div class="trace">${res.trace.map((t) => `<span>${t.ok ? "✓" : "✕"} ${esc(TOOL_LABEL[t.tool] || t.tool)}</span>`).join("")}</div>` : "";
    typing.innerHTML = md(res.reply) + trace;
    $("#log").scrollTop = $("#log").scrollHeight;
    if (res.changed || res.actions.length) await loadLedger();
    res.actions.forEach(addApproval);
  } catch (e) {
    typing.innerHTML = `<p>Sorry — ${esc(e.message)}</p>`;
    HISTORY.pop();
  }
  $("#sendBtn").disabled = false;
}

const SAMPLE = `From: Priya Nair <priya@sandbox-northwind.example>
Subject: Re: onboarding redesign

Hi! Great call today. Confirming we'd like you to go ahead with:
- the onboarding flow redesign, 12 hours at your $85/h rate
- 3 extra illustration variants at $120 each
Net 14 is fine for us. Thanks!
Priya`;

$("#chips").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.sample) { $("#input").value = `Bill this:\n\n${SAMPLE}`; $("#input").focus(); return; }
  ask(b.dataset.q);
});

$("#form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#input").value.trim();
  if (!v) return;
  $("#input").value = "";
  ask(v);
});
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#form").requestSubmit(); } });
$("#clear").addEventListener("click", () => { HISTORY = []; $("#log").innerHTML = ""; addMsg("bot", "<p>Fresh start. What do you need?</p>"); });

async function pollEvents() {
  try {
    const ev = await api(`/api/events?since=${lastEvent}`);
    for (const e of ev) {
      lastEvent = Math.max(lastEvent, e.t);
      if (e.type === "INVOICING.INVOICE.PAID") toast(`💸 Invoice #${e.number || ""} was just paid through PayPal`);
    }
    if (ev.some((e) => e.type?.startsWith("INVOICING."))) loadLedger();
  } catch {}
}

loadLedger();
setInterval(pollEvents, 8000);
setInterval(loadLedger, 60000);
