#!/usr/bin/env python3
"""
Bella Poynt Payment Webhook Listener
Receives Poynt payment events, logs every transaction,
matches payers to Telegram chat IDs, and auto-delivers content.

Endpoints:
  POST /webhook          — Poynt calls this on every payment event
  POST /register-fan     — Telegram bot registers email → chat_id before fan pays
  GET  /payments         — View all transactions log (protected by ADMIN_TOKEN)
  GET  /health           — Health check
"""
import json, os, time, hmac, hashlib, base64, threading
import urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Config from environment ────────────────────────────────────────────────
BOT_TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN", "")
POYNT_APP_ID_RAW = os.environ.get("POYNT_APP_ID", "")
POYNT_CLIENT_SECRET_RAW = os.environ.get("POYNT_CLIENT_SECRET", "")
WEBHOOK_SECRET   = os.environ.get("POYNT_WEBHOOK_SECRET", "")
ADMIN_TOKEN      = os.environ.get("ADMIN_TOKEN", "bella-admin")
CONTENT_MESSAGE  = os.environ.get("CONTENT_MESSAGE",
    "omg thank you so much!! \ud83e\ude77 here's your exclusive access \u2192 https://linktr.ee/bellavistaxo \n\nyou're one of my favs now \ud83d\ude0f\u2728")
BUSINESS_ID      = "8b2a6d7f-7a1f-4a96-9ea5-abc73755d69a"
PORT             = int(os.environ.get("PORT", 8080))

# ── File paths ─────────────────────────────────────────────────────────────
DATA_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PAYMENTS_LOG = os.path.join(DATA_DIR, "payments_log.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending_fans.json")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()


# ── File helpers ───────────────────────────────────────────────────────────
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


# ── Poynt auth ─────────────────────────────────────────────────────────────
def get_poynt_token():
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        from cryptography.hazmat.backends import default_backend
        import uuid as _uuid

        app_id = "urn:aid:" + POYNT_APP_ID_RAW.split("urn:aid:")[-1].strip()
        raw    = POYNT_CLIENT_SECRET_RAW
        clean  = raw.replace("-----BEGIN RSA PRIVATE KEY----- ", "").replace(" -----END RSA PRIVATE KEY-----", "").replace(" ", "")
        pem    = "-----BEGIN RSA PRIVATE KEY-----\n"
        for i in range(0, len(clean), 64):
            pem += clean[i:i+64] + "\n"
        pem += "-----END RSA PRIVATE KEY-----\n"
        key = serialization.load_pem_private_key(pem.encode(), password=None, backend=default_backend())

        def b64u(d):
            if isinstance(d, str): d = d.encode()
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        now     = int(time.time())
        header  = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claims  = {"iss": app_id, "sub": app_id, "aud": "https://services.poynt.net",
                   "iat": now, "exp": now + 300, "jti": str(_uuid.uuid4())}
        payload = b64u(json.dumps(claims))
        sig_in  = f"{header}.{payload}".encode()
        sig     = base64.urlsafe_b64encode(key.sign(sig_in, asym_padding.PKCS1v15(), hashes.SHA256())).rstrip(b"=").decode()
        jwt     = f"{header}.{payload}.{sig}"

        data = f"grantType=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt}".encode()
        req  = urllib.request.Request("https://services.poynt.net/token", data=data,
               headers={"Content-Type": "application/x-www-form-urlencoded",
                        "api-version": "1.2",
                        "Poynt-Request-Id": str(_uuid.uuid4())})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["accessToken"]
    except Exception as e:
        print(f"[poynt_auth] error: {e}")
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
        print(f"[poynt_get] {path} error: {e}")
        return None


# ── Telegram send ──────────────────────────────────────────────────────────
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
            result = json.loads(r.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"[telegram] send error: {e}")
        return False


