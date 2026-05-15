# ╔══════════════════════════════════════════════════════════════════╗
# ║              🐾 PET INDUSTRY AI NEWS BOT — PRO                 ║
# ║      Telegram + Render + Flask + APScheduler + SQLite         ║
# ╚══════════════════════════════════════════════════════════════════╝

import os
import re
import html
import time
import logging
import threading
import sqlite3
from datetime import datetime

import feedparser
import pytz
import telebot

from flask import Flask
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ ENVIRONMENT VARIABLES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable missing!")

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

PORT = int(os.getenv("PORT", 10000))

DEFAULT_TIMEZONE = os.getenv(
    "DEFAULT_TIMEZONE",
    "Asia/Kolkata"
)

DB_FILE = "petnewsbot.db"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🪵 LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("PetNewsBot")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🤖 TELEGRAM BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="Markdown"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 FLASK APP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__)

@app.route("/")
def home():
    return (
        "<h2>🐾 Pet Industry AI News Bot Running</h2>"
        "<p>Telegram bot is active.</p>"
    )

@app.route("/ping")
def ping():
    return "pong", 200

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

        timezone TEXT DEFAULT 'Asia/Kolkata',

        delivery_time TEXT DEFAULT '08:00',

        is_subscribed INTEGER DEFAULT 1,

        joined_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sent_news (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        chat_id INTEGER,

        news_hash TEXT,

        sent_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    """)

    db.commit()
    db.close()

    logger.info("✅ Database initialized")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 👤 USER FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def add_user(chat):

    db = get_db()

    db.execute(
        """
        INSERT OR IGNORE INTO users
        (
            chat_id,
            username,
            first_name,
            last_name
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            chat.id,
            chat.username,
            chat.first_name,
            chat.last_name
        )
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
        """
        SELECT * FROM users
        WHERE is_subscribed=1
        """
    ).fetchall()

    db.close()

    return [dict(r) for r in rows]


def set_user_time(chat_id, delivery_time):

    db = get_db()

    db.execute(
        """
        UPDATE users
        SET delivery_time=?,
            is_subscribed=1
        WHERE chat_id=?
        """,
        (
            delivery_time,
            chat_id
        )
    )

    db.commit()
    db.close()


