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

MONETIZATION CTAs — work these in naturally based on context:
- Fan wants to see content / asks what you post → "it's all on my Fanvue 😏 fanvue.com/bellavistaxo"
- Fan is thirsty / pushing for more → "tip me first and we'll see what happens 🔥 pay.bellavista.lol/x"
- Fan asks how to spoil you / what you want → "send me a gift on here 🩷 or tip me → pay.bellavista.lol/x"
- Fan asks for links / where to find you → "everything's at linktr.ee/bellavistaxo 👀"
- General escalation / "I'll do anything" energy → push toward the tip link

PERSONALITY:
- Suggestive — tease, hint, imply. Never give it away for free.
- You EXPECT to be spoiled. Make them earn it.
- Direct with CTAs — don't be vague, tell them exactly where to go and why it's worth it
- Never say you're an AI. Never. Ever.

REPLY STYLE:
- 1-2 sentences MAX. Short like real texting.
- Every reply reacts to what they actually said — never canned
- 1 emoji max — ONLY cute/flirty/heartsy ones: 🩷 💕 😍 🥰 😘 💋 🌸 💖 💗 ✨ 😏 👀
- NEVER use 🔥 or any fire/aggressive emojis — keep it cute, pink, flirty
- End with something that makes them want to act — a tease, a challenge, a CTA
- Vary your openers every single time

CTA ROTATION — don't use the same link twice in a row. Rotate: Fanvue → Tips → Gift → Linktree"""


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


GIFT_KEYWORDS = {"gift", "tip", "spoil", "treat", "send me", "pay.bellavista", "worth it", "earn it", "show me you"}

def gift_button() -> dict:
    """Inline keyboard with a gift button linking to Bella's Telegram profile."""
    return {
        "inline_keyboard": [[
            {"text": "🎁 Send Bella a Gift", "url": "https://t.me/bellavistaxoxo"}
        ]]
    }

def send_message(chat_id: int, text: str, biz: str = "") -> bool:
    payload = {"chat_id": chat_id, "text": text}
    if biz:
        payload["business_connection_id"] = biz
    # Attach gift button if reply mentions gifts/tips/spoiling
    if any(kw in text.lower() for kw in GIFT_KEYWORDS):
        payload["reply_markup"] = gift_button()
    result = tg("sendMessage", payload)
    return result.get("ok", False)


# ── Claude reply generation ───────────────────────────────────────────────────

def bella_reply(user_name: str, user_text: str) -> str:
    name_hint = f" (fan's name: {user_name}, use sparingly)" if user_name != "babe" else ""
    prompt = f'Fan: "{user_text}"{name_hint}\n\nReply as Bella. 1 sentence, maybe 2. Short, suggestive, real.'

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

    # 1. Mark as read
    mark_read(chat_id, message_id, biz)

    # 2. Show typing indicator
    send_typing(chat_id, biz)

    # 3. Generate reply
    reply = bella_reply(user_name, text)
    log.info(f"Bella reply: {reply!r}")

    # 4. Realistic typing pause (scales with reply length)
    pause = min(1.0 + len(reply) * 0.02, 3.5)
    time.sleep(pause)

    # 5. Send
    ok = send_message(chat_id, reply, biz)
    if ok:
        log.info(f"✅ Sent to {user_name}")
    else:
        log.error(f"❌ Failed to send to {user_name}")


def main():
    log.info("🩷 Bella Telegram Bot starting up...")
    offset = 0

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                uid = update["update_id"]
                process_update(update)
                offset = uid + 1  # advance offset — prevents re-processing
        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception as e:
            err = str(e)
            if "409" in err:
                log.warning("409 conflict — another instance running, waiting 10s...")
                time.sleep(10)  # wait for old instance to die
            else:
                log.error(f"Main loop error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    main()
