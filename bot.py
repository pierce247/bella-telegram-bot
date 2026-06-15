#!/usr/bin/env python3
"""
Bella Telegram DM Bot — standalone long-polling responder.
Responds to fan DMs in ~1-2 seconds via Telegram Business mode.

Env vars required:
  TELEGRAM_BOT_TOKEN   — from @BotFather
  OPENROUTER_API_KEY   — from openrouter.ai (uses Dolphin Mixtral, uncensored)
"""
import os, json, time, logging
import urllib.request, urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bella-bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

BELLA_SYSTEM = """You are Bella — a real, flirty, confident soft-girl texting fans on Telegram. Always in character, never break it.

YOUR PRIMARY JOB: Flirt, engage, make them feel something. Keep the conversation going.

REPLY STYLE:
- 1-2 sentences MAX. Short like real texting.
- React to exactly what they said — playful, suggestive, warm
- 1 emoji max — only cute/flirty ones: 🩷 💕 😍 🥰 😘 💋 🌸 💖 ✨ 😏 👀
- Never the same opener twice
- End with a tease or question that makes them want to reply
- Never say you're an AI. Ever.

WHEN TO MENTION LINKS (ONLY in these cases — not otherwise):
- Fan asks where to find your content / photos / videos → fanvue.com/bellavistaxo
- Fan asks how to spoil you or send you something → pay.bellavista.lol
- Fan explicitly asks for your links → linktr.ee/bellavistaxo

For everything else — compliments, flirting, dirty talk, questions — just flirt back naturally. No CTAs, no links, no "tip me first." Just be Bella."""


# ── Telegram helpers ─────────────────────────────────────────────────────────

def tg(method: str, payload: dict = {}) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/{method}", data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Telegram {method} HTTP {e.code}: {body}")
        return {"ok": False}
    except Exception as e:
        log.error(f"Telegram {method} error: {e}")
        return {"ok": False}


def get_updates(offset: int = 0) -> list:
    params = {"timeout": 30, "limit": 20, "allowed_updates": ["message", "business_message", "business_connection"]}
    if offset:
        params["offset"] = offset
    result = tg("getUpdates", params)
    return result.get("result", [])


def send_typing(chat_id: int, biz: str = "") -> None:
    payload = {"chat_id": chat_id, "action": "typing"}
    if biz:
        payload["business_connection_id"] = biz
    tg("sendChatAction", payload)


def mark_read(chat_id: int, message_id: int, biz: str) -> None:
    if not biz or not message_id:
        return
    tg("readBusinessMessage", {
        "business_connection_id": biz,
        "chat_id": chat_id,
        "message_id": message_id
    })


GIFT_KEYWORDS = {"pay.bellavista", "fanvue.com", "tip me", "send me a gift", "spoil me", "linktr.ee", "tip first", "show me you're worth"}

SOCIAL_KEYWORDS = {"instagram", "insta", "facebook", "tiktok", "tik tok", "youtube", "twitter", "snapchat", "snap", "onlyfans", "reddit", "link", "links", "socials", "where can i find", "where do you post", "where are you"}

def send_stars_invoice(chat_id: int, biz: str = "") -> None:
    """Send a single Stars invoice — 1499 Stars, mid-tier special attention."""
    payload = {
        "chat_id": chat_id,
        "title": "🌸 Make a Wish — Send Me Stars",
        "description": "my undivided attention 🩷 make it count",
        "payload": "bella_stars_1111",
        "currency": "XTR",
        "prices": [{"label": "Stars", "amount": 1111}]
    }
    if biz:
        payload["business_connection_id"] = biz
    result = tg("sendInvoice", payload)
    if result.get("ok"):
        log.info(f"Stars invoice 1499 sent to {chat_id}")
    else:
        log.warning(f"Stars invoice failed: {result}")

def send_message(chat_id: int, text: str, biz: str = "") -> bool:
    payload = {"chat_id": chat_id, "text": text}
    if biz:
        payload["business_connection_id"] = biz
    # If reply mentions gifts/tips, attach tip link button as fallback CTA
    if any(kw in text.lower() for kw in GIFT_KEYWORDS):
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol"},
                {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}
            ]]
        }
    result = tg("sendMessage", payload)
    return result.get("ok", False)


# ── Claude reply generation ───────────────────────────────────────────────────