# ── Payment processing ─────────────────────────────────────────────────────
def handle_payment_event(event):
    event_type  = event.get("eventType", "")
    resource_id = event.get("resourceId", "")
    links       = event.get("links", [])
    resource_url = links[0].get("href", "") if links else ""

    print(f"[payment] event={event_type} resource={resource_id}")

    txn = None
    if resource_url:
        path = resource_url.replace("https://services.poynt.net", "")
        txn = poynt_get(path)

    email  = ""
    name   = ""
    amount = 0
    status = ""

    if txn:
        if "fundingSource" in txn:
            card  = txn.get("fundingSource", {}).get("card", {})
            name  = card.get("cardHolderFullName", "")
            email = txn.get("receiptEmailAddress", "")
            amount = txn.get("amounts", {}).get("transactionAmount", 0)
            status = txn.get("status", "")
        elif "transactions" in txn:
            t = txn.get("transactions", [{}])[0]
            card   = t.get("fundingSource", {}).get("card", {})
            name   = card.get("cardHolderFullName", "")
            email  = t.get("receiptEmailAddress", "")
            amount = txn.get("amounts", {}).get("netTotal", 0)
            status = txn.get("statuses", {}).get("transactionStatusSummary", "")

    log = load_json(PAYMENTS_LOG, [])
    entry = {
        "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type":  event_type,
        "resource_id": resource_id,
        "name":        name,
        "email":       email.lower(),
        "amount_cents": amount,
        "amount_usd":  f"${amount/100:.2f}" if amount else "?",
        "status":      status,
        "chat_id":     None,
        "delivered":   False,
    }

    if status not in ("CAPTURED", "AUTHORIZED", "COMPLETED", "") or not email:
        log.append(entry)
        save_json(PAYMENTS_LOG, log)
        print(f"[payment] logged {status} — no delivery")
        return

    pending = load_json(PENDING_FILE, {})
    match   = pending.get(email.lower())

    if match:
        chat_id    = match.get("chat_id")
        biz_conn   = match.get("biz_conn_id", "")
        fan_name   = match.get("name", "babe")
        entry["chat_id"] = chat_id
        msg = CONTENT_MESSAGE.replace("{name}", fan_name)
        ok  = send_telegram(chat_id, msg, biz_conn)
        if ok:
            entry["delivered"] = True
            print(f"[payment] delivered to {fan_name} chat={chat_id}")
            del pending[email.lower()]
            save_json(PENDING_FILE, pending)
        else:
            print(f"[payment] Telegram send failed chat={chat_id}")
    else:
        print(f"[payment] no fan registered for {email} — logged only")

    log.append(entry)
    save_json(PAYMENTS_LOG, log)


# ── Signature validation ───────────────────────────────────────────────────
def valid_signature(body, sig_header):
    if not WEBHOOK_SECRET:
        return True
    mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode()
    return hmac.compare_digest(expected, sig_header)


# ── HTTP handler ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[http] {fmt % args}")

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json(200, {"status": "ok"})
        elif parsed.path == "/payments":
            token = self.headers.get("X-Admin-Token", "")
            qs    = parse_qs(parsed.query)
            token = token or qs.get("token", [""])[0]
            if token != ADMIN_TOKEN:
                self.send_json(401, {"error": "unauthorized"})
                return
            log = load_json(PAYMENTS_LOG, [])
            self.send_json(200, {"count": len(log), "payments": list(reversed(log))})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        parsed = urlparse(self.path)

        if parsed.path == "/webhook":
            sig = self.headers.get("Poynt-Webhook-Signature", "")
            if not valid_signature(body, sig):
                self.send_json(401, {"error": "invalid signature"})
                return
            self.send_json(200, {"ok": True})
            try:
                event = json.loads(body)
                t = threading.Thread(target=handle_payment_event, args=(event,), daemon=True)
                t.start()
            except Exception as e:
                print(f"[webhook] parse error: {e}")

        elif parsed.path == "/register-fan":
            try:
                data    = json.loads(body)
                email   = data.get("email", "").lower().strip()
                chat_id = data.get("chat_id")
                name    = data.get("name", "babe")
                biz     = data.get("biz_conn_id", "")
                if not email or not chat_id:
                    self.send_json(400, {"error": "email and chat_id required"})
                    return
                pending = load_json(PENDING_FILE, {})
                pending[email] = {"chat_id": chat_id, "name": name,
                                  "biz_conn_id": biz,
                                  "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                save_json(PENDING_FILE, pending)
                print(f"[register] {name} ({email}) -> chat {chat_id}")
                self.send_json(200, {"ok": True, "registered": email})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(404, {"error": "not found"})


# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[startup] Bella webhook listener on port {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
