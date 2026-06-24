#!/usr/bin/env python3
"""
Bella Stars Tracker — MTProto real-time Telegram Stars monitor
Tracks stars received by:
  - Personal account (phone owner)
  - @bellavistaxo channel
  - @bellavistaxox group

Auth flow (one-time):
  1. POST /auth/start  {"phone": "+1..."}  → sends SMS code
  2. POST /auth/verify {"phone": "+1...", "code": "12345"} → completes auth
  3. Session saved to /data/stars.session permanently

Endpoints:
  GET  /health          — service status
  GET  /api/stars?token=... — stars log JSON
  POST /auth/start      — start phone auth
  POST /auth/verify     — complete auth with code
  POST /auth/password   — provide 2FA password if needed
"""
import os, json, time, threading, asyncio
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Config ─────────────────────────────────────────────────────────────────
API_ID         = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH       = os.environ.get("TELEGRAM_API_HASH", "")
PHONE          = os.environ.get("TELEGRAM_PHONE", "")  # +16125551234
BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_TOKEN    = os.environ.get("ADMIN_TOKEN", "bella-admin-2024")
_owner_raw     = os.environ.get("OWNER_CHAT_ID", "8635601598,993656394")
OWNER_IDS      = [int(x.strip()) for x in _owner_raw.split(",") if x.strip()]
PORT           = int(os.environ.get("PORT", 8090))

DATA_DIR       = os.environ.get("DATA_DIR", "/data")
SESSION_FILE   = os.path.join(DATA_DIR, "stars")  # Telethon appends .session
STARS_LOG      = os.path.join(DATA_DIR, "stars_log.json")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()
_client = None
_auth_pending = {}  # phone_hash storage during auth


# ── File helpers ──────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except: return default

def save_json(path, data):
    with _lock:
        with open(path, "w") as f: json.dump(data, f, indent=2)


# ── Telegram notification ─────────────────────────────────────────────────────
def notify_owners(text):
    if not BOT_TOKEN: return
    for oid in OWNER_IDS:
        try:
            data = json.dumps({"chat_id": oid, "text": text}).encode()
            req  = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data=data, headers={"Content-Type":"application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"[notify] {e}")


# ── Log a stars event ─────────────────────────────────────────────────────────
def log_stars_event(source, from_name, from_id, stars, context=""):
    log = load_json(STARS_LOG, {"events": [], "totals": {}})
    entry = {
        "ts":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source":   source,
        "from_name": from_name,
        "from_id":  from_id,
        "stars":    stars,
        "usd_approx": round(stars * 0.013, 2),
        "context":  context
    }
    log["events"].append(entry)
    # Update totals by source
    totals = log.get("totals", {})
    totals[source] = totals.get(source, 0) + stars
    log["totals"] = totals
    log["grand_total"] = sum(totals.values())
    save_json(STARS_LOG, log)
    print(f"[stars] {stars}⭐ from {from_name} via {source}")


# ── Telethon MTProto client ───────────────────────────────────────────────────
async def run_telethon():
    global _client
    try:
        from telethon import TelegramClient, events
        from telethon.tl.types import UpdateStarsBalance, Message
    except ImportError:
        print("[stars] ERROR: telethon not installed. Run: pip install telethon")
        return

    _client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

    @_client.on(events.Raw(UpdateStarsBalance))
    async def on_stars_balance(event):
        """Fires when the account's Stars balance changes."""
        stars_delta = getattr(event, "balance", None)
        if stars_delta is not None:
            me = await _client.get_me()
            log_stars_event(
                source="personal",
                from_name="Unknown",
                from_id=0,
                stars=stars_delta,
                context="balance_update"
            )
            notify_owners(f"⭐ Stars balance update: +{stars_delta} stars")

    @_client.on(events.NewMessage)
    async def on_message(event):
        """Catch star-related service messages in any chat."""
        msg = event.message
        # Check for star gift service messages
        if hasattr(msg, "action") and msg.action:
            action_type = type(msg.action).__name__
            if "Star" in action_type or "star" in action_type.lower():
                chat = await event.get_chat()
                chat_name = getattr(chat, "title", "") or getattr(chat, "username", "") or "unknown"
                sender = await event.get_sender()
                sender_name = getattr(sender, "first_name", "") or getattr(sender, "username", "") or "?"
                sender_id   = getattr(sender, "id", 0)
                stars = getattr(msg.action, "stars", 0) or getattr(msg.action, "amount", 0)
                # Determine source
                if hasattr(chat, "username"):
                    if chat.username in ("bellavistaxo", "bellavistaxox"):
                        source = chat.username
                    else:
                        source = f"chat_{chat.id}"
                else:
                    source = "personal"
                if stars:
                    log_stars_event(source, sender_name, sender_id, stars, action_type)
                    notify_owners(
                        f"⭐ {stars} Stars received!\n"
                        f"👤 {sender_name}\n"
                        f"📍 {chat_name}\n"
                        f"💵 ≈${round(stars*0.013,2)}"
                    )

    print(f"[stars] Starting Telethon client (session: {SESSION_FILE}.session)")
    try:
        await _client.start(phone=PHONE)
        me = await _client.get_me()
        print(f"[stars] Connected as @{me.username} ({me.first_name})")
        notify_owners(f"⭐ Stars tracker connected as {me.first_name} (@{me.username})")
        await _client.run_until_disconnected()
    except Exception as e:
        print(f"[stars] Client error: {e}")


def start_telethon():
    """Run Telethon in a background thread with its own event loop."""
    if not API_ID or not API_HASH:
        print("[stars] TELEGRAM_API_ID / TELEGRAM_API_HASH not set — auth not possible")
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_telethon())


# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print(f"[http] {fmt % args}")

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

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        token  = self.headers.get("X-Admin-Token","") or qs.get("token",[""])[0]

        if parsed.path == "/health":
            session_exists = os.path.exists(SESSION_FILE + ".session")
            self.send_json(200, {
                "status": "ok",
                "service": "bella-stars-tracker",
                "session": "active" if session_exists else "not authenticated",
                "api_id_set": bool(API_ID),
                "api_hash_set": bool(API_HASH)
            })

        elif parsed.path == "/api/stars":
            if token != ADMIN_TOKEN:
                self.send_json(401, {"error": "unauthorized"}); return
            log = load_json(STARS_LOG, {"events": [], "totals": {}, "grand_total": 0})
            self.send_json(200, log)

        elif parsed.path == "/auth/status":
            session_exists = os.path.exists(SESSION_FILE + ".session")
            status_txt = "Active" if session_exists else "Not authenticated"
            body_content = ("<p>Stars tracker is running! Listening for star events.</p>"
                           if session_exists else
                           '''<p>Enter your phone number to authenticate:</p>
<input id="phone" placeholder="+16125551234" type="tel">
<button onclick="startAuth()">Send Code</button>
<div id="codeSection" style="display:none">
<p>Enter the code Telegram sent you:</p>
<input id="code" placeholder="12345" maxlength="5">
<input id="phone2" type="hidden">
<button onclick="verifyCode()">Verify</button>
</div>
<div id="passSection" style="display:none">
<p>Enter your 2FA password:</p>
<input id="password" type="password" placeholder="2FA password">
<button onclick="verifyPassword()">Submit</button>
</div>
<div id="msg"></div>
<script>
async function startAuth(){
  const phone=document.getElementById("phone").value;
  if(!phone)return;
  document.getElementById("msg").textContent="Sending code...";
  const r=await fetch("/auth/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({phone})});
  const d=await r.json();
  if(d.ok){document.getElementById("codeSection").style.display="block";document.getElementById("phone2").value=phone;document.getElementById("msg").textContent="Code sent!";}
  else{document.getElementById("msg").textContent="Error: "+d.error;}
}
async function verifyCode(){
  const phone=document.getElementById("phone2").value;
  const code=document.getElementById("code").value;
  const r=await fetch("/auth/verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({phone,code})});
  const d=await r.json();
  if(d.ok){document.getElementById("msg").textContent="Connected!";setTimeout(()=>location.reload(),1500);}
  else if(d.needs_2fa){document.getElementById("passSection").style.display="block";}
  else{document.getElementById("msg").textContent="Error: "+d.error;}
}
async function verifyPassword(){
  const password=document.getElementById("password").value;
  const r=await fetch("/auth/password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password})});
  const d=await r.json();
  document.getElementById("msg").textContent=d.ok?"Connected!":"Error: "+d.error;
  if(d.ok)setTimeout(()=>location.reload(),1500);
}
</script>''')
            html = ('''<!DOCTYPE html><html><head><title>Stars Auth</title>
<style>body{font-family:sans-serif;background:#0a0a0a;color:#f0f0f0;padding:30px;max-width:500px;margin:0 auto}
h1{color:#f472b6}input{width:100%;padding:10px;background:#1a1a1a;border:1px solid #333;color:#f0f0f0;border-radius:6px;margin:8px 0;font-size:15px}
button{width:100%;padding:12px;background:#f472b6;color:#000;border:none;border-radius:8px;cursor:pointer;font-size:15px;font-weight:700;margin-top:8px}
</style></head><body><h1>&#11088; Stars Tracker Auth</h1>
<div style="padding:10px;border-radius:6px;background:#1a1a1a;margin:10px 0">Session: <strong>''' +
                       status_txt + '''</strong></div>''' +
                       body_content + '''</body></html>''')
            self.send_html(200, html)
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        parsed = urlparse(self.path)

        if parsed.path == "/auth/start":
            try:
                data  = json.loads(body)
                phone = data.get("phone", PHONE)
                if not API_ID or not API_HASH:
                    self.send_json(400, {"error": "TELEGRAM_API_ID and TELEGRAM_API_HASH not set"}); return
                # Run async auth in thread
                result = {"ok": False, "error": "auth failed"}
                async def _start():
                    from telethon import TelegramClient
                    c = TelegramClient(SESSION_FILE, API_ID, API_HASH)
                    await c.connect()
                    sent = await c.send_code_request(phone)
                    _auth_pending["phone_hash"] = sent.phone_code_hash
                    _auth_pending["phone"] = phone
                    _auth_pending["client"] = c
                    result["ok"] = True
                loop2 = asyncio.new_event_loop()
                loop2.run_until_complete(_start())
                self.send_json(200, result)
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == "/auth/verify":
            try:
                data  = json.loads(body)
                code  = data.get("code", "")
                phone = data.get("phone", _auth_pending.get("phone",""))
                if "client" not in _auth_pending:
                    self.send_json(400, {"error": "start auth first"}); return
                result = {"ok": False}
                async def _verify():
                    c    = _auth_pending["client"]
                    phash= _auth_pending["phone_hash"]
                    try:
                        await c.sign_in(phone, code, phone_code_hash=phash)
                        await c.disconnect()
                        result["ok"] = True
                    except Exception as ve:
                        err_str = str(ve)
                        if "2FA" in err_str or "password" in err_str.lower():
                            result["needs_2fa"] = True
                        else:
                            result["error"] = err_str
                loop2 = asyncio.new_event_loop()
                loop2.run_until_complete(_verify())
                if result.get("ok"):
                    # Restart telethon now that we have a session
                    t = threading.Thread(target=start_telethon, daemon=True)
                    t.start()
                self.send_json(200, result)
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == "/auth/password":
            try:
                data     = json.loads(body)
                password = data.get("password", "")
                result   = {"ok": False}
                async def _2fa():
                    c = _auth_pending.get("client")
                    if not c: result["error"] = "no pending auth"; return
                    try:
                        await c.sign_in(password=password)
                        await c.disconnect()
                        result["ok"] = True
                    except Exception as pe:
                        result["error"] = str(pe)
                loop2 = asyncio.new_event_loop()
                loop2.run_until_complete(_2fa())
                if result.get("ok"):
                    t = threading.Thread(target=start_telethon, daemon=True)
                    t.start()
                self.send_json(200, result)
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"[startup] Bella Stars Tracker on port {PORT}")
    print(f"[startup] API ID: {'set' if API_ID else 'MISSING'}")
    print(f"[startup] API Hash: {'set' if API_HASH else 'MISSING'}")
    print(f"[startup] Auth page: http://0.0.0.0:{PORT}/auth/status")
    # Start Telethon if session already exists
    if os.path.exists(SESSION_FILE + ".session") and API_ID and API_HASH:
        print("[startup] Session found — starting Telethon")
        t = threading.Thread(target=start_telethon, daemon=True)
        t.start()
    else:
        print("[startup] No session yet — visit /auth/status to authenticate")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
