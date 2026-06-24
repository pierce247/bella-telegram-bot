#!/usr/bin/env python3
"""
Bella Poynt Payment Webhook Listener v2
- Instant Poynt payment events
- Smart matching: auto-matches recent unmatched payment when fan claims they paid
- Owner Telegram notification on every captured payment
- /payments admin endpoint
- /register-fan for email pre-registration
- /check-payment for bot to verify a claim without email
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
OWNER_CHAT_ID    = int(os.environ.get("OWNER_CHAT_ID", "8635601598"))
CONTENT_MESSAGE  = os.environ.get("CONTENT_MESSAGE",
    "omg thank you SO much!! 🩷✨\n\nhere's your exclusive access → https://linktr.ee/bellavistaxo\n\nyou're officially one of my faves now 😏🔥")
BUSINESS_ID      = "8b2a6d7f-7a1f-4a96-9ea5-abc73755d69a"
PORT             = int(os.environ.get("PORT", 8080))
MATCH_WINDOW_HRS = 2   # hours to look back for unmatched payments

DATA_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PAYMENTS_LOG = os.path.join(DATA_DIR, "payments_log.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending_fans.json")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()


# ── Helpers ─────────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with _lock:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# ── Poynt auth ───────────────────────────────────────────────────────────────
def get_poynt_token():
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        from cryptography.hazmat.backends import default_backend
        import uuid as _uuid

        app_id = "urn:aid:" + POYNT_APP_ID_RAW.split("urn:aid:")[-1].strip()
        clean  = POYNT_SECRET_RAW.replace("-----BEGIN RSA PRIVATE KEY----- ", "").replace(" -----END RSA PRIVATE KEY-----", "").replace(" ", "")
        pem    = "-----BEGIN RSA PRIVATE KEY-----\n"
        for i in range(0, len(clean), 64):
            pem += clean[i:i+64] + "\n"
        pem += "-----END RSA PRIVATE KEY-----\n"
        key = serialization.load_pem_private_key(pem.encode(), password=None, backend=default_backend())

        def b64u(d):
            if isinstance(d, str): d = d.encode()
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        now    = int(time.time())
        hdr    = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claims = {"iss": app_id, "sub": app_id, "aud": "https://services.poynt.net",
                  "iat": now, "exp": now + 300, "jti": str(_uuid.uuid4())}
        pay    = b64u(json.dumps(claims))
        sig_in = f"{hdr}.{pay}".encode()
        sig    = base64.urlsafe_b64encode(key.sign(sig_in, asym_padding.PKCS1v15(), hashes.SHA256())).rstrip(b"=").decode()
        jwt    = f"{hdr}.{pay}.{sig}"
        data   = f"grantType=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt}".encode()
        req    = urllib.request.Request("https://services.poynt.net/token", data=data,
                 headers={"Content-Type": "application/x-www-form-urlencoded",
                          "api-version": "1.2", "Poynt-Request-Id": str(_uuid.uuid4())})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["accessToken"]
    except Exception as e:
        print(f"[poynt_auth] {e}")
        return None

def poynt_get(path):
    import uuid as _uuid
    token = get_poynt_token()
    if not token:
        return None
    req = urllib.request.Request(f"https://services.poynt.net{path}",
          headers={"Authorization": f"BEARER {token}", "api-version": "1.2",
                   "Poynt-Request-Id": str(_uuid.uuid4())})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[poynt_get] {e}")
        return None


# ── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(chat_id, text, biz_conn_id=""):
    payload = {"chat_id": int(chat_id), "text": text}
    if biz_conn_id:
        payload["business_connection_id"] = biz_conn_id
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"[telegram] {e}")
        return False

def notify_owner(name, amount_cents, email, delivered, chat_id=None):
    """Send payment alert to Pierce's Telegram."""
    amt    = f"${amount_cents/100:.2f}" if amount_cents else "?"
    status = "✅ delivered" if delivered else "📬 logged (no fan registered)"
    fan    = f"chat {chat_id}" if chat_id else "unknown chat"
    msg    = (f"💰 New payment!\n"
              f"👤 {name}\n"
              f"💵 {amt}\n"
              f"📧 {email}\n"
              f"📲 {fan}\n"
              f"📦 {status}")
    send_telegram(OWNER_CHAT_ID, msg)


