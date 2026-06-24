#!/usr/bin/env python3
"""
Bella Poynt Payment Webhook Listener v3 — Unified Dashboard
- /webhook: Poynt payment events → log + auto-deliver + owner notify
- /import-payments: bulk backfill historical transactions
- /register-fan: pre-register email→chat_id
- /check-payment: smart match without email
- /dashboard: full ops dashboard (payments + conversation stats)
- /api/summary: JSON summary for embeds
- /payments: raw payment log JSON
"""
import json, os, time, hmac, hashlib, base64, threading
import urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN", "")
POYNT_APP_ID_RAW = os.environ.get("POYNT_APP_ID", "")
POYNT_SECRET_RAW = os.environ.get("POYNT_CLIENT_SECRET", "")
WEBHOOK_SECRET   = os.environ.get("POYNT_WEBHOOK_SECRET", "")
ADMIN_TOKEN      = os.environ.get("ADMIN_TOKEN", "bella-admin-2024")
_owner_raw       = os.environ.get("OWNER_CHAT_ID", "8635601598,993656394")
OWNER_CHAT_IDS   = [int(x.strip()) for x in _owner_raw.split(",") if x.strip()]
CONTENT_MESSAGE  = os.environ.get("CONTENT_MESSAGE", "")  # empty = placeholder mode
BUSINESS_ID      = "8b2a6d7f-7a1f-4a96-9ea5-abc73755d69a"
PORT             = int(os.environ.get("PORT", 8080))
STATS_URL        = os.environ.get("STATS_URL", "")  # bella-bot stats API URL (optional)

DATA_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PAYMENTS_LOG = os.path.join(DATA_DIR, "payments_log.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending_fans.json")
os.makedirs(DATA_DIR, exist_ok=True)
_lock = threading.Lock()


# ── File helpers ─────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except: return default

def save_json(path, data):
    with _lock:
        with open(path, "w") as f: json.dump(data, f, indent=2)


# ── Poynt auth ───────────────────────────────────────────────────────────────
def get_poynt_token():
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding as ap
        from cryptography.hazmat.backends import default_backend
        import uuid as _u
        app_id = "urn:aid:" + POYNT_APP_ID_RAW.split("urn:aid:")[-1].strip()
        clean  = POYNT_SECRET_RAW.replace("-----BEGIN RSA PRIVATE KEY----- ","").replace(" -----END RSA PRIVATE KEY-----","").replace(" ","")
        pem    = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(clean[i:i+64] for i in range(0,len(clean),64)) + "\n-----END RSA PRIVATE KEY-----\n"
        key    = serialization.load_pem_private_key(pem.encode(), password=None, backend=default_backend())
        def b64u(d):
            if isinstance(d,str): d=d.encode()
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()
        now = int(time.time())
        hdr = b64u(json.dumps({"alg":"RS256","typ":"JWT"}))
        cls = {"iss":app_id,"sub":app_id,"aud":"https://services.poynt.net","iat":now,"exp":now+300,"jti":str(_u.uuid4())}
        pay = b64u(json.dumps(cls))
        sig = base64.urlsafe_b64encode(key.sign(f"{hdr}.{pay}".encode(), ap.PKCS1v15(), hashes.SHA256())).rstrip(b"=").decode()
        jwt = f"{hdr}.{pay}.{sig}"
        data = f"grantType=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt}".encode()
        req  = urllib.request.Request("https://services.poynt.net/token", data=data,
               headers={"Content-Type":"application/x-www-form-urlencoded","api-version":"1.2","Poynt-Request-Id":str(_u.uuid4())})
        with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())["accessToken"]
    except Exception as e:
        print(f"[poynt_auth] {e}"); return None

def poynt_get(path):
    import uuid as _u
    token = get_poynt_token()
    if not token: return None
    req = urllib.request.Request(f"https://services.poynt.net{path}",
          headers={"Authorization":f"BEARER {token}","api-version":"1.2","Poynt-Request-Id":str(_u.uuid4())})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())
    except Exception as e: print(f"[poynt_get] {e}"); return None


# ── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(chat_id, text, biz=""):
    p = {"chat_id": int(chat_id), "text": text}
    if biz: p["business_connection_id"] = biz
    data = json.dumps(p).encode()
    req  = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
           data=data, headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read()).get("ok", False)
    except Exception as e: print(f"[telegram] {e}"); return False

