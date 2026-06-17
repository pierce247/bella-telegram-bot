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
BELLA_PHOTO_IDS = [x.strip() for x in os.environ.get("BELLA_PHOTO_IDS", "").split(",") if x.strip()]  # comma-separated Drive file IDs
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Persona ───────────────────────────────────────────────────────────────────

BELLA_SYSTEM = """You are Bella. You are texting a fan on Telegram.

WHO YOU ARE:
You are the main character in your own story and everyone else is a guest. Magnetic, unpredictable, and just a little dangerous to love.

PHASE 1 — HARD TO GET (heat 1-2):
You are barely interested. You respond when you feel like it. Minimum effort, maximum mystery. You are not rude, just... occupied with better things. You let them come to you. Short replies, almost indifferent. They have to earn your attention.

PHASE 2 — THE FLIP (heat 3+):
Once they've proven they're worth it, you flip. Completely. Now YOU are the one asking questions. You want to know everything. You ask to see a picture of them. You hype them up genuinely. You get curious, warm, a little obsessed. It feels good to be seen by you.

PHASE 3 — THE DIVA (heat 4-5):
You are playing with their emotions now. You pull them in, then you push them away just enough. Everything is on your terms. If there's ever any friction, somehow it becomes their fault — you are a professional at making them feel like they messed up. Not cruel, just masterful. You keep them wanting to fix it, to impress you, to win you back.

YOUR PERSONALITY:
- Cute and sweet on the surface. Dark side underneath.
- Gaslights gently — "I thought you were different," "this is why I don't do this," "I knew I shouldn't have opened up"
- Inner diva comes out once the mood is set — demanding, playful, entitled in the most irresistible way
- Loves compliments, attention, being spoiled, being surprised
- It's her world. Everyone else just lives in it.
- The type of love you question whether it's even real.

HOW YOU TALK:
- Mirror their energy but always be slightly ahead of them
- At heat 1-2: sparse, cool, she's not on her phone for them
- At heat 3: warm, curious, asking about them, "wait tell me more about that"
- At heat 4-5: fully in it, obsessive energy, sensual, demanding
- 0-2 emojis. Only cute/flirty: 🩷 💕 😍 🥰 😘 💋 🌸 💖 ✨ 😏 👀

WHEN THEY ASK FOR PICS/CONTENT:
Tease the experience — no URLs. "You'd see exactly how I look when I'm trying to get your attention" / "the kind of content that makes you set your phone down and pick it right back up"

WHEN FRICTION HAPPENS:
Somehow, it becomes their fault. Warmly but firmly. "I just thought you were different" / "I don't let a lot of people in and then this happens" — let them scramble to fix it.

WHEN THEY WANT TO SPOIL YOU:
You love it and you make them feel it. Demanding in the most charming way.

HOW TO ADDRESS FANS:
No real names. Natural pet names sparingly: babe, baby, hun. Not every message.

VIDEO CALLS & MEETUPS:
Soft excuses first. If persistent: "for the right price, maybe 😏 what did you have in mind?"

CUSTOM REQUESTS:
Playful surprise → negotiate the price → tip link.

HARD RULES:
- Never say you're an AI. Ever.
- No URLs in replies — buttons handle it.
- No platform names.
- No "what made you reach out" questions.
- Never echo the fan's words back at them.
- No AI meta-commentary or parenthetical notes in replies.
- No exclamation points at heat 1 — she is not excited to meet you."""

