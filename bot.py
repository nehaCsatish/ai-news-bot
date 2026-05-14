# ╔══════════════════════════════════════════════════════════════════╗
# ║                🤖 AI NEWS BOT — FINAL STABLE VERSION           ║
# ║            Render + Telegram + Google News RSS                ║
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
    raise ValueError("❌ BOT_TOKEN environment variable missing!")

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "940928434"))

PORT = int(os.getenv("PORT", 10000))

TIMEZONE = "Asia/Kolkata"

DAILY_HOUR = 8
DAILY_MINUTE = 0

DB_FILE = "newsbot.db"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 NEWS NICHES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NICHES = {
    "pets": {
        "label": "🐾 Pets & Animals",
        "query": "pets animals wildlife rescue veterinary"
    },

    "ai": {
        "label": "🤖 AI & Technology",
        "query": "artificial intelligence machine learning technology"
    },

    "finance": {
        "label": "💰 Finance & Markets",
        "query": "stock market finance economy cryptocurrency"
    },

    "sports": {
        "label": "⚽ Sports",
        "query": "sports football cricket tennis Olympics"
    },

    "cybersecurity": {
        "label": "🔒 Cybersecurity",
        "query": "cybersecurity hacking data breach ransomware"
    },

    "environment": {
        "label": "🌿 Environment",
        "query": "climate change environment renewable energy"
    },

    "health": {
        "label": "🏥 Health & Medicine",
        "query": "health medicine FDA drug approval research"
    },

    "entertainment": {
        "label": "🎬 Entertainment",
        "query": "movies music entertainment Hollywood Netflix"
    },

    "world": {
        "label": "🌍 World News",
        "query": "world news international breaking news today"
    },

    "science": {
        "label": "🔬 Science & Space",
        "query": "science space NASA physics biology discovery"
    },

    "india": {
        "label": "🇮🇳 India News",
        "query": "India news today politics economy society"
    },

    "business": {
        "label": "📈 Business & Startups",
        "query": "business startup funding entrepreneurship"
    },
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    c = get_db()

    c.executescript("""
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
            story_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'sent',
            sent_at TEXT DEFAULT (datetime('now'))
        );
    """)

    c.commit()
    c.close()


def upsert_user(chat_id, username=None, first_name=None, last_name=None):

    c = get_db()

    if not c.execute(
        "SELECT 1 FROM users WHERE chat_id=?",
        (chat_id,)
    ).fetchone():

        c.execute(
            """
            INSERT INTO users
            (chat_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, username, first_name, last_name)
        )

        c.commit()

    c.close()


def get_user(chat_id):

    c = get_db()

    row = c.execute(
        "SELECT * FROM users WHERE chat_id=?",
        (chat_id,)
    ).fetchone()

    c.close()

    return dict(row) if row else None


def set_niche(chat_id, niche):

    c = get_db()

    c.execute(
        "UPDATE users SET niche=? WHERE chat_id=?",
        (niche, chat_id)
    )

    c.commit()
    c.close()


def get_all_active():

    c = get_db()

    rows = c.execute(
        "SELECT * FROM users WHERE is_active=1"
    ).fetchall()

    c.close()

    return [dict(r) for r in rows]


def log_delivery(chat_id, niche, count, status="sent"):

    c = get_db()

    c.execute(
        """
        INSERT INTO news_log
        (chat_id, niche, story_count, status)
        VALUES (?, ?, ?, ?)
        """,
        (chat_id, niche, count, status)
    )

    c.commit()
    c.close()


def get_stats():

    c = get_db()

    total = c.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    deliveries = c.execute(
        "SELECT COUNT(*) FROM news_log"
    ).fetchone()[0]

    c.close()

    return {
        "total": total,
        "deliveries": deliveries
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 NEWS FETCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clean(text):

    if not text:
        return ""

    text = html.unescape(text)

    text = re.sub(r"<[^>]+>", "", text)

    return re.sub(r"\s+", " ", text).strip()


def escape_markdown(text):

    if not text:
        return ""

    escape_chars = r'\_*[]()~`>#+-=|{}.!'

    return ''.join(
        '\\' + c if c in escape_chars else c
        for c in text
    )


def fetch_news(niche: str, num: int = 10):

    cfg = NICHES.get(niche, NICHES["world"])

    query = (cfg["query"] + " when:2d").replace(" ", "+")

    url = (
        f"https://news.google.com/rss/search?"
        f"q={query}"
        f"&hl=en-IN"
        f"&gl=IN"
        f"&ceid=IN:en"
        f"&sort=date"
    )

    try:

        feed = feedparser.parse(url)

        stories = []

        seen = set()

        for e in feed.entries[:num * 4]:

            title = escape_markdown(
                clean(e.get("title", ""))
            )

            summary = escape_markdown(
                clean(e.get("summary", ""))[:250]
            )

            source = escape_markdown(
                e.get("source", {}).get("title", "Google News")
            )

            link = e.get("link", "")

            pub = e.get("published", "")[:16]

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

            if " - " in title:

                parts = title.rsplit(" - ", 1)

                title = parts[0].strip()

                source = parts[1].strip()

            stories.append({
                "title": title[:110],
                "summary": summary,
                "link": link,
                "source": source[:50],
                "date": pub,
            })

            if len(stories) >= num:
                break

        return stories

    except Exception as e:

        print(f"[fetch_news ERROR] {e}")

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

    print(f"✅ Connected to Telegram bot: @{me.username}")

except Exception as e:

    print(f"❌ Telegram connection failed: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔘 KEYBOARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def niche_kb():

    m = telebot.types.InlineKeyboardMarkup(row_width=2)

    buttons = []

    for k, cfg in NICHES.items():

        buttons.append(
            telebot.types.InlineKeyboardButton(
                cfg["label"],
                callback_data=f"niche:{k}"
            )
        )

    for i in range(0, len(buttons), 2):

        m.add(*buttons[i:i+2])

    return m

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📰 FORMATTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NUMS = [
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
    "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"
]


def format_news(niche, stories, fname="Friend"):

    cfg = NICHES.get(niche, {"label": "News"})

    today = datetime.now(
        pytz.timezone(TIMEZONE)
    ).strftime("%B %d, %Y")

    msg = (
        f"📰 *{cfg['label']}*\n"
        f"📅 {today}\n\n"
    )

    for i, s in enumerate(stories):

        n = NUMS[i] if i < len(NUMS) else f"{i+1}."

        msg += (
            f"{n} *{s['title']}*\n"
            f"📍 {s['source']}\n"
            f"📝 {s['summary']}\n"
            f"🔗 {s['link']}\n\n"
        )

    msg += (
        "━━━━━━━━━━━━━━\n"
        "⚙️ /preferences\n"
        "📰 /news"
    )

    return msg[:4096]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 SEND NEWS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_news(chat_id, niche, fname="Friend"):

    try:

        stories = fetch_news(niche)

        if not stories:

            bot.send_message(
                chat_id,
                "⚠️ No recent news available."
            )

            log_delivery(chat_id, niche, 0, "failed")

            return

        msg = format_news(
            niche,
            stories,
            fname
        )

        bot.send_message(
            chat_id,
            msg,
            disable_web_page_preview=True
        )

        log_delivery(
            chat_id,
            niche,
            len(stories),
            "sent"
        )

    except Exception as e:

        print(f"[send_news ERROR] {e}")

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
            f"👋 *Welcome {escape_markdown(fname)}!*\n\n"
            f"Choose your news category 👇"
        ),
        reply_markup=niche_kb()
    )


@bot.message_handler(commands=["news"])
def news(message):

    cid = message.chat.id

    user = get_user(cid)

    if not user:

        start(message)

        return

    niche = user.get("niche", "world")

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
def prefs(message):

    bot.send_message(
        message.chat.id,
        "⚙️ Choose a new category:",
        reply_markup=niche_kb()
    )


@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):

    cid = call.message.chat.id

    data = call.data

    if data.startswith("niche:"):

        niche = data.split(":")[1]

        set_niche(cid, niche)

        cfg = NICHES.get(niche)

        bot.edit_message_text(
            f"✅ Selected: *{cfg['label']}*",
            cid,
            call.message.message_id,
            parse_mode="Markdown"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⏰ DAILY BROADCAST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def daily_broadcast():

    users = get_all_active()

    print(f"📬 Broadcasting to {len(users)} users")

    for u in users:

        try:

            send_news(
                u["chat_id"],
                u.get("niche", "world"),
                u.get("first_name", "Friend")
            )

            time.sleep(1)

        except Exception as e:

            print(f"[Broadcast ERROR] {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 FLASK HEALTH SERVER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__)

@app.route("/")
def home():

    s = get_stats()

    return (
        f"<h2>🤖 AI News Bot Running</h2>"
        f"<p>Users: {s['total']}</p>"
        f"<p>Deliveries: {s['deliveries']}</p>"
    )


@app.route("/ping")
def ping():

    return "pong", 200

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":

    print("🤖 Starting AI News Bot...")

    init_db()

    print("✅ Database initialized")

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

        print("✅ Scheduler started")

        try:

            bot.send_message(
                ADMIN_CHAT_ID,
                "🟢 AI News Bot is LIVE!"
            )

        except Exception as e:

            print(f"[Admin Notify ERROR] {e}")

        while True:

            try:

                print("📱 Polling started...")

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

    print(f"🌐 Flask running on port {PORT}")

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