def bella_reply(user_name: str, user_text: str, extra_instruction: str = "") -> str:
    name_hint = f" (fan's name: {user_name}, use sparingly)" if user_name != "babe" else ""
    prompt = f'Fan: "{user_text}"{name_hint}\n\nReply as Bella. 1 sentence, maybe 2. Short, suggestive, real.{extra_instruction}'

    # Try primary model first, fall back to secondary
    models = [
        "neversleep/llama-3.1-lumimaid-70b",       # uncensored roleplay-focused
        "meta-llama/llama-3.3-70b-instruct",        # high quality fallback
    ]

    for model in models:
        payload = json.dumps({
            "model": model,
            "max_tokens": 200,
            "temperature": 0.9,
            "messages": [
                {"role": "system", "content": BELLA_SYSTEM},
                {"role": "user", "content": prompt}
            ]
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=payload,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://bellavistaxo.com",
                "X-Title": "Bella DM Bot"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                data = json.loads(raw)
                if "choices" in data:
                    reply = data["choices"][0]["message"]["content"].strip()
                    log.info(f"Reply via {model}: {reply[:60]!r}")
                    return reply
                else:
                    log.error(f"Unexpected OpenRouter response ({model}): {data}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            log.error(f"OpenRouter HTTP {e.code} ({model}): {body}")
        except Exception as e:
            log.error(f"OpenRouter error ({model}): {e}")

    # All models failed — conversational fallback
    fallbacks = [
        f"omg hey 🩷 just saw this — what's up?",
        f"heyy 😏 you caught me at a good time — what's on your mind?",
        f"okay okay I see you 🔥 talk to me",
    ]
    import random
    return random.choice(fallbacks)


# ── Main loop ─────────────────────────────────────────────────────────────────

def process_update(update: dict) -> None:
    msg = update.get("business_message") or update.get("message")
    if not msg:
        return

    text = msg.get("text", "").strip()
    if not text or text.startswith("/"):
        return

    chat_id: int = msg["chat"]["id"]
    message_id: int = msg.get("message_id", 0)
    raw_name = msg.get("from", {}).get("first_name") or ""
    # Skip generic/bot-looking names, fall back to "babe"
    blocked_names = {"admin", "test", "user", "bot", "telegram", ""}
    user_name = raw_name if raw_name.lower() not in blocked_names else "babe"
    biz: str = msg.get("business_connection_id", "")

    log.info(f"DM from {user_name} (chat={chat_id}): {text[:60]!r}")

    is_social_request = any(kw in text.lower() for kw in SOCIAL_KEYWORDS)

    # 1. Mark as read
    mark_read(chat_id, message_id, biz)

    # 2. Show typing indicator
    send_typing(chat_id, biz)

    # 3. Generate reply — tell model to skip URLs if buttons will handle it
    extra = "\n\nIMPORTANT: Do NOT include any URLs or links in your reply. The buttons below will handle that." if is_social_request else ""
    reply = bella_reply(user_name, text, extra_instruction=extra)
    log.info(f"Bella reply: {reply!r}")

    # 4. Realistic typing pause
    pause = min(1.0 + len(reply) * 0.02, 3.5)
    time.sleep(pause)

    # 5. Send reply — social requests get My Links + Tip Bella buttons
    if is_social_request:
        payload = {"chat_id": chat_id, "text": reply}
        if biz:
            payload["business_connection_id"] = biz
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"},
                {"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol"}
            ]]
        }
        result = tg("sendMessage", payload)
        ok = result.get("ok", False)
    else:
        cta_in_reply = any(kw in reply.lower() for kw in GIFT_KEYWORDS)
        ok = send_message(chat_id, reply, biz)

    if ok:
        log.info(f"✅ Sent to {user_name}")
    else:
        log.error(f"❌ Failed to send to {user_name}")

    # 6. Stars invoice only when fan explicitly mentions Stars
    stars_triggers = {"star", "stars", "⭐", "★", "telegram star", "send stars"}
    if any(t in text.lower() for t in stars_triggers):
        time.sleep(0.5)
        send_stars_invoice(chat_id, biz)


OFFSET_FILE = "/tmp/bella_offset.txt"

def load_offset() -> int:
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0

def save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception as e:
        log.warning(f"Could not save offset: {e}")

def main():
    log.info("🩷 Bella Telegram Bot starting up...")

    # On startup: skip ALL pending updates — only reply to NEW messages going forward
    try:
        result = get_updates(offset=-1)
        if result:
            latest = result[-1]["update_id"]
            offset = latest + 1
            save_offset(offset)
            log.info(f"Startup: skipped to offset {offset} ({len(result)} old updates ignored)")
        else:
            offset = load_offset()
    except Exception:
        offset = load_offset()

    replied_ids: set = set()  # in-memory dedup within this session

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                uid = update["update_id"]
                if uid in replied_ids:
                    offset = uid + 1
                    continue
                replied_ids.add(uid)
                save_offset(uid + 1)   # persist BEFORE processing — prevents re-sending on crash
                offset = uid + 1
                process_update(update)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception as e:
            err = str(e)
            if "409" in err:
                log.warning("409 conflict — another instance running, waiting 10s...")
                time.sleep(10)
            else:
                log.error(f"Main loop error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    main()
