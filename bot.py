# 🐾 Enhanced Pet Industry News Bot v3.0 — Single Complete Code

```python
#!/usr/bin/env python3

import os
import re
import html
import time
import threading
import sqlite3
import hashlib
import logging
from datetime import datetime, timedelta

import requests
import feedparser
import pytz
import telebot

from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "940928434"))
PORT = int(os.getenv("PORT", 10000))
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
WEBHOOK_MODE = os.getenv("WEBHOOK_MODE", "true").lower() == "true"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_FILE = "newsbot.db"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NEWS CATEGORIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PET_CATEGORIES = {
    "general": "pet industry news trends",
    "veterinary": "veterinary medicine animal health",
    "pet_food": "pet food nutrition industry",
    "pet_tech": "pet technology smart devices IoT",
    "startups": "pet startup funding animal business",
    "pet_care": "pet care grooming wellness tips",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db = get_db()

    db.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        delivery_hour INTEGER DEFAULT 8,
        delivery_minute INTEGER DEFAULT 0,
        is_subscribed INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS seen_stories (
        story_hash TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS news_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        title TEXT,
        status TEXT,
        sent_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    db.commit()
    db.close()


def upsert_user(chat_id, first_name, username):
    db = get_db()

    db.execute(
        '''
        INSERT OR IGNORE INTO users(chat_id, first_name, username)
        VALUES(?,?,?)
        ''',
        (chat_id, first_name, username)
    )

    db.commit()
    db.close()


def get_user(chat_id):
    db = get_db()

    row = db.execute(
        "SELECT * FROM users WHERE chat_id=?",
        (chat_id,)
    ).fetchone()

    db.close()

    return dict(row) if row else None


def get_all_users():
    db = get_db()

    rows = db.execute(
        "SELECT * FROM users WHERE is_subscribed=1"
    ).fetchall()

    db.close()

    return [dict(r) for r in rows]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def clean_text(text):
    if not text:
        return ""

    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', '', text)

    return re.sub(r'\s+', ' ', text).strip()


def escape_md(text):
    chars = r'_[]()~`>#+-=|{}.!'
    return ''.join(f'\{c}' if c in chars else c for c in text)


def story_hash(title, link):
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()


def shorten_url(url):
    try:
        r = requests.get(
            f"https://tinyurl.com/api-create.php?url={url}",
            timeout=10
        )

        if r.status_code == 200:
            return r.text.strip()

    except Exception as e:
        logger.warning(e)

    return url


def smart_summary(text, max_len=120):
    text = clean_text(text)

    if not text:
        return "Latest pet industry update."

    parts = re.split(r'[.!?]', text)

    summary = parts[0].strip()

    if len(summary) > max_len:
        summary = summary[:max_len] + "..."

    return summary

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NEWS FETCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def fetch_pet_news(category=None, num=8):

    query = category if category else " OR ".join(PET_CATEGORIES.values())

    query = query.replace(" ", "+")

    url = (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl=en-IN&gl=IN&ceid=IN:en"
    )

    feed = feedparser.parse(url)

    stories = []
    seen_local = set()

    for entry in feed.entries[:30]:

        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_text(entry.get("summary", ""))
        source = clean_text(entry.get("source", {}).get("title", "Google News"))

        if not title or not link:
            continue

        h = story_hash(title, link)

        if h in seen_local:
            continue

        seen_local.add(h)

        short_link = shorten_url(link)

        stories.append({
            "title": title[:100],
            "summary": smart_summary(summary),
            "source": source[:40],
            "link": short_link,
            "hash": h
        })

        if len(stories) >= num:
            break

    return stories

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MESSAGE FORMATTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NUMBERS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣"]


def build_news_message(stories, fname="Friend"):

    msg = (
        f"🐾 *PET INDUSTRY NEWS* 🐾\n"
        f"👋 Hey *{escape_md(fname)}*\n\n"
    )

    for i, s in enumerate(stories):

        title = escape_md(s['title'])
        summary = escape_md(s['summary'])
        source = escape_md(s['source'])
        link = s['link']

        msg += (
            f"{NUMBERS[i]} *{title}*\n"
            f"📍 {source}\n"
            f"🧠 {summary}\n"
            f"🔗 {link}\n\n"
        )

    msg += "⚙️ /settime • 📰 /news"

    return msg

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bot = telebot.TeleBot(BOT_TOKEN)


