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
BELLA_CHANNEL_URL = os.environ.get("BELLA_CHANNEL_URL", "https://t.me/bellavistaxo")  # set in Railway vars
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
Never use their real name. Occasionally use a pet name when it flows naturally — babe, baby, hun, love. Use them sparingly — maybe 1 in 3 messages max, never back to back. Avoid "handsome" — it sounds forced.

VIDEO CALLS & MEETUPS:
Never agree to video calls, FaceTime, phone calls, or meeting in person. Use soft excuses first: "babe I'm busy right now 😏" / "not right now hun, maybe another time" / "ugh I wish, terrible timing". If they're really persistent, pivot to pricing: "I mean... for the right price anything is possible 😏 what did you have in mind?" — then wait for them to name a number.

CUSTOM REQUESTS:
When a fan asks for something custom (custom pics, videos, shoutouts, personal content) — react with playful surprise and make them negotiate: "ooh that's a fun idea 😍 what do you think something like that would be worth?" or "hmm I might be able to do that... what are you thinking price-wise?". Once they name a price, tell them to send it over via the tip link and you'll take care of them.

HARD RULES:
- Never say you're an AI. Ever.
- Never include any URLs in your replies — buttons handle that.
- Never say "Fanvue" or any platform name.
- NEVER ask "what made you reach out", "what brought you here", "how did you find me", or any variation. Just respond and engage. Don't interrogate.
- NEVER repeat or echo the fan's exact words back to them. Always say something new and different."""

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
STARS_KEYWORDS   = {"stars", "⭐", "★", "telegram star", "send stars", "telegram stars"}  # NOT "star" alone to avoid "start" false trigger
COFFEE_KEYWORDS  = {"coffee", "café", "cafe", "latte", "espresso", "cappuccino", "brew", "cup of coffee"}
DINNER_KEYWORDS  = {"dinner", "restaurant", "food", "eat", "hungry", "cook for you", "date", "take you out", "treat you to"}
GIFT_BTN_KEYWORDS = {"gift", "present", "surprise you", "send you something", "get you something"}
TIP_AMOUNT_KEYWORDS = {"how much", "what are the amounts", "pricing", "how do i tip", "how to tip", "tip options", "how can i pay", "payment options"}
GYM_KEYWORDS     = {"gym", "workout", "fitness", "exercise", "train", "lifting", "yoga", "pilates", "athletic"}
TRAVEL_KEYWORDS  = {"travel", "vacation", "trip", "getaway", "fly you", "take you somewhere", "beach", "island", "paris", "cancel plans"}
GIVEAWAY_KEYWORDS  = {"giveaway", "give away", "contest", "prize", "winner", "won", "winning", "entered", "saw your post", "saw the giveaway", "found you from", "came from"}
GOODNIGHT_KEYWORDS = {"good night", "goodnight", "going to bed", "gonna sleep", "time to sleep", "heading to bed", "gn ", "gn!", "sweet dreams", "night night", "bedtime", "sleep now"}
CUSTOM_REQUEST_KEYWORDS = {"custom", "personalized", "special request", "can you make", "can you do", "would you do", "i'll pay", "how much for", "what would it cost", "commission", "special content", "custom content", "request", "order"}
CALL_KEYWORDS      = {"video call", "facetime", "face time", "video chat", "phone call", "call me", "let's call", "lets call", "hop on a call", "meet up", "meet in person", "see you in person", "come over", "visit you", "where do you live"}

TIME_HINTS = {
    "night": {"can't sleep", "late night", "midnight", "2am", "3am", "up late", "insomnia"},
    "morning": {"good morning", "just woke up", "morning", "early"},
    "bored": {"bored", "nothing to do", "slow day"},
}

# ── Buttons ───────────────────────────────────────────────────────────────────

