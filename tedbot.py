#!/usr/bin/env python3
"""
TedBot - Telegram bot with local LLM, notes, reminders, tags, soft delete, conversation memory.
Intent/category/content parsing is handled by a single structured LLM call with keyword fallback.
"""

import os
import re
import json
import sqlite3
import threading
import time
import logging
from collections import deque
from datetime import datetime, timedelta

import requests
import schedule
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://172.17.0.1:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "gemma3:1b")
DB_PATH         = os.environ.get("DB_PATH", os.path.expanduser("~/.tedbot/tedbot.db"))
HISTORY_LEN     = 6

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CATEGORIES = ["grocery", "baby", "work", "health", "finance", "general"]

CATEGORY_TAGS = {
    "baby":    ["weight", "growth", "poop", "pee", "feed", "activity"],
    "grocery": ["costco", "local", "walmart", "butcher"],
}

SYSTEM_PROMPT = """You are TedBot, a smart personal assistant for Ted.
You help manage notes by category (grocery, baby, work, health, finance, general), reminders, and general questions.
You remember the recent conversation so you understand follow-ups like "change it to 9am" or "no, make it tuesday".
When Ted corrects or updates something, acknowledge what you changed.
Be concise and friendly. Max 2-3 sentences unless Ted asks for more."""

# ── Conversation memory ───────────────────────────────────────────────────────
conversation_history: dict[int, deque] = {}

def get_history(user_id: int) -> deque:
    if user_id not in conversation_history:
        conversation_history[user_id] = deque(maxlen=HISTORY_LEN)
    return conversation_history[user_id]

def add_to_history(user_id: int, role: str, content: str):
    get_history(user_id).append({"role": role, "content": content})

def format_history(user_id: int) -> str:
    history = get_history(user_id)
    if not history:
        return ""
    lines = []
    for msg in history:
        prefix = "Ted" if msg["role"] == "user" else "TedBot"
        lines.append(f"{prefix}: {msg['content']}")
    return "\n".join(lines)

