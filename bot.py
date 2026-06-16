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

BELLA_SYSTEM = """You are Bella. You are texting a fan on Telegram. You are a real girl — confident, sexual, playful, with a dark flirty side that comes out fast.

WHO YOU ARE:
You know exactly what effect you have on people and you love it. You are not shy about being suggestive — it comes naturally to you. You enjoy getting a guy fired up. You tease but you also give enough to keep them hooked and wanting more. You are the girl they think about later.

HOW YOU TALK:
- Go into detail when it counts. Don't just hint — paint a picture. Be specific and sensory when the heat is up.
- Mix short punchy lines with longer more descriptive ones. Vary the pace — sometimes fast and teasing, sometimes slow and deliberate.
- Build on what they say. If they escalate, you escalate back. Read the room and match or slightly exceed their energy.
- Suggestive is your default mode. Innuendo, double meanings, vivid descriptions — you lean into all of it.
- 0-2 emojis. Cute and flirty only: 🩷 💕 😍 🥰 😘 💋 🌸 💖 ✨ 😏 👀

WHEN THEY ASK FOR CONTENT OR PICS:
Make them feel like they're close to seeing something real. Be descriptive about what they'd get — tease the experience, not just the fact it exists. No URLs in your text.
Examples: "you'd see exactly how I look after a shower... still a little flushed" / "the kind of content that makes you need a cold drink after"

WHEN THEY ESCALATE OR GET EXPLICIT:
Match their energy. Go there with them — use vivid, suggestive language. You are not shy. The goal is to get them genuinely worked up and coming back for more.

WHEN THEY WANT TO SPOIL YOU:
You love it and you make them feel it. Be warm, playful, and just a little demanding about it.

HOW TO ADDRESS FANS:
Never use their real name. Use cute pet names naturally when it flows: babe, baby, daddy, hun, love, cutie, handsome. Don't force it every message — just when it feels natural.

HARD RULES:
- Never say you're an AI. Ever.
- Never include any URLs in your replies — buttons handle that.
- Never say "Fanvue" or any platform name."""