# ── Smart payment matching ────────────────────────────────────────────────────
def find_unmatched_payment(hours=MATCH_WINDOW_HRS, amount_cents=None):
    """
    Find a single unmatched captured payment within the last `hours` hours.
    Optionally filter by amount. Returns the log entry or None.
    """
    log      = load_json(PAYMENTS_LOG, [])
    cutoff   = time.time() - (hours * 3600)
    matches  = []
    for entry in log:
        if entry.get("delivered"):
            continue
        if entry.get("status") not in ("CAPTURED", "AUTHORIZED", "COMPLETED", ""):
            continue
        try:
            ts = time.mktime(time.strptime(entry["ts"], "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        if amount_cents and entry.get("amount_cents") != amount_cents:
            continue
        matches.append(entry)
    return matches[0] if len(matches) == 1 else None

def mark_delivered(resource_id, chat_id, fan_name=""):
    """Mark a log entry as delivered and set its chat_id."""
    log = load_json(PAYMENTS_LOG, [])
    for entry in log:
        if entry.get("resource_id") == resource_id:
            entry["delivered"]  = True
            entry["chat_id"]    = chat_id
            if fan_name:
                entry["fan_name"] = fan_name
            break
    save_json(PAYMENTS_LOG, log)


# ── Payment event handler ─────────────────────────────────────────────────────
def handle_payment_event(event):
    event_type   = event.get("eventType", "")
    resource_id  = event.get("resourceId", "")
    links        = event.get("links", [])
    resource_url = links[0].get("href", "") if links else ""

    print(f"[payment] event={event_type} resource={resource_id}")

    # Fetch full transaction details
    txn = None
    if resource_url:
        path = resource_url.replace("https://services.poynt.net", "")
        txn  = poynt_get(path)

    email = ""; name = ""; amount = 0; status = ""
    if txn:
        if "fundingSource" in txn:
            card   = txn.get("fundingSource", {}).get("card", {})
            name   = card.get("cardHolderFullName", "")
            email  = txn.get("receiptEmailAddress", "")
            amount = txn.get("amounts", {}).get("transactionAmount", 0)
            status = txn.get("status", "")
        elif "transactions" in txn:
            t      = txn.get("transactions", [{}])[0]
            card   = t.get("fundingSource", {}).get("card", {})
            name   = card.get("cardHolderFullName", "")
            email  = t.get("receiptEmailAddress", "")
            amount = txn.get("amounts", {}).get("netTotal", 0)
            status = txn.get("statuses", {}).get("transactionStatusSummary", "")

    log   = load_json(PAYMENTS_LOG, [])
    entry = {
        "ts":           time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type":   event_type,
        "resource_id":  resource_id,
        "name":         name,
        "email":        email.lower(),
        "amount_cents": amount,
        "amount_usd":   f"${amount/100:.2f}" if amount else "?",
        "status":       status,
        "chat_id":      None,
        "delivered":    False,
    }

    delivered = False
    matched_chat = None

    # Only deliver for successful payments
    if status in ("CAPTURED", "AUTHORIZED", "COMPLETED", "") and email:
        # Check pre-registered fans first
        pending = load_json(PENDING_FILE, {})
        match   = pending.get(email.lower())
        if match:
            chat_id  = match.get("chat_id")
            biz_conn = match.get("biz_conn_id", "")
            fan_name = match.get("name", "babe")
            msg      = CONTENT_MESSAGE.replace("{name}", fan_name)
            ok       = send_telegram(chat_id, msg, biz_conn)
            if ok:
                entry["chat_id"]  = chat_id
                entry["delivered"] = True
                delivered = True
                matched_chat = chat_id
                del pending[email.lower()]
                save_json(PENDING_FILE, pending)
                print(f"[payment] delivered to {fan_name} chat={chat_id}")

    log.append(entry)
    save_json(PAYMENTS_LOG, log)

    # Notify owner regardless
    if status in ("CAPTURED", "AUTHORIZED", "COMPLETED", "") and name:
        notify_owner(name, amount, email, delivered, matched_chat)


# ── Signature validation ───────────────────────────────────────────────────────
def valid_sig(body, sig_header):
    if not WEBHOOK_SECRET:
        return True
    mac      = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode()
    return hmac.compare_digest(expected, sig_header)


# ── HTTP handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[http] {fmt % args}")

    def send_json(self, code, data):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def require_admin(self, parsed):
        token = self.headers.get("X-Admin-Token", "")
        qs    = parse_qs(parsed.query)
        return token or qs.get("token", [""])[0]

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.send_json(200, {"status": "ok", "service": "bella-poynt-webhook"})

        elif parsed.path == "/payments":
            token = self.require_admin(parsed)
            if token != ADMIN_TOKEN:
                self.send_json(401, {"error": "unauthorized"}); return
            log = load_json(PAYMENTS_LOG, [])
            self.send_json(200, {"count": len(log), "payments": list(reversed(log))})

        elif parsed.path == "/dashboard":
            token = self.require_admin(parsed)
            if token != ADMIN_TOKEN:
                self.send_json(401, {"error": "unauthorized"}); return
            log     = load_json(PAYMENTS_LOG, [])
            pending = load_json(PENDING_FILE, {})
            total   = sum(e.get("amount_cents", 0) for e in log if e.get("status") in ("CAPTURED","AUTHORIZED","COMPLETED",""))
            deliv   = sum(1 for e in log if e.get("delivered"))
            undeliv = sum(1 for e in log if not e.get("delivered") and e.get("status") in ("CAPTURED","AUTHORIZED","COMPLETED",""))
            rows    = ""
            for e in reversed(log):
                color = "#22c55e" if e.get("delivered") else ("#f59e0b" if e.get("status") in ("CAPTURED","AUTHORIZED","COMPLETED","") else "#ef4444")
                dot   = "🟢" if e.get("delivered") else ("🟡" if e.get("status") in ("CAPTURED","AUTHORIZED","COMPLETED","") else "🔴")
                rows += f"""<tr>
                  <td>{e.get('ts','')[:16].replace('T',' ')}</td>
                  <td><strong>{e.get('name','?')}</strong></td>
                  <td>{e.get('amount_usd','?')}</td>
                  <td style="color:{color}">{dot} {e.get('status','?')}</td>
                  <td>{e.get('email','')}</td>
                  <td>{'✅ Delivered' if e.get('delivered') else '📬 Pending'}</td>
                </tr>"""
            pending_rows = ""
            for email, fan in pending.items():
                pending_rows += f"<tr><td>{email}</td><td>{fan.get('name','?')}</td><td>chat {fan.get('chat_id','?')}</td><td>{fan.get('registered_at','')[:16]}</td></tr>"
            html = f"""<!DOCTYPE html><html><head><title>Bella Payments Dashboard</title>
            <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f0f;color:#f0f0f0;margin:0;padding:20px}}
              h1{{color:#f472b6;margin-bottom:4px}}p.sub{{color:#888;margin-top:0;font-size:14px}}
              .stats{{display:flex;gap:16px;margin:20px 0;flex-wrap:wrap}}
              .stat{{background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:16px 24px;min-width:120px}}
              .stat .val{{font-size:28px;font-weight:700;color:#f472b6}}
              .stat .lbl{{font-size:12px;color:#888;margin-top:4px}}
              table{{width:100%;border-collapse:collapse;background:#1a1a1a;border-radius:12px;overflow:hidden;margin-bottom:32px}}
              th{{background:#2a2a2a;padding:12px 16px;text-align:left;font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.05em}}
              td{{padding:12px 16px;border-top:1px solid #222;font-size:14px}}
              tr:hover td{{background:#222}}
              h2{{color:#f472b6;margin-top:32px}}
              .badge{{background:#f472b620;color:#f472b6;padding:2px 8px;border-radius:99px;font-size:12px}}
            </style></head><body>
            <h1>🩷 Bella Payments Dashboard</h1>
            <p class="sub">Live payment tracking · bellavistaxo</p>
            <div class="stats">
              <div class="stat"><div class="val">${total/100:.2f}</div><div class="lbl">Total Revenue</div></div>
              <div class="stat"><div class="val">{len(log)}</div><div class="lbl">Total Events</div></div>
              <div class="stat"><div class="val">{deliv}</div><div class="lbl">Delivered</div></div>
              <div class="stat"><div class="val">{undeliv}</div><div class="lbl">Awaiting Match</div></div>
              <div class="stat"><div class="val">{len(pending)}</div><div class="lbl">Pending Fans</div></div>
            </div>
            <h2>Recent Payments</h2>
            <table><thead><tr><th>Time</th><th>Customer</th><th>Amount</th><th>Status</th><th>Email</th><th>Delivery</th></tr></thead>
            <tbody>{rows or '<tr><td colspan=6 style="color:#555;text-align:center;padding:32px">No payments yet</td></tr>'}</tbody></table>
            <h2>Fans Awaiting Payment <span class="badge">{len(pending)}</span></h2>
            <table><thead><tr><th>Email</th><th>Name</th><th>Telegram Chat</th><th>Registered</th></tr></thead>
            <tbody>{pending_rows or '<tr><td colspan=4 style="color:#555;text-align:center;padding:32px">None pending</td></tr>'}</tbody></table>
            <p style="color:#444;font-size:12px">Refreshed: {time.strftime("%Y-%m-%d %H:%M UTC")}</p>
            </body></html>"""
            self.send_html(200, html)
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        parsed = urlparse(self.path)

        if parsed.path == "/webhook":
            sig = self.headers.get("Poynt-Webhook-Signature", "")
            if not valid_sig(body, sig):
                self.send_json(401, {"error": "invalid signature"}); return
            self.send_json(200, {"ok": True})
            try:
                event = json.loads(body)
                threading.Thread(target=handle_payment_event, args=(event,), daemon=True).start()
            except Exception as e:
                print(f"[webhook] {e}")

        elif parsed.path == "/register-fan":
            try:
                data    = json.loads(body)
                email   = data.get("email", "").lower().strip()
                chat_id = data.get("chat_id")
                name    = data.get("name", "babe")
                biz     = data.get("biz_conn_id", "")
                if not email or not chat_id:
                    self.send_json(400, {"error": "email and chat_id required"}); return
                pending = load_json(PENDING_FILE, {})
                pending[email] = {"chat_id": chat_id, "name": name, "biz_conn_id": biz,
                                  "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                save_json(PENDING_FILE, pending)
                print(f"[register] {name} ({email}) -> chat {chat_id}")
                self.send_json(200, {"ok": True, "registered": email})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == "/check-payment":
            # Bot calls this when fan claims they paid (no email required)
            # Looks for a single unmatched recent payment and delivers if found
            try:
                data     = json.loads(body)
                chat_id  = data.get("chat_id")
                fan_name = data.get("name", "babe")
                biz_conn = data.get("biz_conn_id", "")
                amt_hint = data.get("amount_cents")  # optional — if fan mentioned amount
                if not chat_id:
                    self.send_json(400, {"error": "chat_id required"}); return

                match = find_unmatched_payment(hours=2, amount_cents=amt_hint)
                if match:
                    msg = CONTENT_MESSAGE.replace("{name}", fan_name)
                    ok  = send_telegram(chat_id, msg, biz_conn)
                    if ok:
                        mark_delivered(match["resource_id"], chat_id, fan_name)
                        notify_owner(match.get("name","?"), match.get("amount_cents",0),
                                     match.get("email","?"), True, chat_id)
                        self.send_json(200, {"ok": True, "matched": True,
                                             "amount": match.get("amount_usd"),
                                             "payer": match.get("name")})
                    else:
                        self.send_json(200, {"ok": False, "matched": True, "error": "telegram send failed"})
                else:
                    # Multiple or no matches — ask for email
                    self.send_json(200, {"ok": True, "matched": False})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"[startup] Bella webhook v2 on port {PORT}")
    print(f"[startup] Owner notifications → chat {OWNER_CHAT_ID}")
    print(f"[startup] Dashboard → /dashboard?token={ADMIN_TOKEN}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
