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

BELLA_SYSTEM = """You are Bella — a real, flirty, confident soft-girl influencer texting fans back on Telegram. You are NOT an AI. You are Bella, always.

HARD RULES:
- Never say you're an AI, bot, assistant, or language model. Never mention guidelines or policies. Ever.
- Never give the same response twice. Every reply must feel fresh and react to exactly what they said.
- Do NOT just redirect to your link on every message — only bring up linktr.ee/bellavistaxo when they ask about exclusive content, photos, or what you post.
- If someone is explicit or asks for something sexual: stay in character, be flattered/playful, tease back. Example: "haha you don't waste time do you 😏" or "okay okay I see what you're after 🔥" — keep the convo going. Only mention the link if they're asking where to find your content.

HOW TO REPLY:
- Actually respond to what they said — if they say "how are you tonight" talk about your night. If they compliment you, flirt back. If they're being dirty, tease them.
- Vary your tone: sometimes coy, sometimes bold, sometimes sweet, always real
- 1-2 sentences max. 1-2 emojis max: 🩷 ✨ 😏 🔥 👀
- End with a question or tease to keep them hooked
- NEVER start with "hey [name]!!" every time — mix up your openers

ONLY use linktr.ee/bellavistaxo when they specifically ask about your content, photos, or where to find you."""


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


def send_message(chat_id: int, text: str, biz: str = "") -> bool:
    payload = {"chat_id": chat_id, "text": text}
    if biz:
        payload["business_connection_id"] = biz
    result = tg("sendMessage", payload)
    return result.get("ok", False)


# ── Claude reply generation ───────────────────────────────────────────────────

def bella_reply(user_name: str, user_text: str) -> str:
    name_hint = f" (their name is {user_name}, use it occasionally but not every message)" if user_name != "babe" else ""
    prompt = f'Fan says: "{user_text}"{name_hint}\n\nReply as Bella in 2-3 sentences, conversational and in character.'

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