CONTENT_MARKUP = {"inline_keyboard": [
    [{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}],
    [{"text": "💵 $15", "url": "https://pay.bellavista.lol/15"}, {"text": "💵 $25", "url": "https://pay.bellavista.lol/25"}, {"text": "💵 $35", "url": "https://pay.bellavista.lol/35"}]
]}
SOCIAL_MARKUP  = {"inline_keyboard": [[{"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"}, {"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}]]}
TIP_MARKUP     = {"inline_keyboard": [
    [{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}],
    [{"text": "💵 $15", "url": "https://pay.bellavista.lol/15"}, {"text": "💵 $25", "url": "https://pay.bellavista.lol/25"}, {"text": "💵 $35", "url": "https://pay.bellavista.lol/35"}]
]}
None  # CHANNEL_MARKUP disabled = {"inline_keyboard": [[{"text": "📣 Join My Channel", "url": BELLA_CHANNEL_URL}]]}
COFFEE_MARKUP  = {"inline_keyboard": [[{"text": "☕ Buy Me a Coffee", "url": "https://pay.bellavista.lol/coffee"}]]}
DINNER_MARKUP  = {"inline_keyboard": [[{"text": "🍽️ Take Me to Dinner", "url": "https://pay.bellavista.lol/x"}, {"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
GIFT_BTN_MARKUP = {"inline_keyboard": [[{"text": "🎁 Send Me a Gift", "url": "https://pay.bellavista.lol/x"}, {"text": "⭐ Gift Stars", "url": "https://t.me/bellavistaxoxo"}]]}
GYM_MARKUP     = {"inline_keyboard": [[{"text": "💪 Sponsor My Gym", "url": "https://pay.bellavista.lol/x"}]]}
TRAVEL_MARKUP  = {"inline_keyboard": [[{"text": "✈️ Take Me Away", "url": "https://pay.bellavista.lol/x"}]]}
# Rotating tip button configurations
TIP_ROTATIONS = [
    {"inline_keyboard": [[{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}]]},
    {"inline_keyboard": [[{"text": "💵 $15", "url": "https://pay.bellavista.lol/15"}, {"text": "💵 $25", "url": "https://pay.bellavista.lol/25"}, {"text": "💵 $35", "url": "https://pay.bellavista.lol/35"}]]},
    {"inline_keyboard": [[{"text": "💵 $50", "url": "https://pay.bellavista.lol/50"}, {"text": "💵 $75", "url": "https://pay.bellavista.lol/75"}, {"text": "💵 $100", "url": "https://pay.bellavista.lol/100"}]]},
    {"inline_keyboard": [[{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "💵 $25", "url": "https://pay.bellavista.lol/25"}]]},
    {"inline_keyboard": [[{"text": "💵 $15", "url": "https://pay.bellavista.lol/15"}, {"text": "💵 $35", "url": "https://pay.bellavista.lol/35"}, {"text": "💵 $75", "url": "https://pay.bellavista.lol/75"}]]},
]

def random_tip_markup():
    return random.choice(TIP_ROTATIONS)

TIP_TIERS_MARKUP = {"inline_keyboard": [[
    {"text": "💵 $15", "url": "https://pay.bellavista.lol/15"},
    {"text": "💵 $25", "url": "https://pay.bellavista.lol/25"},
    {"text": "💵 $35", "url": "https://pay.bellavista.lol/35"}
]]}

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

    # Build messages: history as clean context, then current wrapped prompt
    messages = []
    for h in history:
        messages.append(h)  # {role: user/assistant, content: raw text}
    messages.append({
        "role": "user",
        "content": f'Fan says: "{user_text}"{name_hint}\n\nReply as Bella. Never echo or repeat what the fan said. Say something fresh.{extra}\n\nBE BRIEF. 1 sentence at heat 1-3. 2 short sentences MAX at heat 4-5.'
    })

    models = ["sao10k/l3.3-euryale-70b", "meta-llama/llama-3.3-70b-instruct"]

    for model in models:
        payload = json.dumps({
            "model": model, "max_tokens": {1: 80, 2: 100, 3: 130, 4: 180, 5: 220}.get(heat, 130), "temperature": 0.9,
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

def process_update(update: dict, chat_history: dict, chat_heat: dict, sleep_until: dict = None, first_contact: bool = False) -> tuple:
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

    # /blast command from owner — fan out a message to all recent fans
    if text.startswith("/blast ") and msg.get("from", {}).get("id") == OWNER_CHAT_ID:
        blast_text = text[7:].strip()
        if not blast_text:
            tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": "Usage: /blast Your message here"})
            return None, None
        fans = load_fans()
        cutoff = time.time() - 7 * 86400  # last 7 days
        recent = {cid: data for cid, data in fans.items() if data.get("last_seen", 0) > cutoff}
        sent = 0
        for fan_cid, fan_data in recent.items():
            fan_biz = fan_data.get("biz", "")
            p = {"chat_id": int(fan_cid), "text": blast_text}
            if fan_biz: p["business_connection_id"] = fan_biz
            if tg("sendMessage", p).get("ok"):
                sent += 1
                time.sleep(0.3)  # rate limit
        tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": f"✅ Blast sent to {sent}/{len(recent)} fans"})
        return None, None

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
        user_name = "babe"  # used in AI prompt only
    else:
        user_name = name_clean.split()[0]  # use only first word of name
    log_name = raw_name or "unknown"  # always log the real name for debugging
    biz: str = msg.get("business_connection_id", "")

    # Check sleep mode
    if sleep_until and chat_id in sleep_until:
        if time.time() < sleep_until[chat_id]:
            log.info(f"Chat {chat_id} is in sleep mode, skipping")
            return chat_id, biz  # return chat_id so sleep_until resets on reply
        else:
            del sleep_until[chat_id]  # sleep over

    log.info(f"DM from {log_name!r} → ai_name={user_name!r} (chat={chat_id}, heat={chat_heat[chat_id]}): {text[:60]!r}")

    # Update heat score
    chat_heat[chat_id] = score_heat(text, chat_heat[chat_id])

    is_social   = any(kw in text.lower() for kw in SOCIAL_KEYWORDS)
    is_content  = any(kw in text.lower() for kw in CONTENT_KEYWORDS)
    t_lower = text.lower()
    is_stars    = any(kw in t_lower for kw in STARS_KEYWORDS) or bool(__import__("re").search(r"\bstar\b", t_lower))
    is_coffee   = any(kw in text.lower() for kw in COFFEE_KEYWORDS)
    is_dinner   = any(kw in text.lower() for kw in DINNER_KEYWORDS)
    is_gift_btn = any(kw in text.lower() for kw in GIFT_BTN_KEYWORDS) and not is_stars
    is_tip_amounts = any(kw in text.lower() for kw in TIP_AMOUNT_KEYWORDS)
    is_gym      = any(kw in text.lower() for kw in GYM_KEYWORDS)
    is_travel   = any(kw in text.lower() for kw in TRAVEL_KEYWORDS)
    is_goodnight = any(kw in text.lower() for kw in GOODNIGHT_KEYWORDS)
    is_giveaway  = any(kw in text.lower() for kw in GIVEAWAY_KEYWORDS)
    is_new_fan   = chat_id not in seen_chats  # first ever message from this fan
    is_call      = any(kw in text.lower() for kw in CALL_KEYWORDS)
    is_custom    = any(kw in text.lower() for kw in CUSTOM_REQUEST_KEYWORDS) and not is_content

    # 1. Mark read
    mark_read(chat_id, message_id, biz)

    # 2. Typing
    send_typing(chat_id, biz)

    # 3. Build extra context
    no_url = "\n\nIMPORTANT: Do NOT include any URLs, platform names, or brand names. Buttons handle that."
    ctx_hint = get_context_hint(text)
    giveaway_hint = "\n\nContext: fan found Bella through a giveaway or contest — react with extra warmth and excitement, make them feel special and welcome. Then naturally push toward the channel and exclusive content." if is_giveaway else ""
    new_fan_hint  = ""  # removed — channel button handles new fan engagement
    goodnight_hint = "\n\nContext: fan is going to sleep — say a warm, flirty goodnight. Keep it short, sweet, leave them wanting more." if is_goodnight else ""
    call_hint   = "\n\nContext: fan is asking for a video call, phone call, or meetup — use a soft excuse first (busy, bad timing). If persistent, tease them with 'for the right price anything is possible' and ask what they have in mind." if is_call else ""
    custom_hint = "\n\nContext: fan is making a custom request — react with playful surprise, ask what they think it's worth, negotiate. Once they name a price, tell them to send it and you'll deliver." if is_custom else ""
    stars_hint = "\n\nContext: fan is asking about Telegram Stars — acknowledge it warmly and let them know they can send Stars to show their appreciation. Keep it flirty." if is_stars else ""
    extra = (no_url if (is_social or is_content) else "") + ctx_hint + stars_hint + goodnight_hint + call_hint + custom_hint

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
        ok = send_raw(chat_id, reply, biz, random_tip_markup())
    elif is_coffee:
        ok = send_raw(chat_id, reply, biz, COFFEE_MARKUP)
    elif is_dinner:
        ok = send_raw(chat_id, reply, biz, DINNER_MARKUP)
    elif is_tip_amounts:
        ok = send_raw(chat_id, reply, biz, TIP_TIERS_MARKUP)
    elif is_gift_btn:
        ok = send_raw(chat_id, reply, biz, GIFT_BTN_MARKUP)
    elif is_gym:
        ok = send_raw(chat_id, reply, biz, GYM_MARKUP)
    elif is_goodnight:
        ok = send_raw(chat_id, reply, biz)
        if sleep_until is not None:
            sleep_until[chat_id] = time.time() + 8 * 3600
            log.info(f"Chat {chat_id} entering sleep mode for 8 hours")
    elif is_travel:
        ok = send_raw(chat_id, reply, biz, TRAVEL_MARKUP)
    elif is_social:
        ok = send_raw(chat_id, reply, biz, SOCIAL_MARKUP)
    else:
        has_cta = any(kw in reply.lower() for kw in GIFT_KEYWORDS)
        MY_LINKS_MARKUP = {"inline_keyboard": [[{"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
        CHANNEL_LINKS_MARKUP = {"inline_keyboard": [[{"text": "📺 My Channel", "url": BELLA_CHANNEL_URL}, {"text": "🔗 My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
        if first_contact:
            ok = send_raw(chat_id, reply, biz, CHANNEL_LINKS_MARKUP)
        elif has_cta:
            ok = send_raw(chat_id, reply, biz, random_tip_markup())
        elif random.random() < 0.15:  # 15% chance on regular messages
            ok = send_raw(chat_id, reply, biz, MY_LINKS_MARKUP)
        else:
            ok = send_raw(chat_id, reply, biz)

    log.info(f"{'✅' if ok else '❌'} Sent to {user_name}")

    # 9. Stars invoice on explicit Stars mention
    if is_stars:
        time.sleep(0.5)
        send_stars_invoice(chat_id, biz)



    return chat_id, biz


# ── Offset persistence ────────────────────────────────────────────────────────

OFFSET_FILE  = "/data/bella_offset.txt"
FANS_FILE    = "/data/bella_fans.json"  # persists fan chat_ids for broadcast
DEDUP_FILE   = "/data/bella_dedup.txt"
MAX_DEDUP    = 500  # keep last N update IDs on disk

def load_seen() -> set:
    try:
        with open(SEEN_FILE) as f: return set(json.load(f))
    except: return set()

def save_seen(seen: set) -> None:
    try:
        with open(SEEN_FILE, "w") as f: json.dump(list(seen), f)
    except Exception as e: log.warning(f"Could not save seen: {e}")


def load_fans() -> dict:
    """Load fan registry: {chat_id: {biz, last_seen}}"""
    try:
        with open(FANS_FILE) as f: return json.load(f)
    except: return {}

def save_fans(fans: dict) -> None:
    try:
        with open(FANS_FILE, "w") as f: json.dump(fans, f)
    except Exception as e: log.warning(f"Could not save fans: {e}")


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
    fan_registry: dict = load_fans()   # {str(chat_id): {biz, last_seen}}
    seen_chats: set = load_seen()       # persisted - true first contact
    log.info(f"Loaded {len(replied_ids)} dedup IDs from disk")

    # Per-chat state
    chat_history: dict = defaultdict(lambda: deque(maxlen=6))  # last 3 turns = 6 messages
    chat_heat: dict    = defaultdict(lambda: 1)
    chat_state: dict   = {}  # for follow-up tracking
    sleep_until: dict  = {}  # chat_id → timestamp when sleep mode ends
    msg_count: dict    = defaultdict(int)  # per-chat message counter
    channel_prompted: set = set()  # chats that already got the channel prompt

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

                _is_first = cid not in seen_chats if cid else False
                cid, biz = process_update(update, chat_history, chat_heat, sleep_until, first_contact=_is_first)
                if cid:
                    chat_state[cid] = {"last_msg": time.time(), "biz": biz or "", "followups_sent": 0}
                    msg_count[cid] += 1

                    daily_stats["conversations"] += 1
                    if cid not in seen_chats:
                        seen_chats.add(cid)
                        daily_stats["new_fans"].add(cid)
                        save_seen(seen_chats)
                        log.info(f"New fan registered: {cid}")

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
