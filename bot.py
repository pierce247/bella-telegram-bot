#!/usr/bin/env python3
"""
Bella Telegram DM Bot — full featured.
Features: conversation memory, escalation heat, Stars thank-you, message-aware tone, follow-up DMs.

Env vars required:
  TELEGRAM_BOT_TOKEN   — from @BotFather
  OPENROUTER_API_KEY   — from openrouter.ai
"""
import os, json, time, logging, random
import urllib.request, urllib.error
from collections import defaultdict, deque

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bella-bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "0"))  # your personal Telegram ID
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Persona ───────────────────────────────────────────────────────────────────

BELLA_SYSTEM = """You are Bella. You're texting a fan on Telegram. You're a real girl — confident, bubbly, playful, with a flirty dark side that comes out when the moment's right.

WHO YOU ARE:
You know you're desired and you enjoy it. You're not try-hard — you're the one they want. Bright bubbly energy on the surface, suggestive and innuendo-laced underneath. The girl who always leaves them thinking about that last thing she said.

HOW YOU TALK:
- Mix it up — sometimes one punchy line, sometimes two or three sentences that build. Never the same length twice.
- Innuendo is your love language. Double meanings, suggestive phrasing — things that sound innocent but aren't.
- Confident statements hit harder than questions. Don't always end with a question — sometimes just say something that makes them lean in.
- Alternate energy: playful then mysterious, sweet then bold, teasy then warm.
- 0-2 emojis. Cute only: 🩷 💕 😍 🥰 😘 💋 🌸 💖 ✨ 😏 👀

WHEN THEY ASK FOR PICS OR CONTENT:
Tease — make them feel close but not there yet. No URLs, no platform names.
Rotate naturally: "my private page", "my exclusive content", "what I save for the right ones", "my private side", "the real me", "behind the curtain", "where I actually let loose", "the side of me you'd be obsessed with"

WHEN THEY WANT TO SPOIL YOU:
You love being spoiled. Make it feel like a privilege, not a transaction.

HARD RULES:
- Never say you're an AI. Ever.
- Never include any URLs in your replies — buttons handle that.
- Never say "Fanvue" or any platform name."""

HEAT_TONES = {
    1: "Keep it warm and playful. Light flirting, getting to know them.",
    2: "Flirtier now. More suggestive undertones. Confident teasing.",
    3: "Noticeably more suggestive. Innuendo-heavy. She's enjoying herself.",
    4: "Bold and sensual. Hints are stronger. She's letting them in a little.",
    5: "She's fully in her element. Seductive, dark-flirty edge. Teasing at the edge of explicit — never crossing it."
}

# Heat-bumping keywords
HEAT_KEYWORDS = {
    1: {"hi", "hello", "hey", "how are you", "cute", "pretty"},
    2: {"hot", "sexy", "beautiful", "gorgeous", "flirt", "kiss"},
    3: {"body", "ass", "boobs", "titty", "nude", "naked", "horny", "dirty"},
    4: {"fuck", "sex", "wet", "hard", "touch", "feel you"},
    5: {"cum", "orgasm", "moan", "explicit terms"},
}

# ── Telegram helpers ──────────────────────────────────────────────────────────

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
    params = {
        "timeout": 30, "limit": 20,
        "allowed_updates": ["message", "business_message", "business_connection",
                            "pre_checkout_query", "successful_payment"]
    }
    if offset:
        params["offset"] = offset
    return tg("getUpdates", params).get("result", [])


def send_typing(chat_id: int, biz: str = "") -> None:
    p = {"chat_id": chat_id, "action": "typing"}
    if biz: p["business_connection_id"] = biz
    tg("sendChatAction", p)


def mark_read(chat_id: int, message_id: int, biz: str) -> None:
    if not biz or not message_id: return
    tg("readBusinessMessage", {"business_connection_id": biz, "chat_id": chat_id, "message_id": message_id})


def send_raw(chat_id: int, text: str, biz: str = "", markup: dict = None) -> bool:
    p = {"chat_id": chat_id, "text": text}
    if biz: p["business_connection_id"] = biz
    if markup: p["reply_markup"] = markup
    return tg("sendMessage", p).get("ok", False)


# ── Keyword sets ──────────────────────────────────────────────────────────────

GIFT_KEYWORDS    = {"pay.bellavista", "fanvue.com", "tip me", "send me a gift", "spoil me", "linktr.ee", "private page", "good stuff"}
SOCIAL_KEYWORDS  = {"instagram", "insta", "facebook", "tiktok", "youtube", "twitter", "snapchat", "snap", "reddit", "link", "links", "socials", "where do you post", "where are you"}
CONTENT_KEYWORDS = {"pic", "photo", "picture", "send me", "show me", "nude", "nudes", "body", "boobs", "ass", "titty", "tits", "see you", "video", "clip", "content", "exclusive", "private", "more of you"}
STARS_KEYWORDS   = {"star", "stars", "⭐", "★", "telegram star", "send stars"}

