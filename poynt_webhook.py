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

DATA_DIR     = os.environ.get("DATA_DIR", "/data")
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


# ── Fanvue auto-refresh ──────────────────────────────────────────────────────
FANVUE_CLIENT_ID     = os.environ.get("FANVUE_CLIENT_ID","")
FANVUE_CLIENT_SECRET = os.environ.get("FANVUE_CLIENT_SECRET","")
FANVUE_REFRESH_TOKEN = os.environ.get("FANVUE_REFRESH_TOKEN","")

def fanvue_get_access_token():
    import urllib.parse as _up, base64 as _b64
    rt = FANVUE_REFRESH_TOKEN
    if not rt or not FANVUE_CLIENT_ID: return None
    creds = _b64.b64encode(f"{FANVUE_CLIENT_ID}:{FANVUE_CLIENT_SECRET}".encode()).decode()
    data = _up.urlencode({"grant_type":"refresh_token","refresh_token":rt}).encode()
    req = urllib.request.Request("https://auth.fanvue.com/oauth2/token", data=data,
          headers={"Content-Type":"application/x-www-form-urlencoded","Authorization":f"Basic {creds}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("access_token")
    except Exception as e:
        print(f"[fanvue_auth] {e}"); return None

def fanvue_refresh_stats():
    at = fanvue_get_access_token()
    if not at: return
    try:
        req = urllib.request.Request("https://api.fanvue.com/insights/earnings/summary",
              headers={"Authorization":f"Bearer {at}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        req2 = urllib.request.Request("https://api.fanvue.com/insights/top-spenders?limit=5",
               headers={"Authorization":f"Bearer {at}"})
        with urllib.request.urlopen(req2, timeout=15) as r2:
            sp_data = json.loads(r2.read())
        totals = data.get("totals",{}).get("allTime",{})
        gross  = totals.get("gross",0)
        net    = totals.get("net",0)
        bd     = data.get("breakdownBySource",{})
        spenders = [{"name":s["user"]["displayName"],"gross_cents":s["gross"],"gross":f'${s["gross"]/100:.2f}'}
                    for s in sp_data.get("data",[])]
        stats = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "source": "fanvue_api_auto",
            "earnings": {"all_time_gross_cents":gross,"all_time_net_cents":net,
                         "all_time_gross":f"${gross/100:.2f}","all_time_net":f"${net/100:.2f}",
                         "available_balance":"see Fanvue dashboard"},
            "breakdown": {k:{"gross_cents":v.get("gross",0),"gross":f'${v.get("gross",0)/100:.2f}'}
                          for k,v in bd.items() if v.get("gross",0)>0},
            "top_spenders": spenders
        }
        save_json(os.path.join(DATA_DIR,"fanvue_stats.json"), stats)
        print(f"[fanvue] Stats refreshed: ${gross/100:.2f} gross all time")
    except Exception as e:
        print(f"[fanvue_refresh] {e}")

def start_fanvue_scheduler():
    import threading as _t
    def _loop():
        fanvue_refresh_stats()  # run immediately on startup
        while True:
            _t.Event().wait(3600)  # refresh hourly
            fanvue_refresh_stats()
    if FANVUE_REFRESH_TOKEN:
        _t.Thread(target=_loop, daemon=True).start()
        print("[fanvue] Auto-refresh scheduler started (hourly)")

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
    ps = payment_stats
    cs = conv_stats or {}
    now_str = time.strftime("%Y-%m-%d %H:%M UTC")

    # Compute accurate metrics — exclude declined
    all_payments = ps.get("recent", [])
    captured = [p for p in all_payments if p.get("status","") in ("CAPTURED","AUTHORIZED","COMPLETED","") 
                and not p.get("event_type","").endswith("DECLINED")]
    declined = [p for p in all_payments if p.get("event_type","").endswith("DECLINED") 
                or p.get("status","") == "DECLINED"]
    revenue_cents = sum(p.get("amount_cents",0) for p in captured)
    delivered = sum(1 for p in captured if p.get("delivered"))
    unmatched = len(captured) - delivered
    pending_fans = ps.get("pending_fans", 0)

    # Conversation stats
    total_fans = cs.get("total_fans", "—")
    total_msgs = cs.get("total_messages", "—")
    msgs_today = cs.get("messages_today", "—")
    active_today = cs.get("active_fans_today", "—")
    stars_total = cs.get("stars_total", 0)
    stars_today = cs.get("stars_today", 0)
    conv_online = cs != {}

    # Build daily revenue chart
    daily = ps.get("daily", [])
    max_rev = max((d.get("revenue_cents",0) for d in daily), default=1) or 1
    daily_bars = "".join(
        '<div class="bar-wrap">'
        '<div class="bar" style="height:{}px;background:#f472b6" title="${:.2f}"></div>'
        '<div class="bar-lbl">{}<br><small>${:.0f}</small></div></div>'.format(
            max(4, int(d.get("revenue_cents",0)/max_rev*80)),
            d.get("revenue_cents",0)/100,
            d.get("date",""),
            d.get("revenue_cents",0)/100
        ) for d in daily
    )

    # Daily messages chart
    daily_conv = cs.get("daily_messages",[])
    max_msg = max((d.get("count",0) for d in daily_conv), default=1) or 1
    conv_bars = "".join(
        '<div class="bar-wrap">'
        '<div class="bar conv-bar" style="height:{}px" title="{} msgs"></div>'
        '<div class="bar-lbl">{}<br><small>{}</small></div></div>'.format(
            max(4, int(d.get("count",0)/max_msg*80)), d.get("count",0), d.get("date",""), d.get("count",0)
        ) for d in daily_conv
    ) if daily_conv else ""

    # Daily stars chart
    daily_stars = cs.get("daily_stars", [])
    max_stars = max((d.get("stars",0) for d in daily_stars), default=1) or 1
    star_bars = "".join(
        '<div class="bar-wrap">'
        '<div class="bar" style="height:{}px;background:#f59e0b" title="{}⭐"></div>'
        '<div class="bar-lbl">{}<br><small>{}⭐</small></div></div>'.format(
            max(4, int(d.get("stars",0)/max_stars*80)), d.get("stars",0), d.get("date",""), d.get("stars",0)
        ) for d in daily_stars
    ) if daily_stars else ""

    # Top payers
    from collections import defaultdict
    payer_map = defaultdict(lambda: {"name":"","amount":0,"count":0,"email":"","chat_id":None})
    for p in captured:
        k = p.get("email","?")
        payer_map[k]["name"]  = p.get("name","?")
        payer_map[k]["email"] = k
        payer_map[k]["amount"] += p.get("amount_cents",0)
        payer_map[k]["count"]  += 1
        if p.get("chat_id"): payer_map[k]["chat_id"] = p.get("chat_id")
    top_payers = sorted(payer_map.values(), key=lambda x: x["amount"], reverse=True)[:10]
    payer_rows = "".join(
        '<tr><td>{}</td><td>{}</td><td><strong>${:.2f}</strong></td><td>{}</td><td>{}</td></tr>'.format(
            p["name"], p["email"], p["amount"]/100, p["count"],
            '<span class="badge green">chat '+str(p["chat_id"])+'</span>' if p["chat_id"] else '<span class="badge">unmatched</span>'
        ) for p in top_payers
    ) or '<tr><td colspan=5 class="empty">No payments yet</td></tr>'

    # All payments table (data for JS filter)
    pay_data = json.dumps(list(reversed(all_payments)), default=str)

    # Top fans table
    fan_rows = ""
    for f in cs.get("top_fans",[])[:20] if cs else []:
        heat_dots = "🔥" * min(f.get("heat",1),5)
        fan_rows += '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            f.get("name","?"), f.get("chat_id",""), f.get("msg_count",""),
            heat_dots, f.get("last_seen","?")
        )
    if not fan_rows:
        fan_rows = '<tr><td colspan=5 class="empty">{}</td></tr>'.format(
            "Add STATS_URL env var to show fan data" if not conv_online else "No fans yet"
        )

    return """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>🩷 Bella Ops Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#f0f0f0;padding:16px;overflow-x:hidden}
@media(max-width:600px){body{padding:10px}.charts{flex-direction:column!important}.stat{min-width:calc(50% - 8px)!important}.bar-lbl{font-size:8px}.stats{gap:8px}table{font-size:12px}th,td{padding:7px 8px!important}h1{font-size:20px}h2{font-size:13px}.search-input{width:100%!important}.filters{flex-wrap:wrap;gap:6px}.filter-btn{font-size:11px;padding:4px 10px}}
h1{color:#f472b6;font-size:24px;margin-bottom:2px}
.sub{color:#555;font-size:13px;margin-bottom:24px}
h2{color:#f472b6;font-size:14px;font-weight:600;margin:28px 0 10px;text-transform:uppercase;letter-spacing:.06em}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.stat{background:#141414;border:1px solid #222;border-radius:10px;padding:14px 18px;flex:1;min-width:100px}
.stat .val{font-size:24px;font-weight:700;color:#f472b6}
.stat .lbl{font-size:11px;color:#555;margin-top:3px}
.stat .sub2{font-size:11px;color:#888;margin-top:2px}
.charts{display:flex;gap:14px;margin-bottom:8px;flex-wrap:wrap}
.chart{background:#111;border:1px solid #1a1a1a;border-radius:10px;padding:14px;flex:1;min-width:220px}
.chart-title{font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.bars{display:flex;align-items:flex-end;gap:5px;height:90px}
.bar-wrap{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px}
.bar{background:#f472b6;border-radius:3px 3px 0 0;width:100%}
.conv-bar{background:#818cf8}
.bar-lbl{font-size:9px;color:#444;text-align:center;line-height:1.3}
table{width:100%;border-collapse:collapse;background:#111;border-radius:10px;overflow:hidden;margin-bottom:8px}
th{background:#181818;padding:9px 12px;text-align:left;font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.05em}
td{padding:9px 12px;border-top:1px solid #1a1a1a;font-size:13px}
tr:hover td{background:#161616}
.empty{color:#333;text-align:center;padding:24px!important}
.badge{background:#f472b620;color:#f472b6;padding:2px 7px;border-radius:4px;font-size:11px}
.badge.green{background:#22c55e20;color:#22c55e}
.badge.red{background:#ef444420;color:#ef4444}
.badge.yellow{background:#f59e0b20;color:#f59e0b}
.filters{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.filter-btn{background:#1a1a1a;border:1px solid #333;color:#888;padding:5px 14px;border-radius:6px;cursor:pointer;font-size:12px}
.filter-btn.active{background:#f472b620;border-color:#f472b6;color:#f472b6}
.search-input{background:#1a1a1a;border:1px solid #333;color:#f0f0f0;padding:5px 12px;border-radius:6px;font-size:12px;width:200px}
.search-input:focus{outline:none;border-color:#f472b6}
.dot-green{color:#22c55e}
.dot-yellow{color:#f59e0b}
.dot-red{color:#ef4444}
.footer{color:#333;font-size:11px;margin-top:28px;text-align:center}
a{color:#f472b6;text-decoration:none}
</style>
<script>setTimeout(()=>location.reload(),60000)</script>
</head><body>
<h1>🩷 Bella Ops Dashboard</h1>
<p class="sub">bellavistaxo · live ops · auto-refreshes 60s · """ + now_str + """</p>

<h2>💰 Revenue</h2>
<div class="stats">
  <div class="stat"><div class="val">$""" + f"{revenue_cents/100:.2f}" + """</div><div class="lbl">Total Revenue</div><div class="sub2">from """ + str(len(captured)) + """ payments</div></div>
  <div class="stat"><div class="val">""" + str(len(captured)) + """</div><div class="lbl">Captured Payments</div><div class="sub2">""" + str(len(declined)) + """ declined</div></div>
  <div class="stat"><div class="val">""" + str(delivered) + """</div><div class="lbl">Auto-Delivered</div><div class="sub2">content sent</div></div>
  <div class="stat"><div class="val">""" + str(unmatched) + """</div><div class="lbl">Unmatched</div><div class="sub2">no chat ID yet</div></div>
  <div class="stat"><div class="val">""" + str(pending_fans) + """</div><div class="lbl">Pending Fans</div><div class="sub2">awaiting payment</div></div>
</div>

<h2>💬 Conversations""" + ("" if conv_online else ' <span class="badge yellow">stats offline</span>') + """</h2>
<div class="stats">
  <div class="stat"><div class="val">""" + str(total_fans) + """</div><div class="lbl">Total Fans</div></div>
  <div class="stat"><div class="val">""" + str(total_msgs) + """</div><div class="lbl">Total Messages</div></div>
  <div class="stat"><div class="val">""" + str(msgs_today) + """</div><div class="lbl">Messages Today</div></div>
  <div class="stat"><div class="val">""" + str(active_today) + """</div><div class="lbl">Active Today</div></div>
  <div class="stat"><div class="val">""" + str(stars_total) + """⭐</div><div class="lbl">Stars Received</div><div class="sub2">via bot invoices</div></div>
  <div class="stat"><div class="val">""" + str(stars_today) + """⭐</div><div class="lbl">Stars Today</div></div>
</div>

<div class="charts">
  <div class="chart"><div class="chart-title">💵 Daily Revenue (7d)</div>
    <div class="bars">""" + (daily_bars or '<div style="color:#333;margin:auto;font-size:11px">No data</div>') + """</div></div>
  <div class="chart"><div class="chart-title">💬 Daily Messages (7d)</div>
    <div class="bars">""" + (conv_bars or '<div style="color:#333;margin:auto;font-size:11px">Connecting...</div>') + """</div></div>
  <div class="chart"><div class="chart-title">⭐ Daily Stars (7d)</div>
    <div class="bars">""" + (star_bars or '<div style="color:#333;margin:auto;font-size:11px">No stars yet via bot</div>') + """</div></div>
</div>

<h2>🌟 Top Payers</h2>
<table><thead><tr><th>Name</th><th>Email</th><th>Total Paid</th><th>Payments</th><th>Matched</th></tr></thead>
<tbody>""" + payer_rows + """</tbody></table>

<h2>📋 All Transactions</h2>
<div class="filters">
  <button class="filter-btn active" onclick="filterPay('all',this)">All (""" + str(len(all_payments)) + """)</button>
  <button class="filter-btn" onclick="filterPay('captured',this)">✅ Captured (""" + str(len(captured)) + """)</button>
  <button class="filter-btn" onclick="filterPay('declined',this)">❌ Declined (""" + str(len(declined)) + """)</button>
  <button class="filter-btn" onclick="filterPay('unmatched',this)">📬 Unmatched (""" + str(unmatched) + """)</button>
  <input class="search-input" id="paySearch" oninput="filterPay(currentFilter,null)" placeholder="Search name / email…">
</div>
<table id="payTable"><thead><tr><th>Date</th><th>Name</th><th>Amount</th><th>Email</th><th>Status</th><th>Chat ID</th></tr></thead>
<tbody id="payBody"></tbody></table>

<h2>👥 Active Fans</h2>
<div class="filters">
  <input class="search-input" id="fanSearch" oninput="filterFans()" placeholder="Search fan name…" style="width:250px">
</div>
<table id="fanTable"><thead><tr><th>Name</th><th>Chat ID</th><th>Messages</th><th>Heat</th><th>Last Active</th></tr></thead>
<tbody id="fanBody">""" + fan_rows + """</tbody></table>

<div id="fanvue-section" style="display:none">
<h2>🌸 Fanvue Earnings <span class="badge" id="fv-updated"></span></h2>
<div class="stats" id="fv-stats"></div>
<div class="charts">
  <div class="chart" id="fv-breakdown-wrap"><div class="chart-title">Revenue by Source</div><div id="fv-breakdown"></div></div>
  <div class="chart"><div class="chart-title">&#11088; Top Spenders</div><div id="fv-spenders"></div></div>
</div>
</div>

<p class="footer">Updated: """ + now_str + """ · <a href="?token=bella-admin-2024">Refresh</a> · <a href="/payments?token=bella-admin-2024">Raw JSON</a></p>

<script>
const PAYMENTS = """ + pay_data + """;
let currentFilter = 'all';

function renderPayRows(rows) {
  const q = (document.getElementById('paySearch').value||'').toLowerCase();
  const filtered = rows.filter(p => {
    if (q && !((p.name||'').toLowerCase().includes(q)||(p.email||'').toLowerCase().includes(q))) return false;
    return true;
  });
  const tbody = document.getElementById('payBody');
  if (!filtered.length) { tbody.innerHTML = '<tr><td colspan=6 class="empty">No matching payments</td></tr>'; return; }
  tbody.innerHTML = filtered.map(p => {
    const isDeclined = (p.event_type||'').endsWith('DECLINED') || p.status === 'DECLINED';
    const isCaptured = p.status === 'CAPTURED' || p.status === 'AUTHORIZED' || p.status === 'COMPLETED';
    const dot = p.delivered ? '<span class="dot-green">✅</span>' : (isDeclined ? '<span class="dot-red">❌</span>' : (isCaptured ? '<span class="dot-yellow">📬</span>' : '<span>?</span>'));
    const bf = p.backfilled ? ' <span class="badge" style="font-size:9px">backfill</span>' : '';
    return '<tr>'
      +'<td>'+(p.ts||'').slice(0,10)+'</td>'
      +'<td><strong>'+(p.name||'?')+'</strong>'+bf+'</td>'
      +'<td>'+dot+' '+(p.amount_usd||'?')+'</td>'
      +'<td style="color:#666;font-size:12px">'+(p.email||'')+'</td>'
      +'<td>'+(p.status||'?')+'</td>'
      +'<td style="font-size:12px;color:#888">'+(p.chat_id||'—')+'</td>'
      +'</tr>';
  }).join('');
}

function filterPay(type, btn) {
  currentFilter = type;
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  let rows = PAYMENTS;
  if (type === 'captured') rows = rows.filter(p => !((p.event_type||'').endsWith('DECLINED')||p.status==='DECLINED'));
  else if (type === 'declined') rows = rows.filter(p => (p.event_type||'').endsWith('DECLINED')||p.status==='DECLINED');
  else if (type === 'unmatched') rows = rows.filter(p => !p.delivered && !((p.event_type||'').endsWith('DECLINED')||p.status==='DECLINED'));
  renderPayRows(rows);
}

function filterFans() {
  const q = (document.getElementById('fanSearch').value||'').toLowerCase();
  document.querySelectorAll('#fanBody tr').forEach(tr => {
    const text = tr.textContent.toLowerCase();
    tr.style.display = (!q || text.includes(q)) ? '' : 'none';
  });
}

filterPay('all', document.querySelector('.filter-btn.active'));

// Load Fanvue data
fetch('/api/fanvue?token=bella-admin-2024')
  .then(r=>r.json())
  .then(fv=>{
    if (!fv || !fv.earnings) return;
    document.getElementById('fanvue-section').style.display='block';
    const e=fv.earnings, a=fv.account||{}, bd=fv.breakdown||{}, sp=fv.top_spenders||[];
    document.getElementById('fv-updated').textContent=fv.updated_at?fv.updated_at.slice(0,16).replace('T',' ')+' UTC':'';
    document.getElementById('fv-stats').innerHTML=
      '<div class="stat"><div class="val">'+e.all_time_gross+'</div><div class="lbl">All Time Gross</div></div>'+
      '<div class="stat"><div class="val">'+e.all_time_net+'</div><div class="lbl">All Time Net</div></div>'+
      '<div class="stat"><div class="val">'+e.available_balance+'</div><div class="lbl">Available Balance</div></div>'+
      '<div class="stat"><div class="val">'+(a.subscribers||0)+'</div><div class="lbl">Subscribers</div></div>'+
      '<div class="stat"><div class="val">'+(a.followers||0)+'</div><div class="lbl">Followers</div></div>';
    const bdKeys=[['renewals','Renewals'],['posts','Posts'],['subscriptions','Subs'],['tips','Tips'],['messages','Messages']];
    document.getElementById('fv-breakdown').innerHTML='<table style="margin:0"><thead><tr><th>Source</th><th>Gross</th></tr></thead><tbody>'+
      bdKeys.map(([k,l])=>bd[k]?'<tr><td>'+l+'</td><td>'+bd[k].gross+'</td></tr>':'').join('')+'</tbody></table>';
    document.getElementById('fv-spenders').innerHTML='<table style="margin:0"><thead><tr><th>Fan</th><th>Spent</th></tr></thead><tbody>'+
      sp.map(s=>'<tr><td>'+s.name+'</td><td>'+s.gross+'</td></tr>').join('')+'</tbody></table>';
  }).catch(()=>{});
</script>
</body></html>"""

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
        elif p.path == "/api/fanvue":
            if self.require_admin(p) != ADMIN_TOKEN:
                self.send_json(401,{"error":"unauthorized"}); return
            fpath = os.path.join(DATA_DIR,"fanvue_stats.json")
            stats = load_json(fpath, {})
            self.send_json(200, stats)

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
                # Support reset flag to wipe existing log before import
                if data.get("reset", False):
                    save_json(PAYMENTS_LOG, [])
                log    = load_json(PAYMENTS_LOG,[])
                # Dedup by resource_id AND by name+amount+date (prevents double-counting manual backfills)
                existing_ids = {e.get("resource_id") for e in log}
                def _sig(e):
                    # fingerprint: name + amount + date (day only)
                    return (str(e.get("name","")).lower().strip(),
                            e.get("amount_cents",0),
                            str(e.get("ts",""))[:10])
                existing_sigs = {_sig(e) for e in log}
                added  = 0
                for e in new:
                    if e.get("resource_id") in existing_ids:
                        continue
                    if _sig(e) in existing_sigs:
                        continue  # same name+amount+date already exists (prevents manual/API double-count)
                    log.append(e); added += 1
                    existing_ids.add(e.get("resource_id"))
                    existing_sigs.add(_sig(e))
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

        elif p.path == "/overwrite-payments":
            # Direct overwrite of entire payments log — bypasses dedup
            try:
                data  = json.loads(body)
                token = data.get("token","") or self.headers.get("X-Admin-Token","")
                if token != ADMIN_TOKEN: self.send_json(401,{"error":"unauthorized"}); return
                payments = data.get("payments",[])
                save_json(PAYMENTS_LOG, payments)
                print(f"[overwrite] Log replaced with {len(payments)} entries")
                self.send_json(200,{"ok":True,"count":len(payments)})
            except Exception as e: self.send_json(500,{"error":str(e)})

        elif p.path == "/store-pkce":
            # Store PKCE code_verifier before OAuth redirect
            try:
                data  = json.loads(body)
                token = data.get("token","") or self.headers.get("X-Admin-Token","")
                if token != ADMIN_TOKEN: self.send_json(401,{"error":"unauthorized"}); return
                save_json(os.path.join(DATA_DIR,"fanvue_pkce.json"), {
                    "code_verifier": data.get("code_verifier",""),
                    "state": data.get("state",""),
                    "stored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
                })
                self.send_json(200,{"ok":True})
            except Exception as e: self.send_json(500,{"error":str(e)})

        elif p.path == "/update-fanvue":
            # Store fresh Fanvue stats (called from Hyperagent after MCP fetch)
            try:
                data  = json.loads(body)
                token = data.get("token","") or self.headers.get("X-Admin-Token","")
                if token != ADMIN_TOKEN: self.send_json(401,{"error":"unauthorized"}); return
                stats = data.get("stats",{})
                stats["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
                save_json(os.path.join(DATA_DIR,"fanvue_stats.json"), stats)
                self.send_json(200,{"ok":True})
            except Exception as e: self.send_json(500,{"error":str(e)})

        elif p.path == "/oauth/callback":
            # Fanvue OAuth 2.0 callback — captures code and exchanges for tokens
            qs = parse_qs(p.query)
            code = qs.get("code",[""])[0]
            error = qs.get("error",[""])[0]
            if error:
                html = f"<h2>OAuth Error: {error}</h2><p>{qs.get('error_description',[''])[0]}</p>"
                self.send_html(400, html)
                return
            if not code:
                self.send_html(400, "<h2>No code received</h2>")
                return
            # Exchange code for tokens
            import urllib.parse as _up
            fv_client_id     = os.environ.get("FANVUE_CLIENT_ID","")
            fv_client_secret = os.environ.get("FANVUE_CLIENT_SECRET","")
            redirect_uri     = f"https://bella-poynt-webhook-production.up.railway.app/oauth/callback"
            # PKCE: code_verifier is stored in fanvue_pkce.json on the volume
            pkce = load_json(os.path.join(DATA_DIR,"fanvue_pkce.json"), {})
            code_verifier = pkce.get("code_verifier","")
            token_params = {
                "grant_type":"authorization_code","code":code,
                "redirect_uri":redirect_uri,
                "client_id":fv_client_id
            }
            if code_verifier:
                token_params["code_verifier"] = code_verifier
            else:
                token_params["client_secret"] = fv_client_secret
            token_data = _up.urlencode(token_params).encode()
            req = urllib.request.Request("https://auth.fanvue.com/oauth2/token", data=token_data,
                  headers={"Content-Type":"application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    tokens = json.loads(r.read())
                refresh_token = tokens.get("refresh_token","")
                access_token  = tokens.get("access_token","")
                expires_in    = tokens.get("expires_in",0)
                # Save to fanvue_tokens.json on the volume
                save_json(os.path.join(DATA_DIR,"fanvue_tokens.json"), {
                    "refresh_token": refresh_token, "access_token": access_token,
                    "expires_in": expires_in, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
                })
                print(f"[oauth] Fanvue tokens saved. Refresh token: {refresh_token[:20]}...")
                html = (f"<h2>Connected!</h2>"
                        f"<p>Access token expires in: {expires_in}s</p>"
                        f"<p>Refresh token saved to Railway volume.</p>"
                        f"<p style='font-family:monospace;background:#eee;padding:10px;word-break:break-all'>"
                        f"<b>Copy this refresh token to Railway FANVUE_REFRESH_TOKEN env var:</b><br><br>"
                        f"{refresh_token}</p>"
                        f"<p>You can now close this tab.</p>")
                self.send_html(200, html)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                self.send_html(400, f"<h2>Token exchange failed (HTTP {e.code})</h2><pre>{err_body}</pre>")
            except Exception as e:
                self.send_html(500, f"<h2>Error</h2><p>{e}</p>")

        elif p.path == "/fanvue-webhook":
            # Fanvue real-time event webhook
            # No signature verification yet — events trigger a stats refresh
            try:
                event = json.loads(body)
                event_type = event.get("event","") or event.get("type","")
                print(f"[fanvue_webhook] event={event_type}")
                # Refresh stats immediately on any Fanvue event
                import threading as _fvt
                _fvt.Thread(target=fanvue_refresh_stats, daemon=True).start()
                # Also notify owners for earnings events
                earnings_events = {"purchase_received","tip_received","item_purchased","new_subscriber"}
                if event_type.lower().replace("-","_") in earnings_events:
                    fan = event.get("user",{}).get("displayName","Fan") or event.get("fan",{}).get("displayName","Fan")
                    amount = event.get("amount",0) or event.get("price",0)
                    msg = "Fanvue " + event_type + chr(10) + "Fan: " + fan + (chr(10) + "$"+str(round(amount/100,2)) if amount else "")
                    for oid in OWNER_CHAT_IDS: send_telegram(oid, msg)
                self.send_json(200, {"ok":True})
            except Exception as e:
                print(f"[fanvue_webhook] error: {e}")
                self.send_json(200, {"ok":True})  # always 200 so Fanvue doesn't retry

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
    start_fanvue_scheduler()
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
