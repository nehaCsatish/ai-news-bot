# ╔══════════════════════════════════════════════════════════════════╗
# ║                🤖 AI NEWS BOT — STABLE FINAL VERSION           ║
# ║         Telegram + Render + Flask + Google News RSS           ║
# ╚══════════════════════════════════════════════════════════════════╝

import os
import re
import html
import time
import threading
import sqlite3
import feedparser
import pytz
import telebot

from flask import Flask
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable missing!")

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "940928434"))

PORT = int(os.getenv("PORT", 10000))

TIMEZONE = "Asia/Kolkata"

DAILY_HOUR = 8
DAILY_MINUTE = 0

DB_FILE = "newsbot.db"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 NEWS CATEGORIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NICHES = {
    "pets": {
        "label": "🐾 Pets & Animals",
        "query": "pets animals wildlife rescue veterinary latest news"
    },
    "ai": {
        "label": "🤖 AI & Technology",
        "query": "artificial intelligence machine learning latest news"
    },

    "finance": {
        "label": "💰 Finance & Markets",
        "query": "finance stock market latest news"
    },

    "sports": {
        "label": "⚽ Sports",
        "query": "sports football cricket latest news"
    },

    "health": {
        "label": "🏥 Health & Medicine",
        "query": "health medicine latest news"
    },

    "science": {
        "label": "🔬 Science & Space",
        "query": "science NASA space latest news"
    },

    "business": {
        "label": "📈 Business & Startups",
        "query": "business startup latest news"
    },

    "world": {
        "label": "🌍 World News",
        "query": "world breaking latest news"
    },

    "india": {
        "label": "🇮🇳 India News",
        "query": "India latest breaking news"
    },

    "entertainment": {
        "label": "🎬 Entertainment",
        "query": "movies Netflix celebrity latest news"
    },
    "environment": {
        "label": "🌿 Environment",
        "query": "climate change environment renewable energy latest news"
    },

    "cybersecurity": {
        "label": "🔒 Cybersecurity",
        "query": "cybersecurity hacking latest news"
    },
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    db = get_db()

    db.executescript("""

    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        niche TEXT DEFAULT 'world',
        is_active INTEGER DEFAULT 1,
        joined_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS news_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        niche TEXT,
        story_count INTEGER,
        status TEXT,
        sent_at TEXT DEFAULT (datetime('now'))
    );

    """)

    db.commit()

    db.close()


def upsert_user(chat_id, username=None, first_name=None, last_name=None):

    db = get_db()

    existing = db.execute(
        "SELECT * FROM users WHERE chat_id=?",
        (chat_id,)
    ).fetchone()

    if not existing:

        db.execute(
            """
            INSERT INTO users
            (chat_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            """,
            (
                chat_id,
                username,
                first_name,
                last_name
            )
        )

        db.commit()

    db.close()


def set_niche(chat_id, niche):

    db = get_db()

    db.execute(
        "UPDATE users SET niche=? WHERE chat_id=?",
        (niche, chat_id)
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


def get_all_active():

    db = get_db()

    rows = db.execute(
        "SELECT * FROM users WHERE is_active=1"
    ).fetchall()

    db.close()

    return [dict(r) for r in rows]


def log_delivery(chat_id, niche, count, status="sent"):

    db = get_db()

    db.execute(
        """
        INSERT INTO news_log
        (chat_id, niche, story_count, status)
        VALUES (?, ?, ?, ?)
        """,
        (
            chat_id,
            niche,
            count,
            status
        )
    )

    db.commit()

    db.close()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🧹 CLEANERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clean(text):

    if not text:
        return ""

    text = html.unescape(text)

    text = re.sub(r"<[^>]+>", "", text)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def escape_markdown(text):

    if not text:
        return ""

    escape_chars = r'\_*[]()~`>#+-=|{}.!'

    return ''.join(
        '\\' + c if c in escape_chars else c
        for c in text
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 NEWS FETCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_news(niche="world", num=10):

    cfg = NICHES.get(
        niche,
        NICHES["world"]
    )

    query = cfg["query"].replace(" ", "+")

    url = (
        "https://news.google.com/rss/search?"
        f"q={query}"
        "&hl=en-IN"
        "&gl=IN"
        "&ceid=IN:en"
    )

    try:

        print(f"Fetching news: {url}")

        feed = feedparser.parse(url)

        stories = []

        seen = set()

        for e in feed.entries[:num * 3]:

            title = clean(
                e.get("title", "")
            )

            summary = clean(
                e.get("summary", "")
            )[:220]

            source = clean(
                e.get("source", {}).get(
                    "title",
                    "Google News"
                )
            )

            link = e.get("link", "")

            if not title:
                continue

            normalized = re.sub(
                r"[^a-zA-Z0-9]",
                "",
                title.lower()
            )

            if normalized in seen:
                continue

            seen.add(normalized)

            title = escape_markdown(title)

            summary = escape_markdown(summary)

            source = escape_markdown(source)

            stories.append({
                "title": title,
                "summary": summary,
                "source": source,
                "link": link,
            })

            if len(stories) >= num:
                break

        print(f"Fetched {len(stories)} stories")

        return stories

    except Exception as e:

        print(f"[FETCH ERROR] {e}")

        return []

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🤖 TELEGRAM BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="Markdown"
)

try:

    me = bot.get_me()

    print(f"Connected to bot: @{me.username}")

except Exception as e:

    print(f"[BOT ERROR] {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔘 KEYBOARD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def niche_keyboard():

    markup = telebot.types.InlineKeyboardMarkup(
        row_width=2
    )

    buttons = []

    for key, cfg in NICHES.items():

        buttons.append(

            telebot.types.InlineKeyboardButton(
                cfg["label"],
                callback_data=f"niche:{key}"
            )

        )

    for i in range(0, len(buttons), 2):

        markup.add(*buttons[i:i+2])

    return markup

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 FORMAT NEWS MESSAGE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NUMS = [
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
    "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"
]

def build_message(niche, stories):

    cfg = NICHES.get(
        niche,
        NICHES["world"]
    )

    today = datetime.now(
        pytz.timezone(TIMEZONE)
    ).strftime("%B %d, %Y")

    msg = (
        f"📰 *{cfg['label']}*\n"
        f"📅 {today}\n\n"
    )

    for i, s in enumerate(stories):

        n = NUMS[i]

        msg += (
            f"{n} *{s['title']}*\n"
            f"📍 {s['source']}\n"
            f"📝 {s['summary']}\n"
            f"🔗 {s['link']}\n\n"
        )

    msg += (
        "━━━━━━━━━━━━━━\n"
        "📰 /news\n"
        "⚙️ /preferences"
    )

    return msg[:4096]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 SEND NEWS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_news(chat_id, niche):

    try:

        stories = fetch_news(niche)

        if not stories:

            bot.send_message(
                chat_id,
                "⚠️ No news found right now."
            )

            return

        message = build_message(
            niche,
            stories
        )

        bot.send_message(
            chat_id,
            message,
            disable_web_page_preview=True
        )

        log_delivery(
            chat_id,
            niche,
            len(stories)
        )

    except Exception as e:

        print(f"[SEND ERROR] {e}")

        try:

            bot.send_message(
                chat_id,
                "⚠️ Failed to send news."
            )

        except:
            pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📩 COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=["start"])
def start(message):

    cid = message.chat.id

    fname = message.chat.first_name or "Friend"

    upsert_user(
        cid,
        message.chat.username,
        message.chat.first_name,
        message.chat.last_name
    )

    bot.send_message(
        cid,
        (
            f"👋 *Welcome {escape_markdown(fname)}*\n\n"
            f"Choose your news category 👇"
        ),
        reply_markup=niche_keyboard()
    )


@bot.message_handler(commands=["news"])
def news(message):

    cid = message.chat.id

    user = get_user(cid)

    if not user:

        start(message)

        return

    niche = user.get(
        "niche",
        "world"
    )

    bot.send_message(
        cid,
        "📡 Fetching latest news..."
    )

    threading.Thread(
        target=send_news,
        args=(cid, niche),
        daemon=True
    ).start()


@bot.message_handler(commands=["preferences"])
def preferences(message):

    bot.send_message(
        message.chat.id,
        "⚙️ Choose your category:",
        reply_markup=niche_keyboard()
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔘 CALLBACKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):

    cid = call.message.chat.id

    data = call.data

    bot.answer_callback_query(call.id)

    if data.startswith("niche:"):

        niche = data.split(":")[1]

        set_niche(cid, niche)

        cfg = NICHES.get(
            niche,
            NICHES["world"]
        )

        bot.edit_message_text(
            (
                f"✅ Selected: *{cfg['label']}*\n\n"
                f"📡 Fetching latest news..."
            ),
            cid,
            call.message.message_id,
            parse_mode="Markdown"
        )

        threading.Thread(
            target=send_news,
            args=(cid, niche),
            daemon=True
        ).start()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⏰ DAILY BROADCAST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def daily_broadcast():

    users = get_all_active()

    print(f"Broadcasting to {len(users)} users")

    for u in users:

        try:

            send_news(
                u["chat_id"],
                u.get("niche", "world")
            )

            time.sleep(1)

        except Exception as e:

            print(f"[BROADCAST ERROR] {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 FLASK SERVER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__)

@app.route("/")
def home():

    return (
        "<h2>🤖 AI News Bot Running</h2>"
        "<p>Telegram bot is active.</p>"
    )

@app.route("/ping")
def ping():

    return "pong", 200

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":

    print("Starting AI News Bot...")

    init_db()

    print("Database initialized")

    def run_bot():

        IST = pytz.timezone(TIMEZONE)

        scheduler = BackgroundScheduler(
            timezone=IST
        )

        scheduler.add_job(
            daily_broadcast,
            CronTrigger(
                hour=DAILY_HOUR,
                minute=DAILY_MINUTE,
                timezone=IST
            )
        )

        scheduler.start()

        print("Scheduler started")

        try:

            bot.send_message(
                ADMIN_CHAT_ID,
                "🟢 AI News Bot is LIVE on Render!"
            )

        except Exception as e:

            print(f"[ADMIN ERROR] {e}")

        while True:

            try:

                print("Polling started...")

                bot.infinity_polling(
                    timeout=30,
                    long_polling_timeout=20
                )

            except Exception as e:

                print(f"[POLLING ERROR] {e}")

                time.sleep(5)

    threading.Thread(
        target=run_bot,
        daemon=True
    ).start()

    print(f"Opening Flask port {PORT}")

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
