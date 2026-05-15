#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║     🤖 PET INDUSTRY NEWS BOT — v3.0 (Summaries + Tiny URLs)              ║
# ║     • AI-generated summaries for each story                                ║
# ║     • Tiny URLs via is.gd (free, no API key)                              ║
# ║     • Better news fetching with fallback sources                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

import os
import re
import html
import time
import threading
import sqlite3
import hashlib
import logging
import urllib.request
import urllib.parse
import json
from datetime import datetime, timedelta

import feedparser
import pytz
import telebot
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "940928434")
PORT          = int(os.getenv("PORT", 10000))
DEFAULT_TZ    = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
DB_FILE       = "newsbot.db"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
except ValueError:
    pass

# Pet Industry News Sources (multiple for fallback)
PET_QUERIES = [
    "pet industry news trends",
    "pet care grooming wellness",
    "pet technology smart devices",
    "veterinary medicine health",
    "pet food nutrition industry",
    "pet startup funding business",
    "animal welfare rescue",
    "dog cat health tips",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📝 LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id         INTEGER PRIMARY KEY,
            username        TEXT,
            first_name      TEXT,
            last_name       TEXT,
            timezone        TEXT    DEFAULT 'Asia/Kolkata',
            delivery_hour   INTEGER DEFAULT 8,
            delivery_minute INTEGER DEFAULT 0,
            is_active       INTEGER DEFAULT 1,
            is_subscribed   INTEGER DEFAULT 1,
            created_at      TEXT    DEFAULT (datetime('now')),
            updated_at      TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS news_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            story_hash  TEXT,
            title       TEXT,
            source      TEXT,
            status      TEXT    DEFAULT 'sent',
            sent_at     TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS seen_stories (
            story_hash  TEXT PRIMARY KEY,
            title       TEXT,
            link        TEXT,
            pub_date    TEXT,
            first_seen  TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            event_type  TEXT,
            payload     TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_news_log_chat ON news_log(chat_id);
        CREATE INDEX IF NOT EXISTS idx_news_log_hash ON news_log(story_hash);
        CREATE INDEX IF NOT EXISTS idx_seen_hash ON seen_stories(story_hash);
        CREATE INDEX IF NOT EXISTS idx_seen_date ON seen_stories(pub_date);
    """)
    db.commit()
    db.close()
    logger.info("Database initialized")

# ── User CRUD ─────────────────────────────────────────────────────────────────

def upsert_user(chat_id, username=None, first_name=None, last_name=None):
    db = get_db()
    existing = db.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO users (chat_id, username, first_name, last_name) VALUES (?,?,?,?)",
            (chat_id, username, first_name, last_name)
        )
        db.commit()
        logger.info(f"New user: {chat_id}")
    db.close()

def set_user_time(chat_id, hour, minute):
    db = get_db()
    db.execute(
        "UPDATE users SET delivery_hour=?, delivery_minute=?, updated_at=datetime('now') WHERE chat_id=?",
        (hour, minute, chat_id)
    )
    db.commit()
    db.close()

def toggle_subscription(chat_id, subscribed=True):
    db = get_db()
    db.execute(
        "UPDATE users SET is_subscribed=?, updated_at=datetime('now') WHERE chat_id=?",
        (1 if subscribed else 0, chat_id)
    )
    db.commit()
    db.close()

def get_user(chat_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    db.close()
    return dict(row) if row else None

def get_all_subscribed():
    db = get_db()
    rows = db.execute("SELECT * FROM users WHERE is_active=1 AND is_subscribed=1").fetchall()
    db.close()
    return [dict(r) for r in rows]

# ── News Deduplication ────────────────────────────────────────────────────────

def story_hash(title, link):
    content = f"{title.lower().strip()}|{link}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]

def is_story_seen(story_hash_val):
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM seen_stories WHERE story_hash=? AND first_seen > datetime('now', '-7 days')",
        (story_hash_val,)
    ).fetchone()
    db.close()
    return row is not None

def mark_story_seen(story_hash_val, title, link, pub_date=None):
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO seen_stories (story_hash, title, link, pub_date) VALUES (?,?,?,?)",
        (story_hash_val, title, link, pub_date)
    )
    db.commit()
    db.close()

def log_delivery(chat_id, story_hash_val, title, source, status="sent"):
    db = get_db()
    db.execute(
        "INSERT INTO news_log (chat_id, story_hash, title, source, status) VALUES (?,?,?,?,?)",
        (chat_id, story_hash_val, title, source, status)
    )
    db.commit()
    db.close()

def log_event(chat_id, event_type, payload=None):
    db = get_db()
    db.execute(
        "INSERT INTO events (chat_id, event_type, payload) VALUES (?,?,?)",
        (chat_id, event_type, str(payload)[:500])
    )
    db.commit()
    db.close()

def cleanup_old_seen():
    db = get_db()
    db.execute("DELETE FROM seen_stories WHERE first_seen < datetime('now', '-7 days')")
    db.commit()
    db.close()
    logger.info("Cleaned old stories")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔗 URL SHORTENER — is.gd (Free, No API Key)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def shorten_url(long_url):
    """
    Shorten URL using is.gd free API.
    Returns tiny URL or original URL if shortening fails.
    """
    try:
        encoded_url = urllib.parse.quote(long_url, safe='')
        api_url = f"https://is.gd/create.php?format=simple&url={encoded_url}"

        req = urllib.request.Request(
            api_url,
            headers={
                'User-Agent': 'PetNewsBot/1.0',
                'Accept': 'text/plain'
            }
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            short_url = response.read().decode('utf-8').strip()

        if short_url and short_url.startswith('http'):
            logger.info(f"Shortened: {long_url[:50]}... -> {short_url}")
            return short_url
        return long_url
    except Exception as e:
        logger.warning(f"URL shortening failed: {e}")
        return long_url

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🤖 AI SUMMARY GENERATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_summary(title, description, max_sentences=2):
    """
    Generate a meaningful summary from title + description.
    If description is empty/too short, creates an informative context summary.
    """
    title = clean_text(title)
    description = clean_text(description)

    # If description is basically empty or just repeats title, create context summary
    if not description or len(description) < 30 or description.lower() in title.lower() or title.lower() in description.lower():
        return _generate_context_summary(title)

    # Use description only (ignore title to avoid repetition)
    text = description

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    if not sentences or len(sentences) == 0:
        return _generate_context_summary(title)

    # Score sentences for informativeness
    pet_keywords = [
        'pet', 'dog', 'cat', 'animal', 'veterinary', 'vet', 'food', 'health',
        'care', 'startup', 'technology', 'industry', 'market', 'trend',
        'rescue', 'shelter', 'adoption', 'breed', 'medicine', 'treatment',
        'nutrition', 'wellness', 'grooming', 'insurance', 'business',
        'investment', 'funding', 'research', 'study', 'survey', 'report',
        'growth', 'revenue', 'sales', 'consumer', 'owner', 'companion',
        'pharmaceutical', 'vaccine', 'therapy', 'surgery', 'diagnosis',
        'organic', 'natural', 'premium', 'luxury', 'subscription',
        'e-commerce', 'retail', 'clinic', 'hospital', 'service',
        'regulation', 'policy', 'legislation', 'welfare', 'rights',
        'university', 'college', 'researchers', 'scientists', 'professor',
        'facility', 'unit', 'center', 'program', 'initiative'
    ]

    def score_sentence(sent):
        sent_lower = sent.lower()
        score = 0
        # Keyword relevance
        for kw in pet_keywords:
            if kw in sent_lower:
                score += 1
        # Prefer sentences with numbers/statistics
        if re.search(r'\d+%', sent):
            score += 3
        if re.search(r'\$\d+|\d+ million|\d+ billion|\d+ thousand', sent):
            score += 3
        # Prefer informative over generic
        if any(word in sent_lower for word in ['because', 'according', 'found', 'shows', 'reveals', 'discovered']):
            score += 2
        # Penalize very short or generic sentences
        if len(sent) < 40:
            score -= 2
        if len(sent) > 300:
            score -= 1
        # Penalize sentences that are too similar to title
        title_words = set(title.lower().split())
        sent_words = set(sent.lower().split())
        if len(title_words) > 0:
            overlap = len(title_words & sent_words) / len(title_words)
            if overlap > 0.7:
                score -= 5  # Heavy penalty for title repetition
        return score

    scored = [(s, score_sentence(s)) for s in sentences]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Pick best sentence(s)
    selected = []
    for s, score in scored:
        if len(selected) >= max_sentences:
            break
        if score < -2:  # Skip very low quality sentences
            continue
        # Check not too similar to already selected
        is_duplicate = False
        for existing in selected:
            words_s = set(s.lower().split())
            words_e = set(existing.lower().split())
            if len(words_s) > 0 and len(words_e) > 0:
                overlap = len(words_s & words_e) / min(len(words_s), len(words_e))
                if overlap > 0.5:
                    is_duplicate = True
                    break
        if not is_duplicate:
            selected.append(s)

    if not selected:
        return _generate_context_summary(title)

    # Sort back by original order
    selected.sort(key=lambda x: sentences.index(x))

    summary = ' '.join(selected)
    summary = re.sub(r'\s+', ' ', summary).strip()

    if len(summary) > 280:
        summary = summary[:277] + "..."

    return summary


def _generate_context_summary(title):
    """
    When no description is available, generate an informative context summary
    based on keywords extracted from the title.
    """
    title_lower = title.lower()

    # Extract key entities from title
    context_parts = []

    # Check for specific topics and add context
    if any(word in title_lower for word in ['veterinary', 'vet', 'medicine', 'hospital', 'clinic']):
        context_parts.append("This development could impact veterinary care standards and animal health outcomes.")

    if any(word in title_lower for word in ['food', 'nutrition', 'diet', 'feed']):
        context_parts.append("Pet nutrition trends directly affect millions of pet owners' purchasing decisions.")

    if any(word in title_lower for word in ['startup', 'funding', 'investment', 'million', 'billion']):
        context_parts.append("The pet industry continues to attract significant investor interest and capital.")

    if any(word in title_lower for word in ['technology', 'tech', 'app', 'ai', 'digital', 'smart']):
        context_parts.append("Pet technology innovations are transforming how owners care for their animals.")

    if any(word in title_lower for word in ['research', 'study', 'university', 'scientists', 'professor']):
        context_parts.append("Academic research in this area contributes to advancing pet health knowledge.")

    if any(word in title_lower for word in ['rescue', 'shelter', 'adoption', 'welfare']):
        context_parts.append("Animal welfare developments affect policy and rescue operations nationwide.")

    if any(word in title_lower for word in ['trend', 'market', 'growth', 'industry']):
        context_parts.append("Market trends in the pet sector reflect changing consumer preferences and opportunities.")

    if any(word in title_lower for word in ['dog', 'puppy', 'canine']):
        context_parts.append("Dog-related news remains the largest segment of the pet industry market.")

    if any(word in title_lower for word in ['cat', 'kitten', 'feline']):
        context_parts.append("Cat care innovations continue to drive growth in feline health and wellness products.")

    if not context_parts:
        context_parts.append("This story highlights important developments in the pet industry landscape.")

    # Pick the most relevant 1-2 context sentences
    summary = ' '.join(context_parts[:2])

    return summary

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 NEWS FETCHER — Multi-Source + Fresh Filter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clean_text(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_pub_date(date_str):
    if not date_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%d %b %Y %H:%M:%S %z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            continue
    return None

def is_fresh_story(pub_date, max_age_hours=72):
    """Check if story is within freshness window (default 3 days for better coverage)."""
    if not pub_date:
        return True
    now = datetime.now(pytz.UTC)
    if pub_date.tzinfo is None:
        pub_date = pytz.UTC.localize(pub_date)
    age = now - pub_date
    return age <= timedelta(hours=max_age_hours)

def fetch_pet_news(num=8, max_age_hours=72):
    """
    Fetch pet industry news from multiple Google News RSS queries.
    Returns deduplicated, fresh stories with AI summaries and tiny URLs.
    """
    all_stories = []
    seen_hashes = set()
    skipped_old = 0
    skipped_dup = 0

    # Try multiple queries for better coverage
    for query in PET_QUERIES[:4]:  # Use first 4 queries
        if len(all_stories) >= num * 2:
            break

        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

        try:
            logger.info(f"Fetching: {query}")
            feed = feedparser.parse(url)

            for entry in feed.entries[:15]:
                title   = clean_text(entry.get("title", ""))
                summary_raw = clean_text(entry.get("summary", ""))
                link    = entry.get("link", "")
                source  = clean_text(entry.get("source", {}).get("title", "Google News"))
                pub_str = entry.get("published", "")

                if not title or not link:
                    continue

                # Extract source from title if present
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title, source = parts[0].strip(), parts[1].strip()

                pub_date = parse_pub_date(pub_str)

                # Freshness filter (3 days for better coverage)
                if not is_fresh_story(pub_date, max_age_hours):
                    skipped_old += 1
                    continue

                h = story_hash(title, link)

                if h in seen_hashes or is_story_seen(h):
                    skipped_dup += 1
                    continue

                seen_hashes.add(h)
                mark_story_seen(h, title, link, pub_str)

                # Generate AI summary
                ai_summary = generate_summary(title, summary_raw)

                # Shorten URL
                tiny_link = shorten_url(link)

                all_stories.append({
                    "title":     title[:100],
                    "summary":   ai_summary,
                    "link":      link,
                    "tiny_link": tiny_link,
                    "source":    source[:40],
                    "date":      pub_str[:16] if pub_str else "",
                    "hash":      h,
                })

        except Exception as e:
            logger.warning(f"Fetch error for '{query}': {e}")
            continue

    # Sort by freshness (newest first) and limit
    all_stories.sort(key=lambda x: x["date"], reverse=True)
    result = all_stories[:num]

    logger.info(f"Fetched {len(result)} stories (skipped {skipped_old} old, {skipped_dup} dupes)")
    return result

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ✨ MESSAGE FORMATTING — HTML Mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NUMBERS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

def build_news_message(stories, fname="Friend", is_scheduled=False):
    now = datetime.now(pytz.timezone(DEFAULT_TZ))
    today = now.strftime("%B %d, %Y")

    header = (
        f"🐾 <b>PET INDUSTRY DAILY</b> 🐾\n"
        f"📅 {today}\n"
        f"👋 Hey <b>{fname}</b>!\n"
        f"🤖 Curated by AI News Bot\n"
        f"{'━' * 26}\n\n"
    )

    body = ""
    for i, s in enumerate(stories):
        num = NUMBERS[i] if i < len(NUMBERS) else f"{i+1}."
        title = s["title"]
        source = s["source"]
        summary = s["summary"]
        tiny = s["tiny_link"]

        body += (
            f"{num} <b>{title}</b>\n"
            f"📍 {source}"
        )
        if s["date"]:
            body += f" · <i>{s['date'][:10]}</i>"
        body += "\n"
        if summary:
            body += f"📝 <i>{summary}</i>\n"
        body += f'🔗 <a href="{tiny}">Read more</a>\n\n'

    footer = (
        f"{'━' * 26}\n"
        f"⚙️ /settime · 📰 /news · ❓ /help\n"
    )
    if is_scheduled:
        footer += f"⏰ <i>Your daily briefing</i> 🌅\n"
    else:
        footer += f"⏰ <i>On-demand briefing</i> 📡\n"

    return header + body + footer

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🤖 TELEGRAM BOT SETUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

try:
    me = bot.get_me()
    logger.info(f"Connected to @{me.username}")
except Exception as e:
    logger.error(f"Bot connection failed: {e}")
    raise

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎛️ SCROLLABLE TIME PICKER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

time_picker_sessions = {}

def time_picker_kb(hour, minute, chat_id):
    markup = telebot.types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        telebot.types.InlineKeyboardButton("⬇️", callback_data=f"tp:h:down:{chat_id}"),
        telebot.types.InlineKeyboardButton(f"🕐 {hour:02d}", callback_data="tp:none"),
        telebot.types.InlineKeyboardButton("⬆️", callback_data=f"tp:h:up:{chat_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("⬇️", callback_data=f"tp:m:down:{chat_id}"),
        telebot.types.InlineKeyboardButton(f"🕑 {minute:02d}", callback_data="tp:none"),
        telebot.types.InlineKeyboardButton("⬆️", callback_data=f"tp:m:up:{chat_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Confirm Time", callback_data=f"tp:confirm:{chat_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🔙 Cancel", callback_data="tp:cancel"),
    )
    return markup

def show_time_picker(chat_id, message_id=None, initial_hour=8, initial_minute=0):
    time_picker_sessions[chat_id] = {"hour": initial_hour, "minute": initial_minute}
    text = (
        f"⏰ <b>Set Your Delivery Time</b>\n\n"
        f"Use the arrows to scroll:\n"
        f"• Hour: 00-23\n"
        f"• Minute: 00-59\n\n"
        f"<i>Current: {initial_hour:02d}:{initial_minute:02d}</i>"
    )
    kb = time_picker_kb(initial_hour, initial_minute, chat_id)
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML", reply_markup=kb)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

def update_time_picker(chat_id, message_id):
    session = time_picker_sessions.get(chat_id, {"hour": 8, "minute": 0})
    h, m = session["hour"], session["minute"]
    text = (
        f"⏰ <b>Set Your Delivery Time</b>\n\n"
        f"Use arrows to scroll:\n"
        f"• Hour: 00-23\n"
        f"• Minute: 00-59\n\n"
        f"<i>Current: {h:02d}:{m:02d}</i>"
    )
    kb = time_picker_kb(h, m, chat_id)
    bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML", reply_markup=kb)

# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_menu_kb():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📰 Get News Now", callback_data="get_news"),
        telebot.types.InlineKeyboardButton("⏰ Set Time", callback_data="set_time_menu"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("📊 My Settings", callback_data="my_settings"),
        telebot.types.InlineKeyboardButton("❓ Help", callback_data="show_help"),
    )
    return markup

def settings_kb():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("⏰ Change Time", callback_data="set_time_menu"),
        telebot.types.InlineKeyboardButton("🌍 Timezone", callback_data="set_tz_menu"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🔕 Pause", callback_data="unsubscribe"),
        telebot.types.InlineKeyboardButton("🔔 Resume", callback_data="resubscribe"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu"),
    )
    return markup

# ── Deliver News ──────────────────────────────────────────────────────────────

def deliver_news(chat_id, stories=None, fname="Friend", is_scheduled=False):
    try:
        if not stories:
            stories = fetch_pet_news(num=8, max_age_hours=72)

        if not stories:
            # Fallback: fetch without freshness filter if nothing found
            logger.warning("No fresh news, trying broader search...")
            stories = fetch_pet_news(num=8, max_age_hours=168)  # 7 days fallback

        if not stories:
            bot.send_message(
                chat_id,
                "📰 <b>Pet Industry News</b>\n\n"
                "We're gathering the latest stories for you. "
                "Please try /news again in a few minutes! 🐾",
                parse_mode="HTML"
            )
            return 0

        msg = build_news_message(stories, fname, is_scheduled)
        bot.send_message(chat_id, msg, disable_web_page_preview=True)

        for s in stories:
            log_delivery(chat_id, s["hash"], s["title"], s["source"], "sent")

        return len(stories)

    except Exception as e:
        logger.error(f"Delivery error to {chat_id}: {e}")
        log_delivery(chat_id, None, None, None, f"failed: {e}")
        try:
            bot.send_message(
                chat_id,
                "⚠️ <b>Oops!</b> Something went wrong.<br>Please try /news again.",
                parse_mode="HTML"
            )
        except:
            pass
        return 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📩 COMMAND HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=["start"])
def cmd_start(message):
    cid   = message.chat.id
    fname = message.chat.first_name or "Friend"
    uname = message.chat.username
    lname = message.chat.last_name

    upsert_user(cid, uname, fname, lname)
    log_event(cid, "start")

    welcome = (
        f"🐾 <b>Welcome to Pet Industry News Bot!</b> 🐾\n\n"
        f"👋 Hey <b>{fname}</b>!\n\n"
        f"📰 I deliver <b>AI-curated pet industry news</b> daily:\n"
        f"   • Pet care & wellness\n"
        f"   • Veterinary updates\n"
        f"   • Pet tech & startups\n"
        f"   • Pet food industry\n"
        f"   • Animal trends\n\n"
        f"✨ <b>Features:</b>\n"
        f"   • AI-generated summaries\n"
        f"   • Tiny URLs for easy sharing\n"
        f"   • Personalized delivery time\n\n"
        f"⏰ <b>Default delivery:</b> 8:00 AM IST\n"
        f"⚙️ Tap below to get started!\n\n"
        f"{'━' * 26}"
    )
    bot.send_message(cid, welcome, parse_mode="HTML", reply_markup=main_menu_kb())

@bot.message_handler(commands=["help"])
def cmd_help(message):
    cid = message.chat.id
    help_text = (
        f"🐾 <b>Pet News Bot — Help</b> 🐾\n"
        f"{'━' * 26}\n\n"
        f"📰 <b>/news</b> — Get pet news now (with AI summaries!)\n"
        f"⏰ <b>/settime</b> — Scrollable time picker\n"
        f"🕐 <b>/mytime</b> — Check your settings\n"
        f"🔕 <b>/stoptime</b> — Pause delivery\n"
        f"🔔 <b>/resumetime</b> — Resume delivery\n"
        f"⚙️ <b>/settings</b> — Preferences menu\n"
        f"📊 <b>/mystats</b> — Your usage stats\n\n"
        f"💡 <b>Each story includes:</b>\n"
        f"   • AI-generated summary\n"
        f"   • Tiny URL for sharing\n"
        f"   • Fresh (last 3 days) content\n\n"
        f"🤖 <i>Made with ❤️ for pet lovers</i>"
    )
    bot.send_message(cid, help_text, parse_mode="HTML", reply_markup=main_menu_kb())

@bot.message_handler(commands=["news"])
def cmd_news(message):
    cid   = message.chat.id
    fname = message.chat.first_name or "Friend"
    user  = get_user(cid)
    if not user:
        cmd_start(message)
        return

    log_event(cid, "news_on_demand")
    bot.send_chat_action(cid, "typing")

    threading.Thread(
        target=deliver_news,
        args=(cid, None, fname, False),
        daemon=True
    ).start()

@bot.message_handler(commands=["settime"])
def cmd_settime(message):
    cid = message.chat.id
    user = get_user(cid)
    if not user:
        cmd_start(message)
        return
    current_h = user.get("delivery_hour", 8)
    current_m = user.get("delivery_minute", 0)
    show_time_picker(cid, initial_hour=current_h, initial_minute=current_m)

@bot.message_handler(commands=["mytime"])
def cmd_mytime(message):
    cid = message.chat.id
    user = get_user(cid)
    if not user:
        cmd_start(message)
        return
    h = user.get("delivery_hour", 8)
    m = user.get("delivery_minute", 0)
    tz = user.get("timezone", DEFAULT_TZ)
    sub = "✅ Active" if user.get("is_subscribed") else "🔕 Paused"
    bot.send_message(
        cid,
        f"⏰ <b>Your Delivery Settings</b>\n"
        f"{'━' * 26}\n\n"
        f"🕐 Time: <b>{h:02d}:{m:02d}</b>\n"
        f"🌍 Timezone: <b>{tz}</b>\n"
        f"📬 Status: <b>{sub}</b>\n\n"
        f"⚙️ Use /settime to change",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["stoptime", "unsubscribe"])
def cmd_stop(message):
    cid = message.chat.id
    toggle_subscription(cid, False)
    log_event(cid, "unsubscribe")
    bot.send_message(
        cid,
        f"🔕 <b>Daily delivery paused.</b>\n\n"
        f"You won't receive automatic briefings.\n"
        f"Use /resumetime to reactivate!\n\n"
        f"📰 You can still use /news anytime.",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["resumetime", "resubscribe"])
def cmd_resume(message):
    cid = message.chat.id
    toggle_subscription(cid, True)
    log_event(cid, "resubscribe")
    user = get_user(cid)
    h = user.get("delivery_hour", 8)
    m = user.get("delivery_minute", 0)
    schedule_user_job(cid)
    bot.send_message(
        cid,
        f"🔔 <b>Daily delivery resumed!</b>\n\n"
        f"⏰ Your briefings will arrive at <b>{h:02d}:{m:02d}</b> daily.",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    cid = message.chat.id
    user = get_user(cid)
    if not user:
        cmd_start(message)
        return
    bot.send_message(
        cid,
        f"⚙️ <b>Your Settings</b>\n"
        f"{'━' * 26}",
        parse_mode="HTML",
        reply_markup=settings_kb()
    )

@bot.message_handler(commands=["mystats"])
def cmd_mystats(message):
    cid = message.chat.id
    user = get_user(cid)
    if not user:
        cmd_start(message)
        return
    db = get_db()
    deliveries = db.execute(
        "SELECT COUNT(*) FROM news_log WHERE chat_id=? AND status='sent'", (cid,)
    ).fetchone()[0]
    last = db.execute(
        "SELECT MAX(sent_at) FROM news_log WHERE chat_id=?", (cid,)
    ).fetchone()[0]
    db.close()
    joined = str(user.get("created_at", ""))[:10]
    h = user.get("delivery_hour", 8)
    m = user.get("delivery_minute", 0)
    bot.send_message(
        cid,
        f"📊 <b>Your Stats</b>\n"
        f"{'━' * 26}\n\n"
        f"👤 Name: <b>{user.get('first_name') or 'N/A'}</b>\n"
        f"📬 Briefings received: <b>{deliveries}</b>\n"
        f"📅 Member since: <b>{joined}</b>\n"
        f"⏰ Delivery time: <b>{h:02d}:{m:02d}</b>\n"
        f"🕒 Last delivery: <b>{str(last)[:16] if last else 'Never'}</b>\n\n"
        f"⚙️ /settings · 📰 /news",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["adminstats"])
def cmd_adminstats(message):
    cid = message.chat.id
    if str(cid) != str(ADMIN_CHAT_ID):
        bot.send_message(cid, "⛔ <b>Admin only.</b>", parse_mode="HTML")
        return
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM users WHERE is_subscribed=1").fetchone()[0]
    deliveries = db.execute("SELECT COUNT(*) FROM news_log WHERE status='sent'").fetchone()[0]
    today = db.execute(
        "SELECT COUNT(*) FROM news_log WHERE sent_at > datetime('now', '-1 day') AND status='sent'"
    ).fetchone()[0]
    db.close()
    bot.send_message(
        cid,
        f"🛠 <b>Admin Dashboard</b>\n"
        f"{'━' * 26}\n\n"
        f"👥 Total users: <b>{total}</b>\n"
        f"✅ Subscribed: <b>{active}</b>\n"
        f"📬 Total deliveries: <b>{deliveries}</b>\n"
        f"📅 Today: <b>{today}</b>\n\n"
        f"🤖 <i>Bot running smoothly</i>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: True)
def fallback(message):
    cid = message.chat.id
    user = get_user(cid)
    if not user:
        cmd_start(message)
        return
    bot.send_message(
        cid,
        f"🤔 <b>Didn't understand that.</b>\n\n"
        f"Try:\n"
        f"📰 /news — Get news\n"
        f"⏰ /settime — Set time\n"
        f"❓ /help — All commands",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔘 CALLBACK HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    cid   = call.message.chat.id
    fname = call.message.chat.first_name or "Friend"
    data  = call.data

    bot.answer_callback_query(call.id)

    if data.startswith("tp:"):
        parts = data.split(":")
        action = parts[1]

        if action == "none":
            return

        elif action == "cancel":
            if cid in time_picker_sessions:
                del time_picker_sessions[cid]
            bot.edit_message_text(
                f"⏰ <b>Time selection cancelled.</b>\n\n"
                f"Your previous settings remain unchanged.",
                cid, call.message.message_id,
                parse_mode="HTML",
                reply_markup=main_menu_kb()
            )
            return

        elif action == "confirm":
            session = time_picker_sessions.get(cid)
            if not session:
                bot.edit_message_text(
                    "⚠️ Session expired. Please run /settime again.",
                    cid, call.message.message_id,
                    parse_mode="HTML"
                )
                return
            hour = session["hour"]
            minute = session["minute"]
            set_user_time(cid, hour, minute)
            log_event(cid, "settime_picker", f"{hour:02d}:{minute:02d}")
            schedule_user_job(cid)
            del time_picker_sessions[cid]
            bot.edit_message_text(
                f"✅ <b>Time saved!</b>\n\n"
                f"⏰ Daily delivery: <b>{hour:02d}:{minute:02d}</b>\n"
                f"🐾 You'll receive fresh pet industry news daily!\n\n"
                f"📰 Want a preview now?",
                cid, call.message.message_id,
                parse_mode="HTML",
                reply_markup=main_menu_kb()
            )
            return

        elif action in ("h", "m"):
            direction = parts[2]
            target_cid = int(parts[3])
            if target_cid != cid:
                return
            session = time_picker_sessions.get(cid, {"hour": 8, "minute": 0})
            if action == "h":
                if direction == "up":
                    session["hour"] = (session["hour"] + 1) % 24
                else:
                    session["hour"] = (session["hour"] - 1) % 24
            else:
                if direction == "up":
                    session["minute"] = (session["minute"] + 1) % 60
                else:
                    session["minute"] = (session["minute"] - 1) % 60
            time_picker_sessions[cid] = session
            update_time_picker(cid, call.message.message_id)
            return

    if data == "get_news":
        bot.edit_message_text(
            "📡 <b>Fetching latest pet news…</b>\n"
            "<i>Generating AI summaries & tiny URLs</i> ⏳",
            cid, call.message.message_id,
            parse_mode="HTML"
        )
        threading.Thread(
            target=deliver_news,
            args=(cid, None, fname, False),
            daemon=True
        ).start()

    elif data == "set_time_menu":
        user = get_user(cid)
        current_h = user.get("delivery_hour", 8) if user else 8
        current_m = user.get("delivery_minute", 0) if user else 0
        show_time_picker(cid, message_id=call.message.message_id, 
                        initial_hour=current_h, initial_minute=current_m)

    elif data == "my_settings":
        user = get_user(cid)
        h = user.get("delivery_hour", 8) if user else 8
        m = user.get("delivery_minute", 0) if user else 0
        tz = user.get("timezone", DEFAULT_TZ) if user else DEFAULT_TZ
        sub = "✅ Active" if (user and user.get("is_subscribed")) else "🔕 Paused"
        bot.edit_message_text(
            f"⚙️ <b>Your Settings</b>\n"
            f"{'━' * 26}\n\n"
            f"🕐 Time: <b>{h:02d}:{m:02d}</b>\n"
            f"🌍 Timezone: <b>{tz}</b>\n"
            f"📬 Status: <b>{sub}</b>\n\n"
            f"What would you like to change?",
            cid, call.message.message_id,
            parse_mode="HTML",
            reply_markup=settings_kb()
        )

    elif data == "show_help":
        bot.edit_message_text(
            f"🐾 <b>Pet News Bot — Help</b> 🐾\n"
            f"{'━' * 26}\n\n"
            f"📰 <b>/news</b> — Get news now\n"
            f"⏰ <b>/settime</b> — Scrollable time picker\n"
            f"🕐 <b>/mytime</b> — Check settings\n"
            f"🔕 <b>/stoptime</b> — Pause delivery\n"
            f"🔔 <b>/resumetime</b> — Resume delivery\n"
            f"⚙️ <b>/settings</b> — Preferences\n"
            f"📊 <b>/mystats</b> — Your stats\n\n"
            f"💡 Each story has AI summary + tiny URL\n"
            f"🕒 Fresh content from last 3 days",
            cid, call.message.message_id,
            parse_mode="HTML",
            reply_markup=main_menu_kb()
        )

    elif data == "main_menu":
        bot.edit_message_text(
            f"🐾 <b>Pet Industry News Bot</b> 🐾\n\n"
            f"👋 Welcome back, <b>{fname}</b>!\n\n"
            f"What would you like to do?",
            cid, call.message.message_id,
            parse_mode="HTML",
            reply_markup=main_menu_kb()
        )

    elif data == "unsubscribe":
        toggle_subscription(cid, False)
        bot.edit_message_text(
            f"🔕 <b>Unsubscribed.</b>\n\n"
            f"Daily delivery is paused.\n"
            f"Use /resumetime anytime to reactivate.",
            cid, call.message.message_id,
            parse_mode="HTML",
            reply_markup=settings_kb()
        )

    elif data == "resubscribe":
        toggle_subscription(cid, True)
        user = get_user(cid)
        h = user.get("delivery_hour", 8) if user else 8
        m = user.get("delivery_minute", 0) if user else 0
        schedule_user_job(cid)
        bot.edit_message_text(
            f"🔔 <b>Resubscribed!</b>\n\n"
            f"⏰ Daily delivery at <b>{h:02d}:{m:02d}</b>.",
            cid, call.message.message_id,
            parse_mode="HTML",
            reply_markup=settings_kb()
        )

    elif data == "set_tz_menu":
        bot.edit_message_text(
            f"🌍 <b>Timezone Settings</b>\n\n"
            f"Currently only <b>Asia/Kolkata</b> is supported.\n"
            f"More timezones coming soon! 🌐",
            cid, call.message.message_id,
            parse_mode="HTML",
            reply_markup=settings_kb()
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⏰ SCHEDULING SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

scheduler = BackgroundScheduler()
scheduled_jobs = {}

def send_scheduled_news(chat_id):
    user = get_user(chat_id)
    if not user or not user.get("is_subscribed"):
        return
    fname = user.get("first_name") or "Friend"
    logger.info(f"Scheduled delivery to {chat_id} at {datetime.now()}")
    deliver_news(chat_id, None, fname, is_scheduled=True)

def schedule_user_job(chat_id):
    user = get_user(chat_id)
    if not user or not user.get("is_subscribed"):
        return
    job_id = f"user_{chat_id}"
    if job_id in scheduled_jobs:
        try:
            scheduler.remove_job(job_id)
        except:
            pass
    hour = user.get("delivery_hour", 8)
    minute = user.get("delivery_minute", 0)
    tz_name = user.get("timezone", DEFAULT_TZ)
    try:
        tz = pytz.timezone(tz_name)
    except:
        tz = pytz.timezone(DEFAULT_TZ)
    scheduler.add_job(
        send_scheduled_news,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id=job_id,
        args=[chat_id],
        replace_existing=True,
        misfire_grace_time=3600
    )
    scheduled_jobs[job_id] = True
    logger.info(f"Scheduled user {chat_id} at {hour:02d}:{minute:02d} {tz_name}")

def schedule_all_users():
    users = get_all_subscribed()
    for u in users:
        schedule_user_job(u["chat_id"])
    logger.info(f"Scheduled {len(users)} users")

def daily_cleanup():
    cleanup_old_seen()
    logger.info("Daily cleanup completed")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 FLASK KEEP-ALIVE SERVER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__)

@app.route("/")
def home():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM users WHERE is_subscribed=1").fetchone()[0]
    deliveries = db.execute("SELECT COUNT(*) FROM news_log WHERE status='sent'").fetchone()[0]
    db.close()
    return (
        f"<html><head><title>Pet News Bot</title></head><body>"
        f"<h1>🐾 Pet Industry News Bot v3.0</h1>"
        f"<p><b>Status:</b> ✅ Running</p>"
        f"<p><b>Users:</b> {total} total | {active} subscribed</p>"
        f"<p><b>Deliveries:</b> {deliveries}</p>"
        f"<p><b>Features:</b> AI Summaries + Tiny URLs + 3-Day Fresh Filter</p>"
        f"<hr><small>Ping this URL every 5 min to keep alive on Render Free Tier</small>"
        f"</body></html>"
    ), 200

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "bot": "running",
        "scheduler": "active" if scheduler.running else "stopped",
        "features": ["ai_summaries", "tiny_urls", "3_day_fresh"],
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route("/ping")
def ping():
    return "pong", 200

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 MAIN ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    logger.info("━" * 50)
    logger.info("Pet Industry News Bot v3.0 — Starting up...")
    logger.info("Features: AI Summaries + Tiny URLs + Scrollable Time Picker")
    logger.info("━" * 50)

    init_db()
    scheduler.start()
    logger.info("Scheduler started")

    scheduler.add_job(
        daily_cleanup,
        CronTrigger(hour=3, minute=0, timezone=pytz.timezone(DEFAULT_TZ)),
        id="daily_cleanup",
        replace_existing=True
    )

    schedule_all_users()

    try:
        bot.send_message(
            ADMIN_CHAT_ID,
            f"🟢 <b>Pet News Bot v3.0 is LIVE!</b> 🐾\n\n"
            f"✅ AI-generated summaries\n"
            f"✅ Tiny URLs (is.gd)\n"
            f"✅ Scrollable time picker\n"
            f"✅ 3-day fresh news filter\n\n"
            f"<i>Use /adminstats for dashboard</i>",
            parse_mode="HTML"
        )
        logger.info("Admin notified")
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")

    def run_bot():
        while True:
            try:
                logger.info("Starting Telegram polling...")
                bot.infinity_polling(timeout=30, long_polling_timeout=20)
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(5)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("Bot polling started")

    logger.info(f"Binding to port {PORT} for Render...")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