def notify_owners(name, amount_cents, email, delivered, fan_chat=None):
    amt  = f"${amount_cents/100:.2f}" if amount_cents else "?"
    icon = "✅" if delivered else "📬"
    fan  = f"chat {fan_chat}" if fan_chat else "unmatched"
    msg  = f"💰 New payment!\n👤 {name}\n💵 {amt}\n📧 {email}\n📲 {fan}\n{icon} {'delivered' if delivered else 'logged — no fan registered'}"
    for oid in OWNER_CHAT_IDS: send_telegram(oid, msg)


# ── Smart matching ────────────────────────────────────────────────────────────
def find_unmatched(hours=2, amount_cents=None):
    log    = load_json(PAYMENTS_LOG, [])
    cutoff = time.time() - hours * 3600
    hits   = []
    for e in log:
        if e.get("delivered"): continue
        if e.get("status","") not in ("CAPTURED","AUTHORIZED","COMPLETED",""): continue
        try: ts = time.mktime(time.strptime(e["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
        except: continue
        if ts < cutoff: continue
        if amount_cents and e.get("amount_cents") != amount_cents: continue
        hits.append(e)
    return hits[0] if len(hits) == 1 else None

def mark_delivered(resource_id, chat_id, fan_name=""):
    log = load_json(PAYMENTS_LOG, [])
    for e in log:
        if e.get("resource_id") == resource_id:
            e["delivered"] = True; e["chat_id"] = chat_id
            if fan_name: e["fan_name"] = fan_name
            break
    save_json(PAYMENTS_LOG, log)


# ── Payment event ─────────────────────────────────────────────────────────────
def handle_payment_event(event):
    etype  = event.get("eventType","")
    rid    = event.get("resourceId","")
    links  = event.get("links",[])
    rurl   = links[0].get("href","") if links else ""
    print(f"[payment] {etype} resource={rid}")
    txn = poynt_get(rurl.replace("https://services.poynt.net","")) if rurl else None
    email=""; name=""; amount=0; status=""
    if txn:
        if "fundingSource" in txn:
            card=txn.get("fundingSource",{}).get("card",{}); name=card.get("cardHolderFullName","")
            email=txn.get("receiptEmailAddress",""); amount=txn.get("amounts",{}).get("transactionAmount",0); status=txn.get("status","")
        elif "transactions" in txn:
            t=txn.get("transactions",[{}])[0]; card=t.get("fundingSource",{}).get("card",{})
            name=card.get("cardHolderFullName",""); email=t.get("receiptEmailAddress","")
            amount=txn.get("amounts",{}).get("netTotal",0); status=txn.get("statuses",{}).get("transactionStatusSummary","")
    log   = load_json(PAYMENTS_LOG, [])
    entry = {"ts":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"event_type":etype,"resource_id":rid,
             "name":name,"email":email.lower(),"amount_cents":amount,"amount_usd":f"${amount/100:.2f}" if amount else "?",
             "status":status,"chat_id":None,"delivered":False}
    delivered=False; fan_chat=None
    if status in ("CAPTURED","AUTHORIZED","COMPLETED","") and email:
        pending = load_json(PENDING_FILE, {})
        match   = pending.get(email.lower())
        if match:
            cid=match.get("chat_id"); biz=match.get("biz_conn_id",""); fname=match.get("name","babe")
            entry["chat_id"]=cid
            if CONTENT_MESSAGE:
                ok = send_telegram(cid, CONTENT_MESSAGE.replace("{name}",fname), biz)
            else:
                ok = send_telegram(cid, f"omg thank you SO much {fname}!! 🩷 I got your payment — I'll send your content right over ✨", biz)
            if ok:
                entry["delivered"]=True; delivered=True; fan_chat=cid
                del pending[email.lower()]; save_json(PENDING_FILE, pending)
    log.append(entry); save_json(PAYMENTS_LOG, log)
    if status in ("CAPTURED","AUTHORIZED","COMPLETED","") and name:
        notify_owners(name, amount, email, delivered, fan_chat)


# ── Stats helper ─────────────────────────────────────────────────────────────
def get_payment_stats():
    log      = load_json(PAYMENTS_LOG, [])
    captured = [e for e in log if e.get("status","") in ("CAPTURED","AUTHORIZED","COMPLETED","") and not e.get("event_type","").startswith("BACKFILL_DECLINED")]
    revenue  = sum(e.get("amount_cents",0) for e in captured)
    delivered= sum(1 for e in captured if e.get("delivered"))
    pending  = load_json(PENDING_FILE, {})
    # Daily revenue last 7 days
    daily = []
    for i in range(6,-1,-1):
        d_start = time.time()-(i+1)*86400; d_end=time.time()-i*86400
        d_rev=0; d_cnt=0
        for e in captured:
            try: ts=time.mktime(time.strptime(e["ts"][:19],"%Y-%m-%dT%H:%M:%S"))
            except: continue
            if d_start < ts <= d_end: d_rev+=e.get("amount_cents",0); d_cnt+=1
        daily.append({"date":time.strftime("%m/%d",time.localtime(d_end)),"revenue_cents":d_rev,"count":d_cnt})
    # Top payers
    from collections import defaultdict
    payer_totals = defaultdict(lambda: {"name":"","amount":0,"count":0,"email":""})
    for e in captured:
        k=e.get("email","?"); payer_totals[k]["name"]=e.get("name","?"); payer_totals[k]["email"]=k
        payer_totals[k]["amount"]+=e.get("amount_cents",0); payer_totals[k]["count"]+=1
    top_payers = sorted(payer_totals.values(), key=lambda x: x["amount"], reverse=True)[:10]
    return {"total_revenue_cents":revenue,"total_revenue":f"${revenue/100:.2f}","total_payments":len(captured),
            "delivered":delivered,"unmatched":len(captured)-delivered,"pending_fans":len(pending),
            "daily":daily,"top_payers":top_payers,"recent":list(reversed(log))[:50]}

def get_conv_stats():
    """Fetch conversation stats from bella-bot stats API if available."""
    if not STATS_URL: return None
    try:
        req = urllib.request.Request(f"{STATS_URL}/api/stats?token={ADMIN_TOKEN}")
        with urllib.request.urlopen(req, timeout=5) as r: return json.loads(r.read())
    except: return None


# ── Dashboard HTML ────────────────────────────────────────────────────────────
def build_dashboard(payment_stats, conv_stats):
    ps   = payment_stats
    cs   = conv_stats or {}
    rev  = ps.get("total_revenue","$0.00")
    tp   = ps.get("total_payments",0)
    td   = ps.get("delivered",0)
    tum  = ps.get("unmatched",0)
    tpf  = ps.get("pending_fans",0)

    # Conversation stats boxes
    conv_html = ""
    if cs:
        stars_total = cs.get('stars_total', 0)
        stars_usd   = round(stars_total * 0.013, 2)
        conv_html = (
            f'<div class="stat"><div class="val">{cs.get("total_fans",0)}</div><div class="lbl">Total Fans</div></div>'
            f'<div class="stat"><div class="val">{cs.get("total_messages",0)}</div><div class="lbl">Total Messages</div></div>'
            f'<div class="stat"><div class="val">{cs.get("messages_today",0)}</div><div class="lbl">Msgs Today</div></div>'
            f'<div class="stat"><div class="val">{cs.get("active_fans_today",0)}</div><div class="lbl">Active Today</div></div>'
            f'<div class="stat"><div class="val">{stars_total:,}&#11088;</div><div class="lbl">Total Stars<br><small>~${stars_usd:.2f}</small></div></div>'
            f'<div class="stat"><div class="val">{cs.get("stars_today",0):,}&#11088;</div><div class="lbl">Stars Today</div></div>'
        )
    else:
        conv_html = '<div class="stat conv-offline"><div class="val">—</div><div class="lbl">Conversation stats<br><small>add STATS_URL env var</small></div></div>'

    # Daily bar chart data
    daily   = ps.get("daily",[])
    max_rev = max((d["revenue_cents"] for d in daily), default=1) or 1
    daily_bars = ""
    for d in daily:
        h   = max(4, int(d["revenue_cents"] / max_rev * 80))
        amt = f"${d['revenue_cents']/100:.0f}"
        daily_bars += f'<div class="bar-wrap"><div class="bar" style="height:{h}px" title="{amt}"></div><div class="bar-lbl">{d["date"]}<br><small>{amt}</small></div></div>'

    # Daily conv chart
    daily_conv = cs.get("daily_messages",[]) if cs else []
    max_msg    = max((d["count"] for d in daily_conv), default=1) or 1
    conv_bars  = ""
    for d in daily_conv:
        h = max(4, int(d["count"] / max_msg * 80))
        conv_bars += f'<div class="bar-wrap"><div class="bar conv-bar" style="height:{h}px" title="{d[\"count\"]} msgs"></div><div class="bar-lbl">{d["date"]}<br><small>{d["count"]}</small></div></div>'

    # Stars chart
    daily_stars = cs.get("daily_stars", []) if cs else []
    max_stars   = max((d.get("stars", 0) for d in daily_stars), default=1) or 1
    star_bars   = "".join(
        f'<div class="bar-wrap"><div class="bar" style="height:{max(4, int(d.get("stars",0)/max_stars*80))}px;background:#f59e0b"></div>'
        f'<div class="bar-lbl">{d["date"]}<br><small>{d.get("stars",0)}</small></div></div>'
        for d in daily_stars
    )

    # Top payers table
    payer_rows = ""
    for p in ps.get("top_payers",[]):
        payer_rows += f'<tr><td>{p["name"]}</td><td>{p["email"]}</td><td><strong>${p["amount"]/100:.2f}</strong></td><td>{p["count"]}</td></tr>'

    # Recent payments table
    pay_rows = ""
    for e in ps.get("recent",[])[:30]:
        clr = "#22c55e" if e.get("delivered") else ("#f59e0b" if e.get("status","") in ("CAPTURED","AUTHORIZED","COMPLETED","") else "#ef4444")
        dot = "✅" if e.get("delivered") else ("💵" if e.get("status","") in ("CAPTURED","AUTHORIZED","COMPLETED","") else "❌")
        bf  = " <span class='badge'>backfill</span>" if e.get("backfilled") else ""
        pay_rows += f"""<tr>
            <td>{e.get('ts','')[:16].replace('T',' ')}</td>
            <td><strong>{e.get('name','?')}</strong></td>
            <td style="color:{clr}">{dot} {e.get('amount_usd','?')}</td>
            <td>{e.get('email','')}</td>
            <td>{'✅ Delivered' if e.get('delivered') else '📬 Unmatched'}{bf}</td>
        </tr>"""

    # Top fans table
    fan_rows = ""
    for f in cs.get("top_fans",[])[:15] if cs else []:
        fan_rows += f'<tr><td>{f["name"]}</td><td>{f["chat_id"]}</td><td>{f["msg_count"]}</td><td>{"🔥"*min(f["heat"],5)}</td><td>{f["last_seen"]}</td></tr>'
    if not fan_rows:
        fan_rows = '<tr><td colspan=5 style="color:#555;text-align:center;padding:20px">Add STATS_URL env var to show fan data</td></tr>'

    now = time.strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>🩷 Bella Ops Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#f0f0f0;padding:20px;min-height:100vh}}
h1{{color:#f472b6;font-size:26px;margin-bottom:2px}}
.sub{{color:#666;font-size:13px;margin-bottom:24px}}
.section{{margin-bottom:36px}}
h2{{color:#f472b6;font-size:16px;font-weight:600;margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
.stat{{background:#141414;border:1px solid #222;border-radius:12px;padding:16px 20px;min-width:110px;flex:1}}
.stat .val{{font-size:26px;font-weight:700;color:#f472b6}}
.stat .lbl{{font-size:11px;color:#666;margin-top:4px;line-height:1.4}}
.conv-offline .val{{font-size:18px;color:#555}}
table{{width:100%;border-collapse:collapse;background:#111;border-radius:12px;overflow:hidden}}
th{{background:#1a1a1a;padding:10px 14px;text-align:left;font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.05em}}
td{{padding:10px 14px;border-top:1px solid #1a1a1a;font-size:13px}}
tr:hover td{{background:#161616}}
.badge{{background:#f472b620;color:#f472b6;padding:1px 6px;border-radius:4px;font-size:10px}}
.charts{{display:flex;gap:24px;margin-bottom:24px;flex-wrap:wrap}}
.chart{{background:#111;border:1px solid #1a1a1a;border-radius:12px;padding:16px;flex:1;min-width:280px}}
.chart-title{{font-size:12px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px}}
.bars{{display:flex;align-items:flex-end;gap:6px;height:90px}}
.bar-wrap{{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px}}
.bar{{background:#f472b6;border-radius:3px 3px 0 0;width:100%;min-width:8px;transition:.3s}}
.conv-bar{{background:#818cf8}}
.bar-lbl{{font-size:9px;color:#555;text-align:center;line-height:1.3}}
.refresh{{color:#444;font-size:11px;margin-top:32px;text-align:center}}
a{{color:#f472b6;text-decoration:none}}
</style>
<script>setTimeout(()=>location.reload(),60000)</script>
</head><body>
<h1>🩷 Bella Ops Dashboard</h1>
<p class="sub">bellavistaxo · Live operations · auto-refreshes every 60s · {now}</p>

<div class="section">
<h2>💰 Revenue Overview</h2>
<div class="stats">
  <div class="stat"><div class="val">{rev}</div><div class="lbl">Total Revenue</div></div>
  <div class="stat"><div class="val">{tp}</div><div class="lbl">Total Payments</div></div>
  <div class="stat"><div class="val">{td}</div><div class="lbl">Auto-Delivered</div></div>
  <div class="stat"><div class="val">{tum}</div><div class="lbl">Unmatched</div></div>
  <div class="stat"><div class="val">{tpf}</div><div class="lbl">Pending Fans</div></div>
  {conv_html}
</div></div>

<div class="charts">
  <div class="chart">
    <div class="chart-title">💵 Daily Revenue (7d)</div>
    <div class="bars">{daily_bars or '<div style="color:#333;margin:auto">no data</div>'}</div>
  </div>
  <div class="chart">
    <div class="chart-title">💬 Daily Messages (7d)</div>
    <div class="bars">{conv_bars or '<div style="color:#333;margin:auto;font-size:12px">Add STATS_URL for conversation data</div>'}</div>
  </div>
  <div class="chart">
    <div class="chart-title">&#11088; Daily Stars (7d)</div>
    <div class="bars">{star_bars or '<div style="color:#333;margin:auto;font-size:12px">No stars data yet</div>'}</div>
  </div>
</div>

<div class="section">
<h2>🌟 Top Payers (All Time)</h2>
<table><thead><tr><th>Name</th><th>Email</th><th>Total Paid</th><th>Payments</th></tr></thead>
<tbody>{payer_rows or '<tr><td colspan=4 style="color:#333;text-align:center;padding:20px">No payments yet</td></tr>'}</tbody></table>
</div>

<div class="section">
<h2>📋 Recent Transactions</h2>
<table><thead><tr><th>Time</th><th>Name</th><th>Amount</th><th>Email</th><th>Status</th></tr></thead>
<tbody>{pay_rows or '<tr><td colspan=5 style="color:#333;text-align:center;padding:20px">No transactions yet</td></tr>'}</tbody></table>
</div>

<div class="section">
<h2>👥 Active Fans (Most Recent)</h2>
<table><thead><tr><th>Name</th><th>Chat ID</th><th>Messages</th><th>Heat</th><th>Last Active</th></tr></thead>
<tbody>{fan_rows}</tbody></table>
</div>

<div class="refresh">Last updated: {now} · <a href="?token=bella-admin-2024">Refresh</a> · Commands: /stats /payments /history /fan /deliver</div>
</body></html>"""


# ── Sig validation ────────────────────────────────────────────────────────────
def valid_sig(body, hdr):
    if not WEBHOOK_SECRET: return True
    mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha1)
    return hmac.compare_digest(base64.b64encode(mac.digest()).decode(), hdr)


# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print(f"[http] {fmt % args}")
    def send_json(self, code, data):
        body=json.dumps(data,default=str).encode()
        self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def send_html(self, code, html):
        body=html.encode()
        self.send_response(code); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def require_admin(self, parsed):
        t=self.headers.get("X-Admin-Token",""); qs=parse_qs(parsed.query)
        return t or qs.get("token",[""])[0]

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/health":
            self.send_json(200,{"status":"ok","version":"v3"})
        elif p.path in ("/dashboard","/"):
            if self.require_admin(p) != ADMIN_TOKEN:
                self.send_json(401,{"error":"unauthorized"}); return
            ps = get_payment_stats(); cs = get_conv_stats()
            self.send_html(200, build_dashboard(ps, cs))
        elif p.path == "/payments":
            if self.require_admin(p) != ADMIN_TOKEN:
                self.send_json(401,{"error":"unauthorized"}); return
            log=load_json(PAYMENTS_LOG,[])
            self.send_json(200,{"count":len(log),"payments":list(reversed(log))})
        elif p.path == "/api/summary":
            if self.require_admin(p) != ADMIN_TOKEN:
                self.send_json(401,{"error":"unauthorized"}); return
            self.send_json(200,get_payment_stats())
        else:
            self.send_json(404,{"error":"not found"})

    def do_POST(self):
        length=int(self.headers.get("Content-Length",0))
        body=self.rfile.read(length); p=urlparse(self.path)

        if p.path == "/webhook":
            sig=self.headers.get("Poynt-Webhook-Signature","")
            if not valid_sig(body,sig): self.send_json(401,{"error":"bad sig"}); return
            self.send_json(200,{"ok":True})
            try:
                event=json.loads(body)
                threading.Thread(target=handle_payment_event,args=(event,),daemon=True).start()
            except Exception as e: print(f"[webhook] {e}")

        elif p.path == "/import-payments":
            # Bulk import historical payments (backfill)
            try:
                data   = json.loads(body)
                token  = data.get("token","") or self.headers.get("X-Admin-Token","")
                if token != ADMIN_TOKEN: self.send_json(401,{"error":"unauthorized"}); return
                new    = data.get("payments",[])
                log    = load_json(PAYMENTS_LOG,[])
                # Dedup by resource_id
                existing_ids = {e.get("resource_id") for e in log}
                added  = 0
                for e in new:
                    if e.get("resource_id") not in existing_ids:
                        log.append(e); added += 1
                save_json(PAYMENTS_LOG, log)
                print(f"[import] Added {added} new entries ({len(new)} submitted)")
                self.send_json(200,{"ok":True,"added":added,"total":len(log)})
            except Exception as e: self.send_json(500,{"error":str(e)})

        elif p.path == "/register-fan":
            try:
                data=json.loads(body); email=data.get("email","").lower().strip(); cid=data.get("chat_id")
                name=data.get("name","babe"); biz=data.get("biz_conn_id","")
                if not email or not cid: self.send_json(400,{"error":"email and chat_id required"}); return
                pending=load_json(PENDING_FILE,{})
                pending[email]={"chat_id":cid,"name":name,"biz_conn_id":biz,
                                "registered_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
                save_json(PENDING_FILE,pending)
                print(f"[register] {name} ({email}) -> {cid}")
                self.send_json(200,{"ok":True,"registered":email})
            except Exception as e: self.send_json(500,{"error":str(e)})

        elif p.path == "/check-payment":
            try:
                data=json.loads(body); cid=data.get("chat_id"); fname=data.get("name","babe")
                biz=data.get("biz_conn_id",""); amt_hint=data.get("amount_cents")
                if not cid: self.send_json(400,{"error":"chat_id required"}); return
                match=find_unmatched(hours=2,amount_cents=amt_hint)
                if match:
                    msg = CONTENT_MESSAGE.replace("{name}",fname) if CONTENT_MESSAGE else f"omg thank you SO much {fname}!! 🩷 I got your payment — I'll send your content right over ✨"
                    ok=send_telegram(cid,msg,biz)
                    if ok:
                        mark_delivered(match["resource_id"],cid,fname)
                        notify_owners(match.get("name","?"),match.get("amount_cents",0),match.get("email","?"),True,cid)
                        self.send_json(200,{"ok":True,"matched":True,"amount":match.get("amount_usd"),"payer":match.get("name")})
                    else: self.send_json(200,{"ok":False,"matched":True,"error":"telegram send failed"})
                else: self.send_json(200,{"ok":True,"matched":False})
            except Exception as e: self.send_json(500,{"error":str(e)})
        else:
            self.send_json(404,{"error":"not found"})


if __name__ == "__main__":
    print(f"[startup] Bella webhook v3 on port {PORT}")
    print(f"[startup] Owner IDs: {OWNER_CHAT_IDS}")
    print(f"[startup] Stats URL: {STATS_URL or 'not set'}")
    print(f"[startup] Content delivery: {'custom' if CONTENT_MESSAGE else 'placeholder mode'}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