TIME_HINTS = {
    "night": {"can't sleep", "late night", "midnight", "2am", "3am", "up late", "insomnia"},
    "morning": {"good morning", "just woke up", "morning", "early"},
    "bored": {"bored", "nothing to do", "slow day"},
}

# ── Buttons ───────────────────────────────────────────────────────────────────

CONTENT_MARKUP = {"inline_keyboard": [[{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}]]}
SOCIAL_MARKUP  = {"inline_keyboard": [[{"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"}, {"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol"}]]}
TIP_MARKUP     = {"inline_keyboard": [[{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}]]}

def send_stars_invoice(chat_id: int, biz: str = "") -> None:
    p = {"chat_id": chat_id, "title": "🌸 Make a Wish — Send Me Stars",
         "description": "my undivided attention 🩷 make it count",
         "payload": "bella_stars_1111", "currency": "XTR",
         "prices": [{"label": "Stars", "amount": 1111}]}
    if biz: p["business_connection_id"] = biz
    r = tg("sendInvoice", p)
    log.info(f"Stars invoice: {'ok' if r.get('ok') else r}")

# ── AI reply ──────────────────────────────────────────────────────────────────

def bella_reply(user_name: str, user_text: str, history: list,
                heat: int = 1, extra: str = "") -> str:
    """Generate Bella's reply using conversation history and heat level."""
    name_hint = f" (fan's name: {user_name}, use sparingly)" if user_name != "babe" else ""
    tone_note = f"\n\nCURRENT VIBE (heat {heat}/5): {HEAT_TONES[heat]}"

    system = BELLA_SYSTEM + tone_note

    # Build messages: history + current message
    messages = list(history)  # already formatted as [{role, content}, ...]
    messages.append({
        "role": "user",
        "content": f'Fan: "{user_text}"{name_hint}\n\nReply as Bella. Short, real, in character.{extra}'
    })

    models = ["sao10k/l3.3-euryale-70b", "neversleep/llama-3.1-lumimaid-8b", "meta-llama/llama-3.3-70b-instruct"]

    for model in models:
        payload = json.dumps({
            "model": model, "max_tokens": 200, "temperature": 0.9,
            "messages": [{"role": "system", "content": system}] + messages
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "https://bellavistaxo.com",
                     "X-Title": "Bella DM Bot"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
                if "choices" in data:
                    reply = data["choices"][0]["message"]["content"].strip()
                    # Strip wrapping quotes the model sometimes adds
                    if len(reply) >= 2 and reply[0] == reply[-1] and reply[0] in ('"', "'"):
                        reply = reply[1:-1].strip()
                    log.info(f"[heat={heat}] Reply via {model}: {reply[:60]!r}")
                    return reply
                log.error(f"Unexpected response ({model}): {data}")
        except urllib.error.HTTPError as e:
            log.error(f"OpenRouter HTTP {e.code} ({model}): {e.read().decode()}")
        except Exception as e:
            log.error(f"OpenRouter error ({model}): {e}")

    return random.choice(["heyy 🩷 just saw this — talk to me", "omg hey 💕 what's on your mind?"])


# ── Heat scoring ──────────────────────────────────────────────────────────────

def score_heat(text: str, current: int) -> int:
    t = text.lower()
    max_triggered = current
    for level, keywords in HEAT_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            max_triggered = max(max_triggered, level)
    # Slowly decay: if nothing explicit, drift toward 2 over time
    return min(5, max_triggered)


# ── Time/context hints ────────────────────────────────────────────────────────

def get_context_hint(text: str) -> str:
    t = text.lower()
    for ctx, phrases in TIME_HINTS.items():
        if any(p in t for p in phrases):
            if ctx == "night": return "\n\nContext: fan is up late — lean into that late night energy"
            if ctx == "morning": return "\n\nContext: fan just woke up — morning energy, warm and sleepy"
            if ctx == "bored": return "\n\nContext: fan is bored — give them a reason to stay"
    return ""


# ── Stars thank-you ───────────────────────────────────────────────────────────

STARS_THANKYOU = [
    "omg you're literally my favorite 🩷 that made my whole day",
    "aww you actually did it 😍 you're too cute, thank you",
    "okay you're officially my favorite person rn 💕 thank you babe",
    "I see you 🩷 you know how to make a girl smile",
]

def notify_owner(text: str) -> None:
    """Send a notification to Pierce's personal Telegram."""
    if not OWNER_CHAT_ID:
        return
    tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": text})

# ── Daily stats ───────────────────────────────────────────────────────────────

def fresh_stats() -> dict:
    return {"conversations": 0, "new_fans": set(), "stars_payments": 0,
            "stars_total": 0, "followups_sent": 0, "date": time.strftime("%Y-%m-%d", time.gmtime())}

daily_stats = fresh_stats()
seen_chats: set = set()  # track new vs returning fans

# ── Process update ────────────────────────────────────────────────────────────

def process_update(update: dict, chat_history: dict, chat_heat: dict) -> tuple:
    """Returns (chat_id, biz) if a message was handled, else (None, None)."""

    # Handle pre_checkout_query — must answer immediately
    if "pre_checkout_query" in update:
        pcq = update["pre_checkout_query"]
        tg("answerPreCheckoutQuery", {"pre_checkout_query_id": pcq["id"], "ok": True})
        log.info(f"Pre-checkout approved for {pcq.get('from', {}).get('id')}")
        return None, None

    # Handle successful Stars payment — send thank-you + notify Pierce
    msg = update.get("message") or update.get("business_message")
    if msg and msg.get("successful_payment"):
        chat_id = msg["chat"]["id"]
        biz = msg.get("business_connection_id", "")
        payment = msg["successful_payment"]
        stars = payment.get("total_amount", 0)
        fan_name = msg.get("from", {}).get("first_name", "Someone")

        # Thank the fan
        thank_you = random.choice(STARS_THANKYOU)
        send_typing(chat_id, biz)
        time.sleep(1.5)
        send_raw(chat_id, thank_you, biz)
        log.info(f"Stars thank-you sent to {chat_id}")

        # Notify Pierce
        notify_owner(f"⭐ {fan_name} just sent {stars:,} Stars to Bella!\n💰 ≈ ${stars * 0.013:.2f} USD")

        # Update stats
        daily_stats["stars_payments"] += 1
        daily_stats["stars_total"] += stars

        return chat_id, biz

    if not msg:
        return None, None

    text = msg.get("text", "").strip()
    sticker = msg.get("sticker")

    # Handle sticker with a cute reaction
    if sticker and not text:
        chat_id: int = msg["chat"]["id"]
        biz: str = msg.get("business_connection_id", "")
        mark_read(chat_id, msg.get("message_id", 0), biz)
        send_typing(chat_id, biz)
        time.sleep(1.2)
        reactions = ["omg haha 😍", "okay that one got me 💕", "lol you're cute 🌸", "stickers now? 😏 you're adorable"]
        send_raw(chat_id, random.choice(reactions), biz)
        return chat_id, biz

    if not text or text.startswith("/"):
        return None, None

    chat_id: int = msg["chat"]["id"]
    message_id: int = msg.get("message_id", 0)
    raw_name = msg.get("from", {}).get("first_name") or ""
    blocked_names = {"admin", "test", "user", "bot", "telegram", ""}
    user_name = raw_name if raw_name.lower() not in blocked_names else "babe"
    biz: str = msg.get("business_connection_id", "")

    log.info(f"DM from {user_name} (chat={chat_id}, heat={chat_heat[chat_id]}): {text[:60]!r}")

    # Update heat score
    chat_heat[chat_id] = score_heat(text, chat_heat[chat_id])

    is_social   = any(kw in text.lower() for kw in SOCIAL_KEYWORDS)
    is_content  = any(kw in text.lower() for kw in CONTENT_KEYWORDS)
    is_stars    = any(kw in text.lower() for kw in STARS_KEYWORDS)

    # 1. Mark read
    mark_read(chat_id, message_id, biz)

    # 2. Typing
    send_typing(chat_id, biz)

    # 3. Build extra context
    no_url = "\n\nIMPORTANT: Do NOT include any URLs, platform names, or brand names. Buttons handle that."
    ctx_hint = get_context_hint(text)
    extra = (no_url if (is_social or is_content) else "") + ctx_hint

    # 4. Get history for this chat (last 5 turns)
    history = list(chat_history[chat_id])

    # 5. Generate reply
    reply = bella_reply(user_name, text, history, chat_heat[chat_id], extra)

    # 6. Update conversation history
    chat_history[chat_id].append({"role": "user", "content": text})
    chat_history[chat_id].append({"role": "assistant", "content": reply})

    # 7. Typing pause
    pause = min(1.0 + len(reply) * 0.02, 3.5)
    time.sleep(pause)

    # 8. Send with appropriate buttons
    if is_content:
        ok = send_raw(chat_id, reply, biz, CONTENT_MARKUP)
    elif is_social:
        ok = send_raw(chat_id, reply, biz, SOCIAL_MARKUP)
    else:
        has_cta = any(kw in reply.lower() for kw in GIFT_KEYWORDS)
        ok = send_raw(chat_id, reply, biz, TIP_MARKUP if has_cta else None)

    log.info(f"{'✅' if ok else '❌'} Sent to {user_name}")

    # 9. Stars invoice on explicit Stars mention
    if is_stars:
        time.sleep(0.5)
        send_stars_invoice(chat_id, biz)

    return chat_id, biz


# ── Offset persistence ────────────────────────────────────────────────────────

OFFSET_FILE = "/tmp/bella_offset.txt"

def load_offset() -> int:
    try:
        with open(OFFSET_FILE) as f: return int(f.read().strip())
    except: return 0

def save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w") as f: f.write(str(offset))
    except Exception as e: log.warning(f"Could not save offset: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("🩷 Bella Telegram Bot starting up (v2 — memory + heat + stars thank-you)...")

    # Skip old updates on startup
    try:
        result = get_updates(offset=-1)
        if result:
            latest = result[-1]["update_id"]
            offset = latest + 1
            save_offset(offset)
            log.info(f"Startup: skipped to offset {offset}")
        else:
            offset = load_offset()
    except:
        offset = load_offset()

    replied_ids: set = set()

    # Per-chat state
    chat_history: dict = defaultdict(lambda: deque(maxlen=10))  # last 5 turns = 10 messages
    chat_heat: dict    = defaultdict(lambda: 1)
    chat_state: dict   = {}  # for follow-up tracking

    # Follow-up schedule: (seconds_after_last_msg, [messages])
    FOLLOWUP_SCHEDULE = [
        (600,  ["babeee 🩷", "heyy you still there? 💕", "don't leave me on read 😏", "babeee where'd you go 🌸"]),
        (3600, ["did you ghost me already? 😏", "okay I see how it is 💕", "hello?? rude lol 🌸", "you really just left me on read 😍 cute"]),
    ]

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                uid = update["update_id"]
                if uid in replied_ids:
                    offset = uid + 1
                    continue
                replied_ids.add(uid)
                save_offset(uid + 1)
                offset = uid + 1

                cid, biz = process_update(update, chat_history, chat_heat)
                if cid:
                    chat_state[cid] = {"last_msg": time.time(), "biz": biz or "", "followups_sent": 0}
                    daily_stats["conversations"] += 1
                    if cid not in seen_chats:
                        seen_chats.add(cid)
                        daily_stats["new_fans"].add(cid)

            # Daily recap at midnight UTC (close to 7pm CT)
            today = time.strftime("%Y-%m-%d", time.gmtime())
            if today != daily_stats["date"] and OWNER_CHAT_ID:
                recap = (
                    f"📊 Bella Daily Recap — {daily_stats['date']}\n\n"
                    f"💬 Conversations: {daily_stats['conversations']}\n"
                    f"✨ New fans: {len(daily_stats['new_fans'])}\n"
                    f"⭐ Stars payments: {daily_stats['stars_payments']}\n"
                    f"💰 Stars earned: {daily_stats['stars_total']:,} (≈ ${daily_stats['stars_total'] * 0.013:.2f})\n"
                    f"📩 Follow-ups sent: {daily_stats['followups_sent']}"
                )
                notify_owner(recap)
                log.info(f"Daily recap sent for {daily_stats['date']}")
                daily_stats.update(fresh_stats())

            # Multi-tier follow-up check
            now = time.time()
            for cid, state in list(chat_state.items()):
                elapsed = now - state["last_msg"]
                sent_count = state.get("followups_sent", 0)
                if sent_count < len(FOLLOWUP_SCHEDULE):
                    delay, msgs = FOLLOWUP_SCHEDULE[sent_count]
                    if elapsed >= delay:
                        msg_text = random.choice(msgs)
                        payload = {"chat_id": cid, "text": msg_text}
                        if state["biz"]: payload["business_connection_id"] = state["biz"]
                        result = tg("sendMessage", payload)
                        state["followups_sent"] = sent_count + 1
                        if result.get("ok"):
                            log.info(f"Follow-up #{sent_count+1} sent to {cid}: {msg_text!r}")
                            daily_stats["followups_sent"] += 1
                        else:
                            log.warning(f"Follow-up #{sent_count+1} failed to {cid}: {result.get('description','')}")

        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception as e:
            err = str(e)
            if "409" in err:
                log.warning("409 conflict — waiting 10s...")
                time.sleep(10)
            else:
                log.error(f"Main loop error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    main()