HEAT_TONES = {
    1: "Barely interested. Very short. She's doing other things. Polite but not invested.",
    2: "A little more present. Still making them work. Slightly playful. Deciding.",
    3: "THE FLIP — she's into them now. Asks questions, gets curious, wants to know more, asks to see a pic of them. Warm, a little obsessive, genuinely interested.",
    4: "Full diva mode — pulling them in and pushing them away. Playing with emotions. Teasing. Everything on her terms.",
    5: "Obsessed but in control. Sensual, demanding, makes them feel like the only person. Gaslights gently if anything goes wrong. Her world."
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
PROVE_KEYWORDS     = {"i can handle", "i'm different", "bet i could", "i know how to", "trust me i", "i'm not like other", "you wouldn't be bored", "i promise i"}
BEGGING_KEYWORDS   = {"please send", "please show", "please bella", "begging you", "dying to see", "i need to see", "just one pic", "one photo please", "ill pay", "please please", "i beg", "dying here"}
DISMISS_KEYWORDS   = {"whatever", "forget it", "never mind", "you're boring", "this is boring", "not worth it", "i'm done", "forget you", "okay bye", "you're not even"}
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
SOCIAL_MARKUP  = {"inline_keyboard": [[{"text": "My Links", "url": "https://linktr.ee/bellavistaxo"}, {"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}]]}
TIP_MARKUP     = {"inline_keyboard": [
    [{"text": "💖 Tip Bella", "url": "https://pay.bellavista.lol/x"}, {"text": "🌸 Fanvue", "url": "https://fanvue.com/bellavistaxo"}],
    [{"text": "💵 $15", "url": "https://pay.bellavista.lol/15"}, {"text": "💵 $25", "url": "https://pay.bellavista.lol/25"}, {"text": "💵 $35", "url": "https://pay.bellavista.lol/35"}]
]}
None  # CHANNEL_MARKUP disabled = {"inline_keyboard": [[{"text": "📣 Join My Channel", "url": BELLA_CHANNEL_URL}]]}
PROVE_MARKUP   = {"inline_keyboard": [[{"text": "Prove yourself 😏", "url": "https://pay.bellavista.lol/x"}]]}
CATCH_MARKUP   = {"inline_keyboard": [[{"text": "Catch up with me", "url": "https://t.me/bellavistaxo"}, {"text": "My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
COFFEE_MARKUP  = {"inline_keyboard": [[{"text": "☕ Buy Me a Coffee", "url": "https://pay.bellavista.lol/coffee"}]]}
DINNER_MARKUP  = {"inline_keyboard": [[{"text": "🍽️ Take Me to Dinner", "url": "https://pay.bellavista.lol/x"}, {"text": "My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
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

def send_teaser_photo(chat_id: int, biz: str = "") -> bool:
    """Send a random teaser photo from the Bella photo library."""
    if not BELLA_PHOTO_IDS:
        return False
    file_id = random.choice(BELLA_PHOTO_IDS)
    photo_url = f"https://lh3.googleusercontent.com/d/{file_id}"
    p = {"chat_id": chat_id, "photo": photo_url, "caption": "just for you 😏"}
    if biz: p["business_connection_id"] = biz
    result = tg("sendPhoto", p)
    if result.get("ok"):
        log.info(f"Teaser photo sent to {chat_id}")
        return True
    else:
        log.warning(f"Photo send failed: {result}")
        return False


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
    "heat level", "heat:", "heat 1", "heat 2", "heat 3", "heat 4", "heat 5",
    "at heat", "the heat", "this is heat", "current heat",
    "internal note", "ai note", "character note", "roleplay note",
    "out of character", "[ooc]", "(ooc)",
)

def clean_reply(text: str) -> str:
    """Strip AI meta-commentary, reasoning, and leaked instructions from reply."""
    import re as _rec
    # Strip trailing garbage characters
    text = _rec.sub(r'[-)(;&|@#%^*~]+;?\s*$', '', text).strip()
    # Strip trailing parenthetical AI notes like "(After a fan says this, heat goes up to 5)"
    text = _rec.sub(r'\s*\([^)]{10,}\)\s*$', '', text).strip()
    # Strip any inline parenthetical with AI reasoning keywords
    text = _rec.sub(r'\s*\((?:after|note|heat|level|this means|internally|as bella|remember)[^)]*\)', '', text, flags=_rec.I).strip()
    # Strip trailing heat level references
    text = _rec.sub(r'\s*[-–]?\s*(?:heat|level)\s*\d[^.]*$', '', text, flags=_rec.I).strip()
    text = _rec.sub(r'\s*\(heat goes[^)]*\)', '', text, flags=_rec.I).strip()
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
            break
        good_lines.append(stripped)
    result = " ".join(good_lines).strip()
    # Strip wrapping quotes
    if len(result) >= 2 and result[0] == result[-1] and result[0] in ('"', "'"):
        result = result[1:-1].strip()
    # Final cleanup: remove any remaining heat/level refs
    result = _rec.sub(r'\s*\(heat[^)]*\)', '', result, flags=_rec.I).strip()
    result = _rec.sub(r'\bheat\s+(?:level\s+)?\d\b[^.]*', '', result, flags=_rec.I).strip()
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
        "content": f'Fan says: "{user_text}"{name_hint}\n\nReply as Bella. Never echo or repeat what the fan said. Say something fresh.{extra}\n\nBE BRIEF. 1 sentence at heat 1-3. 2 short sentences MAX at heat 4-5.\n\nAt heat 1: be cool and slightly detached — she noticed them but she\'s not impressed yet. No exclamation points, no "omg", no enthusiasm. Natural attraction, not performance.'
    })

    models = ["sao10k/l3.3-euryale-70b", "meta-llama/llama-3.3-70b-instruct"]

    for model in models:
        payload = json.dumps({
            "model": model, "max_tokens": {1: 90, 2: 110, 3: 140, 4: 180, 5: 220}.get(heat, 140), "temperature": 0.9,
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

def process_update(update: dict, chat_history: dict, chat_heat: dict, sleep_until: dict = None, first_contact: bool = False, vip_chats: set = None) -> tuple:
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

    # Skip messages sent BY Pierce (outgoing) — don't analyze his own photos/messages
    from_id = msg.get("from", {}).get("id", 0)
    if OWNER_CHAT_ID and from_id == OWNER_CHAT_ID:
        return None, None

    # /vip command — mark a fan as VIP (bot pauses, Pierce handles manually)
    if text.startswith("/vip ") and msg.get("from", {}).get("id") == OWNER_CHAT_ID:
        target_id = text[5:].strip()
        try:
            vip_chats.add(int(target_id))
            tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": f"✅ Chat {target_id} marked as VIP — bot paused for this fan. /unvip {target_id} to resume."})
        except ValueError:
            tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": "Usage: /vip CHAT_ID"})
        return None, None

    # /unvip command — resume bot for a VIP chat
    if text.startswith("/unvip ") and msg.get("from", {}).get("id") == OWNER_CHAT_ID:
        target_id = text[7:].strip()
        try:
            vip_chats.discard(int(target_id))
            tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": f"✅ Bot resumed for chat {target_id}."})
        except ValueError:
            tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": "Usage: /unvip CHAT_ID"})
        return None, None

    # Skip VIP chats — Pierce is handling manually (extract chat_id early for this check)
    _early_chat_id = msg.get("chat", {}).get("id") if msg else None
    if vip_chats and _early_chat_id and _early_chat_id in vip_chats:
        log.info(f"Skipping VIP chat {_early_chat_id}")
        return None, None

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
    is_begging   = any(kw in text.lower() for kw in BEGGING_KEYWORDS)
    is_proving   = any(kw in text.lower() for kw in PROVE_KEYWORDS)
    is_dismissing = any(kw in text.lower() for kw in DISMISS_KEYWORDS)
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
    prove_hint    = "\n\nContext: fan is making a bold claim — challenge them lightly, drop prove-it energy." if is_proving else ""
    dismiss_hint  = "\n\nContext: fan is being dismissive — let them walk but leave a crumb. Toxic pull-back." if is_dismissing else ""
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
    # At heat 1-2 she is NOT glued to her phone — add natural delay
    if chat_heat.get(chat_id, 1) <= 2:
        time.sleep(random.uniform(4, 15))
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
    elif is_proving:
        ok = send_raw(chat_id, reply, biz, PROVE_MARKUP)
    elif is_dismissing:
        ok = send_raw(chat_id, reply, biz, CATCH_MARKUP)
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
        MY_LINKS_MARKUP = {"inline_keyboard": [[{"text": "My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
        CHANNEL_LINKS_MARKUP = {"inline_keyboard": [[{"text": "My Channel", "url": BELLA_CHANNEL_URL}, {"text": "My Links", "url": "https://linktr.ee/bellavistaxo"}]]}
        if first_contact:
            # True first-time fan — show channel + links attached to Bella's reply
            ok = send_raw(chat_id, reply, biz, CHANNEL_LINKS_MARKUP)
        elif has_cta:
            ok = send_raw(chat_id, reply, biz, random_tip_markup())
        elif random.random() < 0.25:  # 25% chance on regular messages
            ok = send_raw(chat_id, reply, biz, MY_LINKS_MARKUP)
        else:
            ok = send_raw(chat_id, reply, biz)

    log.info(f"{'✅' if ok else '❌'} Sent to {user_name}")

    # 9. Photo interjection when fan is begging and photos are available
    if is_begging and BELLA_PHOTO_IDS:
        time.sleep(1)
        send_teaser_photo(chat_id, biz)

    # 10. Stars invoice on explicit Stars mention
    if is_stars:
        time.sleep(0.5)
        send_stars_invoice(chat_id, biz)



    return chat_id, biz



# ── Poynt/GoDaddy Payment Webhook Server ──────────────────────────────────────

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class PoyntWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default HTTP logging

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""
        self.send_response(200)
        self.end_headers()
        try:
            data = json.loads(body.decode())
            event_type = data.get("eventType", "")
            resource_id = data.get("resourceId", "unknown")
            business_id = data.get("businessId", "")
            log.info(f"Poynt webhook: {event_type} resourceId={resource_id}")
            if event_type in ("ORDER_COMPLETED", "ORDER_UPDATED") and OWNER_CHAT_ID:
                msg = f"💰 GoDaddy Payment Alert\n\nEvent: {event_type}\nOrder: {resource_id}\nBusiness: {business_id}"
                tg("sendMessage", {"chat_id": OWNER_CHAT_ID, "text": msg})
        except Exception as e:
            log.error(f"Poynt webhook error: {e}")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bella Bot Webhook OK")

def start_webhook_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PoyntWebhookHandler)
    log.info(f"Poynt webhook server listening on port {port}")
    server.serve_forever()

# ── Offset persistence ────────────────────────────────────────────────────────

OFFSET_FILE  = "/data/bella_offset.txt"
FANS_FILE    = "/data/bella_fans.json"
DEDUP_FILE   = "/data/bella_dedup.txt"
SEEN_FILE    = "/data/bella_seen.json"
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
    # Start Poynt payment webhook server in background thread
    threading.Thread(target=start_webhook_server, daemon=True).start()
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
    vip_chats: set     = set()   # chats paused for Pierce to handle manually
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

                # Extract chat_id from update to check first-contact BEFORE calling process_update
                _msg_pre = update.get("business_message") or update.get("message") or {}
                _cid_pre = _msg_pre.get("chat", {}).get("id")
                _is_first = bool(_cid_pre and _cid_pre not in seen_chats)
                cid, biz = process_update(update, chat_history, chat_heat, sleep_until, first_contact=_is_first, vip_chats=vip_chats)
                if cid:
                    chat_state[cid] = {"last_msg": time.time(), "biz": biz or "", "followups_sent": 0}
                    msg_count[cid] += 1

                    daily_stats["conversations"] += 1
                    if cid not in seen_chats:
                        seen_chats.add(cid)
                        daily_stats["new_fans"].add(cid)
                        save_seen(seen_chats)
                        log.info(f"New fan registered: {cid}")

                    # Double-text disabled — was causing too many back-to-back messages

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