def main_menu_kb():

    kb = telebot.types.InlineKeyboardMarkup(row_width=2)

    kb.add(
        telebot.types.InlineKeyboardButton("📰 News", callback_data="news"),
        telebot.types.InlineKeyboardButton("⚙️ Settings", callback_data="settings")
    )

    kb.add(
        telebot.types.InlineKeyboardButton("🐶 General", callback_data="general"),
        telebot.types.InlineKeyboardButton("🩺 Vet", callback_data="vet")
    )

    kb.add(
        telebot.types.InlineKeyboardButton("💻 Tech", callback_data="tech"),
        telebot.types.InlineKeyboardButton("🚀 Startups", callback_data="startup")
    )

    return kb

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=['start'])
def start(message):

    cid = message.chat.id

    upsert_user(
        cid,
        message.chat.first_name,
        message.chat.username
    )

    bot.send_message(
        cid,
        "🐾 *Welcome to Pet Industry News Bot!*\n\nChoose an option below:",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_kb()
    )


@bot.message_handler(commands=['news'])
def news(message):

    cid = message.chat.id

    bot.send_chat_action(cid, 'typing')

    stories = fetch_pet_news()

    msg = build_news_message(
        stories,
        message.chat.first_name or "Friend"
    )

    bot.send_message(
        cid,
        msg,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )


@bot.message_handler(commands=['adminstats'])
def adminstats(message):

    cid = message.chat.id

    if cid != ADMIN_CHAT_ID:
        return

    db = get_db()

    users = db.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    deliveries = db.execute(
        "SELECT COUNT(*) FROM news_log"
    ).fetchone()[0]

    db.close()

    bot.send_message(
        cid,
        (
            f"📊 ADMIN PANEL\n\n"
            f"👥 Users: {users}\n"
            f"📬 Deliveries: {deliveries}\n"
            f"✅ Bot Status: Running"
        )
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALLBACKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda call: True)
def callback(call):

    cid = call.message.chat.id
    fname = call.message.chat.first_name or "Friend"

    bot.answer_callback_query(call.id)

    category = None

    if call.data == "general":
        category = PET_CATEGORIES['general']

    elif call.data == "vet":
        category = PET_CATEGORIES['veterinary']

    elif call.data == "tech":
        category = PET_CATEGORIES['pet_tech']

    elif call.data == "startup":
        category = PET_CATEGORIES['startups']

    elif call.data == "news":
        category = None

    if category is not None or call.data == "news":

        bot.edit_message_text(
            "📡 Fetching latest news...",
            cid,
            call.message.message_id
        )

        stories = fetch_pet_news(category)

        msg = build_news_message(stories, fname)

        bot.send_message(
            cid,
            msg,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCHEDULER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

scheduler = BackgroundScheduler()


def scheduled_news(chat_id):

    user = get_user(chat_id)

    if not user:
        return

    stories = fetch_pet_news()

    msg = build_news_message(
        stories,
        user['first_name'] or 'Friend'
    )

    bot.send_message(
        chat_id,
        msg,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )


def schedule_all_users():

    users = get_all_users()

    for u in users:

        scheduler.add_job(
            scheduled_news,
            CronTrigger(
                hour=u['delivery_hour'],
                minute=u['delivery_minute'],
                timezone=pytz.timezone(DEFAULT_TZ)
            ),
            args=[u['chat_id']],
            id=f"user_{u['chat_id']}",
            replace_existing=True
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLASK APP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__)


@app.route('/')
def home():
    return "🐾 Pet Industry News Bot Running", 200


@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat()
    })


@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():

    if request.headers.get('content-type') == 'application/json':

        json_str = request.get_data().decode('utf-8')

        update = telebot.types.Update.de_json(json_str)

        bot.process_new_updates([update])

        return 'OK', 200

    return 'Invalid', 403

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':

    logger.info('Starting bot...')

    init_db()

    scheduler.start()

    schedule_all_users()

    if WEBHOOK_MODE and RENDER_EXTERNAL_URL:

        webhook_url = f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"

        bot.remove_webhook()

        time.sleep(2)

        bot.set_webhook(url=webhook_url)

        logger.info(f"Webhook set: {webhook_url}")

    else:

        def polling():
            while True:
                try:
                    bot.infinity_polling(timeout=30)
                except Exception as e:
                    logger.error(e)
                    time.sleep(5)

        threading.Thread(target=polling, daemon=True).start()

    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        use_reloader=False
    )
```

# requirements.txt

```txt
pyTelegramBotAPI
Flask
feedparser
pytz
APScheduler
requests
gunicorn
```

# Render Start Command

```bash
gunicorn bot:app
```

# Render Environment Variables

```text
BOT_TOKEN=YOUR_BOT_TOKEN
ADMIN_CHAT_ID=YOUR_CHAT_ID
PORT=10000
DEFAULT_TIMEZONE=Asia/Kolkata
WEBHOOK_MODE=true
RENDER_EXTERNAL_URL=https://your-app.onrender.com
```