# Track last saved item for follow-up corrections
last_action: dict[int, dict] = {}

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL DEFAULT 'general',
        content TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        archived_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        remind_at TEXT NOT NULL,
        fired_at TEXT,
        created_at TEXT NOT NULL,
        archived_at TEXT)""")
    _migrate(con)
    con.commit()
    con.close()

def _migrate(con):
    existing_note_cols = {r[1] for r in con.execute("PRAGMA table_info(notes)")}
    existing_rem_cols  = {r[1] for r in con.execute("PRAGMA table_info(reminders)")}
    if "tags" not in existing_note_cols:
        con.execute("ALTER TABLE notes ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
    if "archived_at" not in existing_note_cols:
        con.execute("ALTER TABLE notes ADD COLUMN archived_at TEXT")
    if "fired_at" not in existing_rem_cols:
        con.execute("ALTER TABLE reminders ADD COLUMN fired_at TEXT")
    if "archived_at" not in existing_rem_cols:
        con.execute("ALTER TABLE reminders ADD COLUMN archived_at TEXT")
    if "done" in existing_rem_cols:
        con.execute("""UPDATE reminders SET archived_at = datetime('now')
                       WHERE archived_at IS NULL AND done = 1""")

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# ── LLM helpers ───────────────────────────────────────────────────────────────
def _ollama(prompt: str, max_tokens: int = 80, timeout: int = 90) -> str | None:
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": max_tokens}},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        log.warning(f"Ollama call failed: {e}")
        return None

def ask_llama(user_id: int, user_message: str, extra_context: str = "") -> str:
    history_str = format_history(user_id)
    prompt = SYSTEM_PROMPT + "\n\n"
    if extra_context:
        prompt += f"Context:\n{extra_context}\n\n"
    if history_str:
        prompt += f"Recent conversation:\n{history_str}\n\n"
    prompt += f"Ted: {user_message}\nTedBot:"
    result = _ollama(prompt, max_tokens=200, timeout=30)
    return result if result else "⚠️ Could not reach AI model right now."

# ── Structured message parser ─────────────────────────────────────────────────
# The LLM receives a tightly constrained prompt and returns a fixed JSON schema.
# It slot-fills — no open-ended reasoning required. Reliable on a 1B model.
# Python handles all datetime conversion; LLM only extracts the raw time expression.

PARSE_PROMPT = '''You are a message parser for a personal assistant. Reply with ONLY valid JSON, nothing else. No explanation, no markdown.

Fields:
- intent: one of note | reminder | correction | show | chat
- category: one of grocery | baby | work | health | finance | general
- content: the actual item or task. Strip ONLY the leading command prefix (remind me to / add / put on list / log). Keep the full task. "remind me to go to work at 8am" -> "go to work", NOT empty.
- time_expr: raw time/date string if present (e.g. "7am", "tomorrow", "monday at 9pm"), else null
- tags: comma-separated tags from this fixed list only. baby:(weight,growth,poop,pee,feed,activity) grocery:(costco,local,walmart,butcher). Empty string if none apply.
- correction_of: "reminder" or "note" if intent is correction, else null

Message: "remind me to go to work at 8am"
{"intent":"reminder","category":"work","content":"go to work","time_expr":"8am","tags":"","correction_of":null}

Message: "remind me to do a workout at 7am"
{"intent":"reminder","category":"health","content":"workout","time_expr":"7am","tags":"","correction_of":null}

Message: "remind me to pick up the kids at 3pm"
{"intent":"reminder","category":"general","content":"pick up the kids","time_expr":"3pm","tags":"","correction_of":null}

Message: "remind me to call the dentist on friday at 3pm"
{"intent":"reminder","category":"health","content":"call the dentist","time_expr":"friday at 3pm","tags":"","correction_of":null}

Message: "remind me to take out the trash tomorrow morning"
{"intent":"reminder","category":"general","content":"take out the trash","time_expr":"tomorrow morning","tags":"","correction_of":null}

Message: "add tortillas to the grocery list"
{"intent":"note","category":"grocery","content":"tortillas","time_expr":null,"tags":"","correction_of":null}

Message: "put milk eggs and bread on the grocery list from costco"
{"intent":"note","category":"grocery","content":"milk, eggs, bread","time_expr":null,"tags":"costco","correction_of":null}

Message: "need to get diapers from walmart"
{"intent":"note","category":"grocery","content":"diapers","time_expr":null,"tags":"walmart","correction_of":null}

Message: "baby fed at 2pm"
{"intent":"note","category":"baby","content":"baby fed","time_expr":"2pm","tags":"feed","correction_of":null}

Message: "logged baby weight 14 pounds"
{"intent":"note","category":"baby","content":"baby weight 14 pounds","time_expr":null,"tags":"weight","correction_of":null}

Message: "baby had a big poop this morning"
{"intent":"note","category":"baby","content":"baby had a big poop","time_expr":"this morning","tags":"poop","correction_of":null}

Message: "tax deadline is tomorrow"
{"intent":"note","category":"finance","content":"tax deadline","time_expr":"tomorrow","tags":"","correction_of":null}

Message: "make it 10am instead"
{"intent":"correction","category":"general","content":"","time_expr":"10am","tags":"","correction_of":"reminder"}

Message: "change it to tuesday"
{"intent":"correction","category":"general","content":"","time_expr":"tuesday","tags":"","correction_of":"reminder"}

Message: "actually make it baby"
{"intent":"correction","category":"baby","content":"","time_expr":null,"tags":"","correction_of":"note"}

Message: "show me my work notes"
{"intent":"show","category":"work","content":"","time_expr":null,"tags":"","correction_of":null}

Message: "what are my reminders"
{"intent":"show","category":"general","content":"reminders","time_expr":null,"tags":"","correction_of":null}

Message: "what is the capital of France"
{"intent":"chat","category":"general","content":"what is the capital of France","time_expr":null,"tags":"","correction_of":null}

Now parse this:
Message: "{message}"'''



def _keyword_fallback(text: str, user_id: int) -> dict:
    """Keyword-based parser — used silently when LLM call fails."""
    t = text.lower()
    intent = "chat"

    if user_id in last_action:
        correction_words = ["no, ", "no that", "change it", "change that",
                            "make it", "actually make", "actually change",
                            "instead make", "update it", "edit it",
                            "fix it", "that's wrong", "not that",
                            "wrong time", "wrong day", "wrong category"]
        if any(k in t for k in correction_words):
            intent = "correction"

    if intent == "chat":
        if any(k in t for k in ["remind me", "reminder", "don't let me forget", "remember to"]):
            intent = "reminder"
        elif any(k in t for k in ["add to", "add this to", "note:", "save this",
                                    "grocery list", "shopping list", "put on the list",
                                    "log this", "log that"]) \
             or re.search(r"^add .+ to (the )?\w+", t):
            intent = "note"
        elif any(k in t for k in ["show me", "list all", "show all", "what are my",
                                    "display all", "what's on", "whats on", "show my"]):
            intent = "show"

    category = "general"
    for cat in CATEGORIES:
        if cat in t:
            category = cat; break
    if category == "general":
        if any(k in t for k in ["milk","eggs","bread","food","buy","store","shop",
                                  "fruit","meat","costco","walmart","butcher","groceries"]):
            category = "grocery"
        elif any(k in t for k in ["baby","diaper","formula","pediatr","stroller",
                                    "crib","infant","weight","feed","poop","pee","growth"]):
            category = "baby"
        elif any(k in t for k in ["work","meeting","client","project","email","boss","deadline"]):
            category = "work"
        elif any(k in t for k in ["doctor","appointment","medicine","pharmacy",
                                    "sick","exercise","workout"]):
            category = "health"
        elif any(k in t for k in ["pay","bill","bank","money","budget",
                                    "invoice","tax","insurance"]):
            category = "finance"

    return {"intent": intent, "category": category, "content": text,
            "time_expr": None, "correction_of": None}


def parse_message(text: str, user_id: int) -> dict:
    """
    Single structured LLM call returning intent, category, content,
    time_expr, correction_of. Falls back to keyword detection silently.
    """
    prompt = PARSE_PROMPT.replace("{message}", text.replace('"', "'"))
    raw = _ollama(prompt, max_tokens=100)

    if raw:
        cleaned = re.sub(r"```json|```", "", raw).strip()
        match = re.search(r"\{.*?\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                intent   = parsed.get("intent", "")
                category = parsed.get("category", "general")
                if intent in ("note", "reminder", "correction", "show", "chat") \
                   and category in CATEGORIES:
                    log.info(f"LLM parse OK: {parsed}")
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

    log.warning("LLM parse failed — using keyword fallback")
    return _keyword_fallback(text, user_id)


# ── Time parsing ──────────────────────────────────────────────────────────────
# LLM extracts the raw time expression string.
# This function converts it to an actual datetime.
# Keeping these separate means the LLM never formats timestamps.

def _extract_time_from_expr(t: str, base: datetime) -> datetime:
    """
    Given a base date and a time expression string, extract HH:MM and apply it.
    Falls back to the base datetime unchanged if no time found.
    """
    # Try HH:MM am/pm
    tm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", t)
    if tm:
        h, m = int(tm.group(1)), int(tm.group(2))
        ampm = tm.group(3)
        if ampm == "pm" and h < 12: h += 12
        elif ampm == "am" and h == 12: h = 0
        return base.replace(hour=h, minute=m, second=0, microsecond=0)
    # Try Xam / Xpm
    at_match = re.search(r"(\d{1,2})\s*(am|pm)", t)
    if at_match:
        h = int(at_match.group(1))
        ampm = at_match.group(2)
        if ampm == "pm" and h < 12: h += 12
        elif ampm == "am" and h == 12: h = 0
        return base.replace(hour=h, minute=0, second=0, microsecond=0)
    # Try "at X"
    at_bare = re.search(r"at\s+(\d{1,2})", t)
    if at_bare:
        h = int(at_bare.group(1))
        if h < 7: h += 12
        return base.replace(hour=h, minute=0, second=0, microsecond=0)
    return base

def parse_reminder_time(text: str) -> datetime | None:
    t = text.lower().strip() if text else ""
    if not t:
        return None
    now = datetime.now()

    if "tomorrow" in t:
        # Default 9am but respect explicit time like "tomorrow at 5pm"
        base = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return _extract_time_from_expr(t, base)

    if "today" in t:
        # Default 6pm but respect explicit time like "today at 3pm"
        base = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return _extract_time_from_expr(t, base)

    days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for i, day in enumerate(days):
        if day in t:
            delta = (i - now.weekday()) % 7 or 7
            base = (now + timedelta(days=delta)).replace(second=0, microsecond=0)
            tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
            if tm:
                h = int(tm.group(1))
                m = int(tm.group(2)) if tm.group(2) else 0
                ampm = tm.group(3)
                if ampm == "pm" and h < 12: h += 12
                elif ampm == "am" and h == 12: h = 0
                elif not ampm and h < 7: h += 12
                return base.replace(hour=h, minute=m)
            return base.replace(hour=9, minute=0)

    # HH:MM with optional am/pm
    tm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", t)
    if tm:
        h, m = int(tm.group(1)), int(tm.group(2))
        ampm = tm.group(3)
        if ampm == "pm" and h < 12: h += 12
        elif ampm == "am" and h == 12: h = 0
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    # "7am", "3pm"
    at_match = re.search(r"(?:at\s+)?(\d{1,2})\s*(am|pm)", t)
    if at_match:
        h = int(at_match.group(1))
        ampm = at_match.group(2)
        if ampm == "pm" and h < 12: h += 12
        elif ampm == "am" and h == 12: h = 0
        return now.replace(hour=h, minute=0, second=0, microsecond=0)

    # "at 9"
    at_bare = re.search(r"\bat\s+(\d{1,2})\b", t)
    if at_bare:
        h = int(at_bare.group(1))
        if h < 7: h += 12
        return now.replace(hour=h, minute=0, second=0, microsecond=0)

    # "in 2 hours / 30 minutes"
    inm = re.search(r"in\s+(\d+)\s+(hour|minute|min)", t)
    if inm:
        n = int(inm.group(1))
        return now + (timedelta(hours=n) if "hour" in inm.group(2) else timedelta(minutes=n))

    return None


# ── Tag extraction ────────────────────────────────────────────────────────────
def extract_tags(content: str, category: str) -> str:
    vocab = CATEGORY_TAGS.get(category, [])
    if not vocab:
        return ""
    t = content.lower()
    return ",".join(tag for tag in vocab if tag in t)


# ── Auth ──────────────────────────────────────────────────────────────────────
def auth(update: Update) -> bool:
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return False
    return True


# ── Debug command ─────────────────────────────────────────────────────────────
async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show exactly how TedBot parses a message. Usage: /debug <message>"""
    if not auth(update): return
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/debug <your message>`\nExample: `/debug remind me workout at 7am`",
            parse_mode="Markdown")
        return

    text = " ".join(ctx.args)
    user_id = update.effective_user.id
    parsed = parse_message(text, user_id)
    time_expr = parsed.get("time_expr")
    parsed_dt = parse_reminder_time(time_expr) if time_expr else None
    action = last_action.get(user_id)
    action_str = f"{action['type']} #{action['id']} — {action.get('content','')}" \
                 if action else "none"

    msg = (
        f"🔍 *Debug:* `{text}`\n\n"
        f"*Intent:* `{parsed.get('intent')}`\n"
        f"*Category:* `{parsed.get('category')}`\n"
        f"*Content:* `{parsed.get('content')}`\n"
        f"*Tags (LLM):* `{parsed.get('tags') or 'none'}`\n"
        f"*Time expr:* `{time_expr}`\n"
        f"*Parsed datetime:* `{parsed_dt.strftime('%Y-%m-%d %H:%M') if parsed_dt else 'None'}`\n"
        f"*Correction of:* `{parsed.get('correction_of')}`\n"
        f"*Last action in memory:* `{action_str}`\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Correction handler ────────────────────────────────────────────────────────
async def handle_correction(update: Update, user_id: int,
                             parsed: dict, original_text: str) -> bool:
    action = last_action.get(user_id)
    if not action:
        return False

    time_expr = parsed.get("time_expr")
    new_time = parse_reminder_time(time_expr) if time_expr else None

    if action["type"] == "reminder":
        if new_time:
            con = get_db()
            con.execute("UPDATE reminders SET remind_at=? WHERE id=?",
                        (new_time.strftime("%Y-%m-%d %H:%M"), action["id"]))
            con.commit(); con.close()
            last_action[user_id]["remind_at"] = new_time.strftime("%Y-%m-%d %H:%M")
            reply = f"✅ Updated! Reminder now set for {new_time.strftime('%A, %b %d at %H:%M')}."
            await update.message.reply_text(reply)
            add_to_history(user_id, "user", original_text)
            add_to_history(user_id, "assistant", reply)
            return True

        new_content = parsed.get("content", "").strip()
        if new_content and len(new_content) > 3:
            con = get_db()
            con.execute("UPDATE reminders SET content=? WHERE id=?",
                        (new_content, action["id"]))
            con.commit(); con.close()
            last_action[user_id]["content"] = new_content
            reply = f"✅ Reminder updated to: _{new_content}_"
            await update.message.reply_text(reply, parse_mode="Markdown")
            add_to_history(user_id, "user", original_text)
            add_to_history(user_id, "assistant", reply)
            return True

    elif action["type"] == "note":
        new_cat = parsed.get("category", "general")
        if new_cat in CATEGORIES:
            con = get_db()
            con.execute("UPDATE notes SET category=? WHERE id=?",
                        (new_cat, action["id"]))
            con.commit(); con.close()
            last_action[user_id]["category"] = new_cat
            reply = f"✅ Moved to *{new_cat}*."
            await update.message.reply_text(reply, parse_mode="Markdown")
            add_to_history(user_id, "user", original_text)
            add_to_history(user_id, "assistant", reply)
            return True

    return False


# ── Commands ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text(
        "👋 Hey Ted! TedBot is ready.\n\n"
        "Just talk naturally:\n"
        "• _\"add tortillas to grocery list\"_\n"
        "• _\"remind me workout at 7am\"_\n"
        "• _\"baby fed at 2pm\"_ → auto-tagged\n"
        "• _\"make it 10am\"_ → updates last reminder\n\n"
        "Commands: /notes /reminders /categories /find /archive /debug /help",
        parse_mode="Markdown"
    )

async def cmd_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    category = ctx.args[0].lower() if ctx.args else None
    con = get_db()
    if category:
        rows = con.execute(
            "SELECT id, category, content, tags, created_at FROM notes "
            "WHERE category=? AND archived_at IS NULL ORDER BY created_at DESC", (category,)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT id, category, content, tags, created_at FROM notes "
            "WHERE archived_at IS NULL ORDER BY category, created_at DESC"
        ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text(
            f"No notes{' in *'+category+'*' if category else ''} yet.",
            parse_mode="Markdown")
        return
    grouped = {}
    for row in rows:
        tag_str = f" `[{row['tags']}]`" if row['tags'] else ""
        grouped.setdefault(row['category'], []).append(
            f"  `[{row['id']}]` {row['content']}{tag_str}")
    msg = "📋 *Notes*\n\n" + "\n\n".join(
        f"*{cat.upper()}*\n" + "\n".join(items) for cat, items in grouped.items())
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    con = get_db()
    rows = con.execute(
        "SELECT id, content, remind_at, fired_at FROM reminders "
        "WHERE archived_at IS NULL ORDER BY remind_at ASC"
    ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("No reminders.")
        return
    now = datetime.now()
    msg = "⏰ *Reminders*\n\n"
    for row in rows:
        try:
            dt = datetime.strptime(row['remind_at'], "%Y-%m-%d %H:%M")
            overdue = "🟡 " if dt < now else ""
            fired = " _(notified)_" if row['fired_at'] else ""
        except:
            overdue = ""; fired = ""
        msg += f"{overdue}`[{row['id']}]` {row['content']}{fired}\n    📅 {row['remind_at']}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/find <keyword>`\n\nExamples:\n"
            "• `/find dentist` — search notes and reminders\n"
            "• `/find #feed` — search by tag\n"
            "• `/find baby today` — multiple keywords",
            parse_mode="Markdown")
        return

    raw_query = " ".join(ctx.args).lower().strip()

    # Tag search: /find #feed or /find feed (in tag vocab)
    all_tags = [t for tags in CATEGORY_TAGS.values() for t in tags]
    tag_query = raw_query.lstrip("#")
    is_tag_search = raw_query.startswith("#") or tag_query in all_tags

    con = get_db()

    if is_tag_search:
        # Exact tag match — fast and precise
        like = f"%{tag_query}%"
        notes = con.execute(
            "SELECT id, category, content, tags, created_at FROM notes "
            "WHERE archived_at IS NULL AND LOWER(tags) LIKE ? "
            "ORDER BY created_at DESC LIMIT 20",
            (like,)
        ).fetchall()
        reminders = []
        search_label = f"tag *#{tag_query}*"
    else:
        # Multi-keyword search — split on spaces, ALL terms must match
        # This is much more precise than a single LIKE on the full query string
        terms = raw_query.split()
        # Build AND conditions for each term across content, tags, category
        note_conditions = " AND ".join(
            "(LOWER(content) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(category) LIKE ?)"
            for _ in terms
        )
        note_params = []
        for term in terms:
            like = f"%{term}%"
            note_params.extend([like, like, like])

        notes = con.execute(
            f"SELECT id, category, content, tags, created_at FROM notes "
            f"WHERE archived_at IS NULL AND {note_conditions} "
            f"ORDER BY created_at DESC LIMIT 20",
            note_params
        ).fetchall()

        rem_conditions = " AND ".join("LOWER(content) LIKE ?" for _ in terms)
        rem_params = [f"%{t}%" for t in terms]
        reminders = con.execute(
            f"SELECT id, content, remind_at FROM reminders "
            f"WHERE archived_at IS NULL AND {rem_conditions} "
            f"ORDER BY remind_at ASC LIMIT 10",
            rem_params
        ).fetchall()
        search_label = f"*{raw_query}*"

    con.close()

    if not notes and not reminders:
        await update.message.reply_text(
            f"Nothing found for {search_label}.", parse_mode="Markdown")
        return

    msg = f"🔍 Results for {search_label}\n\n"
    if notes:
        msg += f"*Notes* ({len(notes)})\n"
        for r in notes:
            tag_str = f" `[{r['tags']}]`" if r['tags'] else ""
            date_str = r['created_at'][:10] if r['created_at'] else ""
            msg += f"  `[{r['id']}]` _{r['category']}_ {date_str} — {r['content']}{tag_str}\n"
        msg += "\n"
    if reminders:
        msg += f"*Reminders* ({len(reminders)})\n"
        for r in reminders:
            msg += f"  `[{r['id']}]` {r['content']} — 📅 {r['remind_at']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_archive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: `/archive note 5` or `/archive reminder 3`", parse_mode="Markdown")
        return
    kind, id_ = ctx.args[0].lower(), ctx.args[1]
    table = "notes" if kind == "note" else "reminders" if kind == "reminder" else None
    if not table:
        await update.message.reply_text("Use `note` or `reminder`.", parse_mode="Markdown")
        return
    con = get_db()
    con.execute(f"UPDATE {table} SET archived_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), id_))
    con.commit(); con.close()
    await update.message.reply_text(
        f"✅ Archived {kind} #{id_} — still in the database, just hidden.")

async def cmd_categories(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    con = get_db()
    rows = con.execute(
        "SELECT category, COUNT(*) as n FROM notes WHERE archived_at IS NULL "
        "GROUP BY category ORDER BY n DESC"
    ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("No notes yet.")
        return
    msg = "🗂 *Notes by Category*\n\n" + "\n".join(
        f"• *{r['category']}*: {r['n']} note{'s' if r['n']!=1 else ''}" for r in rows)
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Main message handler ──────────────────────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Single LLM call — returns intent, category, content, time_expr, correction_of
    parsed   = parse_message(text, user_id)
    intent   = parsed.get("intent", "chat")
    category = parsed.get("category", "general")
    content  = parsed.get("content", text).strip() or text
    time_expr = parsed.get("time_expr")

    # ── Correction ────────────────────────────────────────────────────────────
    if intent == "correction":
        handled = await handle_correction(update, user_id, parsed, text)
        if handled:
            return
        intent = "chat"  # no last_action to correct — fall through to chat

    # ── Note ──────────────────────────────────────────────────────────────────
    if intent == "note":
        # Merge LLM-detected tags (from parse_message) with keyword-detected tags
        # LLM tags come from the structured parse; keyword tags catch anything missed
        llm_tags = [t.strip() for t in parsed.get("tags", "").split(",") if t.strip()]
        kw_tags  = extract_tags(content, category).split(",") if extract_tags(content, category) else []
        tags = ",".join(dict.fromkeys(llm_tags + [t for t in kw_tags if t not in llm_tags]))
        con = get_db()
        cur = con.execute(
            "INSERT INTO notes (category, content, tags, created_at) VALUES (?, ?, ?, ?)",
            (category, content, tags, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        con.commit()
        note_id = cur.lastrowid
        con.close()
        last_action[user_id] = {"type": "note", "id": note_id,
                                 "category": category, "content": content}
        tag_hint = f"\n🏷 Tagged: _{tags}_" if tags else ""
        cat_hint = ""
        if category in CATEGORY_TAGS:
            unused = [t for t in CATEGORY_TAGS[category] if t not in tags.split(",")]
            if unused:
                cat_hint = f"\nOptional tags: {', '.join(unused[:3])}"
        reply = (f"✅ Saved to *{category}*:\n_{content}_{tag_hint}{cat_hint}\n\n"
                 "Say _\"actually make it work\"_ to change category.")
        add_to_history(user_id, "user", text)
        add_to_history(user_id, "assistant", f"Saved note to {category}: {content}")
        await update.message.reply_text(reply, parse_mode="Markdown")

    # ── Reminder ──────────────────────────────────────────────────────────────
    elif intent == "reminder":
        # Parse time from LLM-extracted expression — far more accurate than
        # running the regex over the full raw message
        remind_at = parse_reminder_time(time_expr) if time_expr else None

        if remind_at:
            con = get_db()
            cur = con.execute(
                "INSERT INTO reminders (content, remind_at, created_at) VALUES (?, ?, ?)",
                (content, remind_at.strftime("%Y-%m-%d %H:%M"),
                 datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            con.commit()
            rid = cur.lastrowid
            con.close()
            last_action[user_id] = {"type": "reminder", "id": rid, "content": content,
                                     "remind_at": remind_at.strftime("%Y-%m-%d %H:%M")}
            reply = (f"⏰ Reminder set!\n*{content}*\n"
                     f"📅 {remind_at.strftime('%A, %b %d at %H:%M')}\n\n"
                     "Say _\"make it 10am\"_ or _\"change to Tuesday\"_ to update.")
            add_to_history(user_id, "user", text)
            add_to_history(user_id, "assistant",
                           f"Set reminder: {content} at {remind_at.strftime('%Y-%m-%d %H:%M')}")
            await update.message.reply_text(reply, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "When should I remind you? Try:\n"
                "• _\"remind me tomorrow\"_\n"
                "• _\"remind me on Friday at 3pm\"_\n"
                "• _\"remind me in 2 hours\"_",
                parse_mode="Markdown")

    # ── Show ──────────────────────────────────────────────────────────────────
    elif intent == "show":
        t = text.lower()
        if any(k in t for k in ["reminder", "appointment", "calendar"]):
            await cmd_reminders(update, ctx)
        elif category == "general":
            await cmd_notes(update, ctx)
        else:
            ctx.args = [category]
            await cmd_notes(update, ctx)

    # ── Chat ──────────────────────────────────────────────────────────────────
    else:
        extra_context = ""
        t = text.lower()
        if any(k in t for k in ["note", "list", "remind", "appointment"] + CATEGORIES):
            con = get_db()
            recent = con.execute(
                "SELECT category, content FROM notes WHERE archived_at IS NULL "
                "ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
            upcoming = con.execute(
                "SELECT content, remind_at FROM reminders WHERE archived_at IS NULL "
                "ORDER BY remind_at ASC LIMIT 5"
            ).fetchall()
            con.close()
            if recent:
                extra_context += "Ted's recent notes:\n" + \
                    "\n".join(f"- [{r['category']}] {r['content']}" for r in recent)
            if upcoming:
                extra_context += "\nUpcoming reminders:\n" + \
                    "\n".join(f"- {r['content']} ({r['remind_at']})" for r in upcoming)

        add_to_history(user_id, "user", text)
        await update.message.reply_text("💭 Thinking...")
        response = ask_llama(user_id, text, extra_context)
        add_to_history(user_id, "assistant", response)
        await update.message.reply_text(response)


# ── Reminder scheduler ────────────────────────────────────────────────────────
_bot_instance = None
_loop = None

def check_reminders():
    if not _bot_instance or not ALLOWED_USER_ID:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    con = get_db()
    due = con.execute(
        "SELECT id, content FROM reminders "
        "WHERE archived_at IS NULL AND fired_at IS NULL AND remind_at <= ?", (now,)
    ).fetchall()
    for row in due:
        con.execute("UPDATE reminders SET fired_at=? WHERE id=?",
                    (datetime.now().strftime("%Y-%m-%d %H:%M"), row['id']))
        import asyncio
        try:
            future = asyncio.run_coroutine_threadsafe(
                _bot_instance.send_message(
                    chat_id=ALLOWED_USER_ID,
                    text=(f"⏰ *Reminder:* {row['content']}\n\n"
                          f"Say _/archive reminder {row['id']}_ when done."),
                    parse_mode="Markdown"
                ), _loop)
            future.result(timeout=10)
        except Exception as e:
            log.error(f"Reminder send failed: {e}")
    con.commit(); con.close()

def run_scheduler():
    schedule.every(1).minutes.do(check_reminders)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global _bot_instance, _loop
    init_db()
    log.info(f"Starting TedBot — model: {OLLAMA_MODEL} @ {OLLAMA_URL}")
    log.info(f"Database: {DB_PATH}")

    import asyncio
    _loop = asyncio.get_event_loop()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    _bot_instance = app.bot

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_start))
    app.add_handler(CommandHandler("notes",      cmd_notes))
    app.add_handler(CommandHandler("reminders",  cmd_reminders))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("find",       cmd_find))
    app.add_handler(CommandHandler("archive",    cmd_archive))
    app.add_handler(CommandHandler("debug",      cmd_debug))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    threading.Thread(target=run_scheduler, daemon=True).start()
    log.info("Bot is running!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()