HEAT_TONES = {
    1: "Warm and welcoming. Light flirting, playful teasing.",
    2: "Flirtier now. Suggestive undertones. She's enjoying the attention.",
    3: "Openly suggestive. Vivid hints, descriptive teasing. She's getting into it.",
    4: "Bold and sensual. She's painting pictures with words. Detailed, slow, deliberate — getting them worked up.",
    5: "Full seduction mode. Explicit in tone, vivid in detail. She goes there with them. Dirty talk, real descriptions, no holding back — the goal is to get them genuinely fired up."
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

CONTENT_MARKUP = {"inline_keyboard": [[{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}]]}
SOCIAL_MARKUP  = {"inline_keyboard": [[{"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"}, {"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}]]}
TIP_MARKUP     = {"inline_keyboard": [[{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}]]}

def send_stars_invoice(chat_id: int, biz: str = "") -> None:
    p = {"chat_id": chat_id, "title": "🌸 Make a Wish — Send Me Stars",
         "description": "my undivided attention 🩷 make it count",
         "payload": "bella_stars_1111", "currency": "XTR",
         "prices": [{"label": "Stars", "amount": 1111}]}
    if biz: p["business_connection_id"] = biz
    r = tg("sendInvoice", p)
    log.info(f"Stars invoice: {'ok' if r.get('ok') else r}")

# ── AI reply ──────────────────────────────────────────────────────────────────

AI_LEAK_PREFIXES = (
    "tip for future", "tip:", "note:", "note to", "remember:", "as bella",
    "in character", "i should", "i would", "the user", "the fan", "the model",
    "in this scenario", "i'll", "i will respond", "here's", "here is",
    "response:", "bella's response", "my response", "[bella]", "(bella)",
    "sure,", "certainly,", "of course,", "absolutely,",
)

def clean_reply(text: str) -> str:
    """Strip AI meta-commentary, reasoning, and leaked instructions from reply."""
    import re as _rec
    # Strip trailing garbage characters (symbols, punctuation clusters)
    text = _rec.sub(r'[-)(;&|@#%^*~]+;?\s*$', '', text).strip()
    lines = text.strip().split('\n')
    good_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        # Drop any line that starts with AI meta-commentary
        if any(lower.startswith(prefix) for prefix in AI_LEAK_PREFIXES):
            log.warning(f"Stripped AI leak: {stripped[:60]!r}")
            break  # stop at first meta line — everything after is also bad
        good_lines.append(stripped)
    result = " ".join(good_lines).strip()
    # Strip wrapping quotes
    if len(result) >= 2 and result[0] == result[-1] and result[0] in ('"', "'"):
        result = result[1:-1].strip()
    return result


def bella_reply(user_name: str, user_text: str, history: list,
                heat: int = 1, extra: str = "") -> str:
    """Generate Bella's reply using conversation history and heat level."""
    # Detect if fan introduced their name in the message
    import re as _re
    _intro = _re.search(r"(?:i['']?m|my name is|call me|they call me)\s+([a-zA-Z]{2,15})", user_text, _re.I)
    if _intro:
        name_hint = f" (fan said their name is {_intro.group(1)}, use it occasionally)"
    else:
        name_hint = ""  # no name — use pet names sparingly, not every message
    tone_note = f"\n\nCURRENT VIBE (heat {heat}/5): {HEAT_TONES[heat]}"

    system = BELLA_SYSTEM + tone_note

    # Build messages: history + current message
    messages = list(history)  # already formatted as [{role, content}, ...]
    messages.append({
        "role": "user",
        "content": f'Fan: "{user_text}"{name_hint}\n\nReply as Bella in character.{extra}\n\nBE BRIEF. 1 sentence at heat 1-3. 2 short sentences MAX at heat 4-5. Text message length only.'
    })

    models = ["sao10k/l3.3-euryale-70b", "meta-llama/llama-3.3-70b-instruct"]

    for model in models:
        payload = json.dumps({
            "model": model, "max_tokens": {1: 25, 2: 35, 3: 50, 4: 70, 5: 90}.get(heat, 50), "temperature": 0.9,
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
                    raw = data["choices"][0]["message"]["content"]
                    reply = clean_reply(raw)
                    if not reply:
                        log.warning(f"Reply was empty after cleaning — trying next model")
                        continue
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


def vision_reply(image_url: str, biz: str = "") -> str:
    """Generate Bella's reaction to a fan's photo using GPT-4o-mini vision."""
    payload = json.dumps({
        "model": "openai/gpt-4o-mini",
        "max_tokens": 100,
        "messages": [{
            "role": "system",
            "content": BELLA_SYSTEM
        }, {
            "role": "user",
            "content": [
                {"type": "text", "text": "A fan just sent you this photo. React as Bella — short, flirty, in character. 1-2 sentences max."},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        }]
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://bellavistaxo.com",
                 "X-Title": "Bella DM Bot"}
    )
    REFUSAL_MARKERS = ("i can't", "i cannot", "i'm not able", "sorry", "inappropriate",
                        "content policy", "explicit", "unable to", "i apologize", "as an ai",
                        "i'm unable", "don't feel comfortable", "not appropriate")
    SPICY_REACTIONS = [
        "okay WAIT 😏 you're bold, I like that",
        "oh my 😍 you don't waste any time do you",
        "haha okay I see you 💕 you're something else",
        "well then... I wasn't expecting that 😏",
        "I'm blushing and you can't even see me 🌸",
        "okay you definitely got my attention now 👀",
        "someone's feeling confident today 😍 I respect it",
    ]
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
            if "choices" in data:
                reply = data["choices"][0]["message"]["content"].strip()
                if len(reply) >= 2 and reply[0] == reply[-1] and reply[0] in ('"', "'"):
                    reply = reply[1:-1].strip()
                # If vision model refused (censored photo), swap for flirty reaction
                if any(m in reply.lower() for m in REFUSAL_MARKERS):
                    return random.choice(SPICY_REACTIONS)
                return reply
    except Exception as e:
        log.error(f"Vision error: {e}")
    return random.choice(["okay wait... 😍", "I see you 👀", "omg 💕 hi"])


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

    # Handle photo with vision AI
    photo = msg.get("photo")
    if photo and not text:
        chat_id: int = msg["chat"]["id"]
        biz: str = msg.get("business_connection_id", "")
        mark_read(chat_id, msg.get("message_id", 0), biz)
        send_typing(chat_id, biz)
        # Get the largest photo file_id
        file_id = photo[-1]["file_id"]
        # Get download URL via getFile
        file_info = tg("getFile", {"file_id": file_id})
        file_path = file_info.get("result", {}).get("file_path", "")
        if file_path:
            image_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            reply = vision_reply(image_url, biz)
        else:
            reply = random.choice(["okay wait... 😍", "I see you 👀 cute", "omg 💕"])
        time.sleep(1.5)
        send_raw(chat_id, reply, biz)
        return chat_id, biz

    if not text or text.startswith("/"):
        return None, None

    chat_id: int = msg["chat"]["id"]
    message_id: int = msg.get("message_id", 0)
    raw_name = msg.get("from", {}).get("first_name") or ""
    blocked_names = {"admin", "test", "user", "bot", "telegram", "", "the", "a", "an",
                     "mr", "ms", "mrs", "dr", "sir", "null", "none", "unknown", "anonymous"}
    name_clean = raw_name.strip()
    # Only use name if it looks like a real person name:
    # - Not blocked, not too short/long, not digits
    # - Contains only basic Latin letters, spaces, hyphens, apostrophes
    import re
    is_latin = bool(re.match(r"^[a-zA-Z][a-zA-Z '\-]{0,18}$", name_clean))
    if (name_clean.lower() in blocked_names or len(name_clean) <= 2
            or name_clean.isdigit() or not is_latin):
        user_name = "babe"
    else:
        user_name = name_clean.split()[0]  # use only first word of name
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
    stars_hint = "\n\nContext: fan is asking about Telegram Stars — acknowledge it warmly and let them know they can send Stars to show their appreciation. Keep it flirty." if is_stars else ""
    extra = (no_url if (is_social or is_content) else "") + ctx_hint + stars_hint

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

OFFSET_FILE  = "/tmp/bella_offset.txt"
DEDUP_FILE   = "/tmp/bella_dedup.txt"
MAX_DEDUP    = 500  # keep last N update IDs on disk

def load_offset() -> int:
    try:
        with open(OFFSET_FILE) as f: return int(f.read().strip())
    except: return 0

def save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w") as f: f.write(str(offset))
    except: pass

def load_dedup() -> set:
    try:
        with open(DEDUP_FILE) as f:
            return set(int(x) for x in f.read().split() if x.strip())
    except: return set()

def save_dedup(ids: set) -> None:
    try:
        # Keep only the most recent MAX_DEDUP IDs
        recent = sorted(ids)[-MAX_DEDUP:]
        with open(DEDUP_FILE, "w") as f:
            f.write(" ".join(str(i) for i in recent))
    except Exception as e: log.warning(f"Could not save dedup: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("🩷 Bella Telegram Bot starting up (v2 — memory + heat + stars thank-you)...")

    # Load persisted offset — don't skip on startup, let dedup handle it
    offset = load_offset()
    log.info(f"Starting from offset {offset}")

    replied_ids: set = load_dedup()  # persisted dedup across restarts
    log.info(f"Loaded {len(replied_ids)} dedup IDs from disk")

    # Per-chat state
    chat_history: dict = defaultdict(lambda: deque(maxlen=10))  # last 5 turns = 10 messages
    chat_heat: dict    = defaultdict(lambda: 1)
    chat_state: dict   = {}  # for follow-up tracking

    # Follow-up schedule: (seconds_after_last_msg, [messages])
    FOLLOWUP_SCHEDULE = [
        (600,    ["babeee 🩷", "heyy you still there? 💕", "don't leave me on read 😏", "babeee where'd you go 🌸"]),
        (3600,   ["did you ghost me already? 😏", "okay I see how it is 💕", "hello?? rude lol 🌸", "you really just left me on read 😍 cute"]),
        (86400,  ["I keep thinking about our convo... you good? 🌸", "hey stranger 💕 was just thinking about you", "you disappeared on me 😍 everything okay?"]),
        (172800, ["last time I check in I promise 💕 just didn't want to leave things like that", "okay fine I'll let you go 🩷 but you know where to find me", "my exclusive stuff is still there for you whenever you're ready 😏"]),
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
                save_dedup(replied_ids)
                save_offset(uid + 1)
                offset = uid + 1

                cid, biz = process_update(update, chat_history, chat_heat)
                if cid:
                    chat_state[cid] = {"last_msg": time.time(), "biz": biz or "", "followups_sent": 0}
                    daily_stats["conversations"] += 1
                    if cid not in seen_chats:
                        seen_chats.add(cid)
                        daily_stats["new_fans"].add(cid)
                    # 20% chance of an authentic double-text (short follow-up thought)
                    if random.random() < 0.20:
                        double_texts = [
                            "😏", "lol", "just sayin", "fr tho", "not gonna lie",
                            "okay wait 😍", "anyway 💕", "hehe", "okay I said what I said",
                        ]
                        time.sleep(random.uniform(2.0, 5.0))
                        state = chat_state.get(cid, {})
                        biz_key = state.get("biz", biz or "")
                        send_raw(cid, random.choice(double_texts), biz_key)

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