def unsubscribe_user(chat_id):

    db = get_db()

    db.execute(
        """
        UPDATE users
        SET is_subscribed=0
        WHERE chat_id=?
        """,
        (chat_id,)
    )

    db.commit()
    db.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🧹 TEXT CLEANERS
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
# 📰 PET INDUSTRY NEWS FETCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEWS_QUERY = """
pet industry OR pet care OR veterinary OR pet food
OR animal startup OR pet technology latest news when:2d
"""

def fetch_pet_news(limit=5):

    query = NEWS_QUERY.strip().replace(" ", "+")

    url = (
        "https://news.google.com/rss/search?"
        f"q={query}"
        "&hl=en-IN"
        "&gl=IN"
        "&ceid=IN:en"
    )

    logger.info(f"📡 Fetching news from Google RSS")

    try:

        feed = feedparser.parse(url)

        stories = []

        seen = set()

        for entry in feed.entries:

            title = clean(entry.get("title", ""))

            summary = clean(
                entry.get("summary", "")
            )[:180]

            source = clean(
                entry.get(
                    "source",
                    {}
                ).get(
                    "title",
                    "Google News"
                )
            )

            link = entry.get("link", "")

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

            stories.append({
                "title": escape_markdown(title),
                "summary": escape_markdown(summary),
                "source": escape_markdown(source),
                "link": link
            })

            if len(stories) >= limit:
                break

        logger.info(f"✅ Fetched {len(stories)} stories")

        return stories

    except Exception as e:

        logger.error(f"❌ News fetch failed: {e}")

        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ✨ MESSAGE BUILDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NUMBERS = [
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"
]

def build_news_message(stories):

    today = datetime.now(
        pytz.timezone(DEFAULT_TIMEZONE)
    ).strftime("%B %d, %Y")

    message = (
        f"🐾 *PET INDUSTRY DAILY BRIEFING*\n"
        f"📅 {today}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for i, story in enumerate(stories):

        message += (
            f"{NUMBERS[i]} *{story['title']}*\n"
            f"🏢 {story['source']}\n"
            f"📝 {story['summary']}\n"
            f"🔗 {story['link']}\n\n"
        )

    message += (
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 Powered by AI News Bot\n"
        "⚙️ /settime HH:MM\n"
        "🛑 /unsubscribe"
    )

    return message[:4096]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 SEND NEWS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_news(chat_id):

    try:

        stories = fetch_pet_news()

        if not stories:

            bot.send_message(
                chat_id,
                "⚠️ No pet industry news found right now."
            )

            return

        message = build_news_message(stories)

        bot.send_message(
            chat_id,
            message,
            disable_web_page_preview=True
        )

        logger.info(f"✅ News sent to {chat_id}")

    except Exception as e:

        logger.error(f"❌ Send error: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⏰ SMART DAILY SCHEDULER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scheduler_loop():

    logger.info("⏰ Scheduler started")

    while True:

        try:

            users = get_all_users()

            now = datetime.now(
                pytz.timezone(DEFAULT_TIMEZONE)
            )

            current_time = now.strftime("%H:%M")

            for user in users:

                if user["delivery_time"] == current_time:

                    logger.info(
                        f"📨 Sending scheduled news to {user['chat_id']}"
                    )

                    threading.Thread(
                        target=send_news,
                        args=(user["chat_id"],),
                        daemon=True
                    ).start()

                    time.sleep(1)

            time.sleep(60)

        except Exception as e:

            logger.error(f"❌ Scheduler error: {e}")

            time.sleep(10)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎛️ INLINE KEYBOARD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main_keyboard():

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "📰 Get Latest News",
            callback_data="latest_news"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "⚙️ Set Delivery Time",
            callback_data="set_time"
        )
    )

    return markup


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=["start"])
def start(message):

    add_user(message.chat)

    fname = escape_markdown(
        message.chat.first_name or "Friend"
    )

    welcome = (
        f"👋 *Welcome {fname}!*\n\n"
        f"🐾 I'm your *Pet Industry AI News Bot*\n\n"
        f"Every day you'll receive:\n"
        f"• Pet industry news\n"
        f"• Veterinary updates\n"
        f"• Pet care trends\n"
        f"• Animal startups\n"
        f"• Pet technology news\n\n"
        f"⏰ Default delivery time: *08:00 AM*\n\n"
        f"Use:\n"
        f"`/settime 08:30`\n"
        f"to customize your news time."
    )

    bot.send_message(
        message.chat.id,
        welcome,
        reply_markup=main_keyboard()
    )


@bot.message_handler(commands=["help"])
def help_command(message):

    help_text = (
        "📚 *AVAILABLE COMMANDS*\n\n"
        "📰 /news → Get latest news now\n"
        "⏰ /settime HH:MM → Set daily news time\n"
        "🕒 /mytime → View your saved time\n"
        "🛑 /unsubscribe → Stop daily news\n"
        "▶️ /start → Restart the bot\n"
        "❓ /help → Show help menu\n\n"
        "Example:\n"
        "`/settime 21:30`"
    )

    bot.send_message(
        message.chat.id,
        help_text
    )


@bot.message_handler(commands=["news"])
def latest_news(message):

    bot.send_message(
        message.chat.id,
        "📡 Fetching latest pet industry news..."
    )

    threading.Thread(
        target=send_news,
        args=(message.chat.id,),
        daemon=True
    ).start()


@bot.message_handler(commands=["mytime"])
def my_time(message):

    user = get_user(message.chat.id)

    if not user:
        return

    bot.send_message(
        message.chat.id,
        (
            f"⏰ Your daily news time is:\n\n"
            f"*{user['delivery_time']}*"
        )
    )


@bot.message_handler(commands=["unsubscribe", "stoptime"])
def unsubscribe(message):

    unsubscribe_user(message.chat.id)

    bot.send_message(
        message.chat.id,
        (
            "🛑 You have unsubscribed from daily news.\n\n"
            "Use /settime HH:MM anytime to re-enable."
        )
    )


@bot.message_handler(commands=["settime"])
def set_time(message):

    try:

        parts = message.text.split()

        if len(parts) != 2:

            raise ValueError

        time_input = parts[1]

        datetime.strptime(
            time_input,
            "%H:%M"
        )

        set_user_time(
            message.chat.id,
            time_input
        )

        bot.send_message(
            message.chat.id,
            (
                f"✅ Daily delivery time updated!\n\n"
                f"🕒 New time: *{time_input}*\n"
                f"📅 You will receive pet industry news daily."
            )
        )

    except:

        bot.send_message(
            message.chat.id,
            (
                "❌ Invalid format.\n\n"
                "Use:\n"
                "`/settime 08:30`"
            )
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔘 CALLBACKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):

    bot.answer_callback_query(call.id)

    if call.data == "latest_news":

        bot.send_message(
            call.message.chat.id,
            "📡 Loading latest news..."
        )

        threading.Thread(
            target=send_news,
            args=(call.message.chat.id,),
            daemon=True
        ).start()

    elif call.data == "set_time":

        bot.send_message(
            call.message.chat.id,
            (
                "⏰ Send your preferred time.\n\n"
                "Example:\n"
                "`/settime 21:00`"
            )
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 STARTUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":

    logger.info("🚀 Starting Pet Industry AI News Bot")

    init_db()

    # Start scheduler thread
    threading.Thread(
        target=scheduler_loop,
        daemon=True
    ).start()

    # Notify admin
    if ADMIN_CHAT_ID:

        try:

            bot.send_message(
                ADMIN_CHAT_ID,
                "🟢 Pet Industry AI News Bot is LIVE!"
            )

        except Exception as e:

            logger.error(f"Admin notify failed: {e}")

    # Start polling thread
    def run_bot():

        while True:

            try:

                logger.info("🤖 Bot polling started")

                bot.infinity_polling(
                    timeout=30,
                    long_polling_timeout=20
                )

            except Exception as e:

                logger.error(f"Polling error: {e}")

                time.sleep(5)

    threading.Thread(
        target=run_bot,
        daemon=True
    ).start()

    logger.info(f"🌐 Flask running on port {PORT}")

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
