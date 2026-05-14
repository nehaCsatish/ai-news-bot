
# ╔══════════════════════════════════════════════════════════════════╗
# ║       🤖  AI NEWS BOT  —  Render-Ready Version                 ║
# ║  Includes Flask health endpoint so Render Free Tier works!     ║
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
#  ⚙️  CONFIG  —  Edit ONLY this section
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN     = os.getenv("BOT_TOKEN",  "8762726870:AAF3C2NfW2z81lXwjU2xaH7UG872xrsonSg")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "940928434"))
PORT          = int(os.getenv("PORT", 10000))   # Render sets PORT automatically
TIMEZONE      = "Asia/Kolkata"
DAILY_HOUR    = 8
DAILY_MINUTE  = 0
DB_FILE       = "newsbot.db"

# ── 12 available news niches ──────────────────────────────────────
NICHES = {
    "pets":          {"label": "🐾 Pets & Animals",       "query": "pets animals wildlife rescue veterinary"},
    "ai":            {"label": "🤖 AI & Technology",      "query": "artificial intelligence machine learning technology"},
    "finance":       {"label": "💰 Finance & Markets",    "query": "stock market finance economy cryptocurrency"},
    "sports":        {"label": "⚽ Sports",               "query": "sports football cricket tennis Olympics"},
    "cybersecurity": {"label": "🔒 Cybersecurity",        "query": "cybersecurity hacking data breach ransomware"},
    "environment":   {"label": "🌿 Environment",          "query": "climate change environment renewable energy"},
    "health":        {"label": "🏥 Health & Medicine",    "query": "health medicine FDA drug approval research"},
    "entertainment": {"label": "🎬 Entertainment",        "query": "movies music entertainment Hollywood Netflix"},
    "world":         {"label": "🌍 World News",           "query": "world news international breaking news today"},
    "science":       {"label": "🔬 Science & Space",      "query": "science space NASA physics biology discovery"},
    "india":         {"label": "🇮🇳 India News",          "query": "India news today politics economy society"},
    "business":      {"label": "📈 Business & Startups",  "query": "business startup funding entrepreneurship"},
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🗄️  DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = get_db()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id    INTEGER PRIMARY KEY,
            username   TEXT, first_name TEXT, last_name TEXT,
            niche      TEXT    DEFAULT 'world',
            is_active  INTEGER DEFAULT 1,
            state      TEXT    DEFAULT 'idle',
            joined_at  TEXT    DEFAULT (datetime('now')),
            updated_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS news_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER, niche TEXT,
            story_count INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'sent',
            sent_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER, event_type TEXT, payload TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    c.commit(); c.close()

def upsert_user(chat_id, username=None, first_name=None, last_name=None):
    c = get_db()
    if not c.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)).fetchone():
        c.execute("INSERT INTO users (chat_id,username,first_name,last_name) VALUES (?,?,?,?)",
                  (chat_id, username, first_name, last_name))
        c.commit()
    c.close()

def set_niche(chat_id, niche):
    c = get_db()
    c.execute("UPDATE users SET niche=?,updated_at=datetime('now') WHERE chat_id=?", (niche, chat_id))
    c.commit(); c.close()

def set_state(chat_id, state):
    c = get_db()
    c.execute("UPDATE users SET state=? WHERE chat_id=?", (state, chat_id))
    c.commit(); c.close()

def get_user(chat_id):
    c = get_db()
    row = c.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    c.close()
    return dict(row) if row else None

def get_all_active():
    c = get_db()
    rows = c.execute("SELECT * FROM users WHERE is_active=1").fetchall()
    c.close()
    return [dict(r) for r in rows]

def log_event(chat_id, event_type, payload=None):
    c = get_db()
    c.execute("INSERT INTO events (chat_id,event_type,payload) VALUES (?,?,?)",
              (chat_id, event_type, str(payload)))
    c.commit(); c.close()

def log_delivery(chat_id, niche, count, status="sent"):
    c = get_db()
    c.execute("INSERT INTO news_log (chat_id,niche,story_count,status) VALUES (?,?,?,?)",
              (chat_id, niche, count, status))
    c.commit(); c.close()

def get_stats():
    c = get_db()
    total      = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active     = c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    deliveries = c.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
    niches     = c.execute("SELECT niche,COUNT(*) FROM users GROUP BY niche ORDER BY 2 DESC").fetchall()
    c.close()
    return {"total": total, "active": active, "deliveries": deliveries,
            "niches": [(r[0], r[1]) for r in niches]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  📰  NEWS FETCHER  (Google News RSS — free, no API key)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clean(text):
    if not text: return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()

def fetch_news(niche: str, num: int = 10) -> list:
    cfg   = NICHES.get(niche, NICHES["world"])
    query = cfg["query"].replace(" ", "+")
    url   = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        feed    = feedparser.parse(url)
        stories = []
        seen    = set()
        for e in feed.entries[:num * 2]:
            title   = clean(e.get("title", ""))
            summary = clean(e.get("summary", ""))[:280]
            link    = e.get("link", "")
            source  = e.get("source", {}).get("title", "Google News")
            pub     = e.get("published", "")[:16]
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            if " - " in title:
                p = title.rsplit(" - ", 1)
                title, source = p[0].strip(), p[1].strip()
            stories.append({
                "title":   title[:110],
                "summary": summary,
                "link":    link,
                "source":  source[:50],
                "date":    pub,
            })
            if len(stories) >= num:
                break
        return stories
    except Exception as e:
        print(f"[fetch_news] {e}")
        return []

NUMS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","1️⃣1️⃣","1️⃣2️⃣"]

def fmt_msg(niche, stories, fname="Friend", part=1, total=2):
    cfg   = NICHES.get(niche, {"label": "World News"})
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%B %d, %Y")
    if part == 1:
        msg = (f"📰 *{cfg['label'].upper()} DAILY BRIEFING*\n"
               f"📅 {today}  ·  Hey *{fname}*! 👋\n"
               f"🤖 Curated by AI News Bot\n{'━'*28}\n\n")
    else:
        msg = f"📰 *{cfg['label']} — Continued*\n{'━'*28}\n\n"

    for i, s in enumerate(stories):
        n    = NUMS[i] if i < len(NUMS) else f"{i+1}."
        snip = s["summary"]
        if snip and not snip.endswith((".", "!", "?")): snip += "…"
        msg += (f"{n} *{s['title']}*\n"
                f"📍 {s['source']}"
                + (f"  ·  _{s['date'][:10]}_" if s["date"] else "")
                + ("\n📝 " + snip if snip else "")
                + f"\n🔗 {s['link']}\n\n")

    if part == total:
        msg += (f"{'━'*28}\n"
                f"⚙️ /preferences  📰 /news  📊 /mystats\n"
                f"🤖 _AI News Bot · Every morning 8 AM IST_")
    else:
        msg += f"_({part}/{total}) More stories below…_"
    return msg[:4096]

def build_msgs(niche, stories, fname="Friend"):
    chunks = [stories[i:i+5] for i in range(0, len(stories), 5)]
    return [fmt_msg(niche, c, fname, i+1, len(chunks)) for i, c in enumerate(chunks)]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🤖  BOT SETUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

def niche_kb():
    m = telebot.types.InlineKeyboardMarkup(row_width=2)
    btns = [telebot.types.InlineKeyboardButton(
                cfg["label"], callback_data=f"niche:{k}")
            for k, cfg in NICHES.items()]
    for i in range(0, len(btns), 2):
        m.add(*btns[i:i+2])
    return m

def confirm_kb(niche):
    m = telebot.types.InlineKeyboardMarkup(row_width=2)
    m.add(
        telebot.types.InlineKeyboardButton("✅ Yes! That's my topic", callback_data=f"confirm:{niche}"),
        telebot.types.InlineKeyboardButton("🔄 Let me re-pick",       callback_data="pick_again"),
    )
    return m

def preview_kb():
    m = telebot.types.InlineKeyboardMarkup(row_width=2)
    m.add(
        telebot.types.InlineKeyboardButton("📰 Send today's briefing!", callback_data="get_now"),
        telebot.types.InlineKeyboardButton("⏳ Wait till 8 AM",         callback_data="wait"),
    )
    return m

def send_news(chat_id, niche, fname="Friend"):
    try:
        stories = fetch_news(niche, num=10)
        if not stories:
            bot.send_message(chat_id, "⚠️ No stories right now. Try again in a few minutes!")
            log_delivery(chat_id, niche, 0, "failed")
            return
        for msg in build_msgs(niche, stories, fname):
            bot.send_message(chat_id, msg, disable_web_page_preview=True)
            time.sleep(0.3)
        log_delivery(chat_id, niche, len(stories), "sent")
    except Exception as e:
        print(f"[send_news] {e}")
        log_delivery(chat_id, niche, 0, "failed")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  📩  COMMAND HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=["start"])
def cmd_start(message):
    cid   = message.chat.id
    fname = message.chat.first_name or "Friend"
    upsert_user(cid, message.chat.username, message.chat.first_name, message.chat.last_name)
    log_event(cid, "start")
    set_state(cid, "picking_niche")
    bot.send_chat_action(cid, "typing")
    bot.send_message(cid,
        f"👋 *Welcome, {fname}!*\n\n"
        "I'm your *AI Personalised News Bot* 🤖\n\n"
        "Every morning at *8:00 AM IST* I deliver the\n"
        "*top 10 fresh stories* — tailored to YOUR topic.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 *Pick your news niche to get started:*",
        reply_markup=niche_kb()
    )


@bot.message_handler(commands=["preferences", "settings"])
def cmd_prefs(message):
    cid   = message.chat.id
    fname = message.chat.first_name or "Friend"
    set_state(cid, "picking_niche")
    bot.send_chat_action(cid, "typing")
    bot.send_message(cid,
        f"⚙️ *Change your topic, {fname}*\n\n👇 Pick a new niche:",
        reply_markup=niche_kb()
    )


@bot.message_handler(commands=["news"])
def cmd_news(message):
    cid   = message.chat.id
    fname = message.chat.first_name or "Friend"
    user  = get_user(cid)
    if not user:
        cmd_start(message)
        return
    niche = user.get("niche", "world")
    cfg   = NICHES.get(niche, {"label": "News"})
    bot.send_chat_action(cid, "typing")
    bot.send_message(cid,
        f"📡 *Fetching your {cfg['label']} briefing…* ⏳\n_~10 seconds!_")
    log_event(cid, "news_request", niche)
    threading.Thread(target=send_news, args=(cid, niche, fname), daemon=True).start()


@bot.message_handler(commands=["mystats"])
def cmd_mystats(message):
    cid  = message.chat.id
    user = get_user(cid)
    if not user:
        bot.send_message(cid, "Please /start first!")
        return
    c          = get_db()
    deliveries = c.execute("SELECT COUNT(*) FROM news_log WHERE chat_id=?", (cid,)).fetchone()[0]
    c.close()
    niche = user.get("niche", "world")
    cfg   = NICHES.get(niche, {"label": niche})
    bot.send_message(cid,
        f"📊 *Your Stats*\n{'━'*22}\n"
        f"👤 Name: {user.get('first_name') or 'N/A'}\n"
        f"📰 Topic: {cfg['label']}\n"
        f"📬 Briefings Received: *{deliveries}*\n"
        f"📅 Member Since: {str(user.get('joined_at', ''))[:10]}\n"
        f"⏰ Daily Delivery: *8:00 AM IST*\n\n"
        f"⚙️ /preferences — change topic\n"
        f"📰 /news — get stories now"
    )


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    cid = message.chat.id
    if cid != ADMIN_CHAT_ID:
        bot.send_message(cid, "⛔ Admin only command.")
        return
    s  = get_stats()
    nb = "\n".join(
        f"  {NICHES.get(k, {}).get('label', k)}: *{v}* users"
        for k, v in s["niches"]
    ) or "_None yet_"
    bot.send_message(cid,
        f"🛠 *Admin Dashboard*\n{'━'*22}\n"
        f"👥 Total Users: *{s['total']}*\n"
        f"✅ Active: *{s['active']}*\n"
        f"📬 Deliveries: *{s['deliveries']}*\n\n"
        f"📊 *Niche Breakdown:*\n{nb}"
    )


@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    cid = message.chat.id
    if cid != ADMIN_CHAT_ID:
        bot.send_message(cid, "⛔ Admin only.")
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        bot.send_message(cid, "Usage: /broadcast your message here")
        return
    users   = get_all_active()
    success = 0
    for u in users:
        try:
            bot.send_message(u["chat_id"], f"📢 *Announcement*\n\n{text}")
            success += 1
            time.sleep(0.05)
        except Exception:
            pass
    bot.send_message(cid, f"✅ Sent to {success}/{len(users)} users.")


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(message.chat.id,
        "🤖 *AI News Bot — Commands*\n{'━'*24}\n"
        "🚀 /start — Setup & pick topic\n"
        "📰 /news — Get briefing right now\n"
        "⚙️ /preferences — Change your niche\n"
        "📊 /mystats — Your stats\n"
        "❓ /help — This menu\n\n"
        "_Briefings auto-deliver at *8 AM IST* daily!_ 🌅"
    )


@bot.message_handler(func=lambda m: True)
def fallback(message):
    user = get_user(message.chat.id)
    if not user:
        cmd_start(message)
        return
    bot.send_message(message.chat.id,
        "🤔 Try: /news · /preferences · /help")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🔘  CALLBACK HANDLER  (inline button taps)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    cid   = call.message.chat.id
    fname = call.message.chat.first_name or "Friend"
    data  = call.data
    bot.answer_callback_query(call.id)

    if data.startswith("niche:"):
        niche = data.split(":", 1)[1]
        cfg   = NICHES.get(niche, {"label": niche})
        set_state(cid, f"confirm:{niche}")
        bot.edit_message_text(
            chat_id=cid, message_id=call.message.message_id,
            text=(f"You chose: *{cfg['label']}*\n\n"
                  f"📬 Top 10 *{cfg['label']}* stories\n"
                  f"every morning at *8:00 AM IST* 🌅\n\nConfirm?"),
            parse_mode="Markdown", reply_markup=confirm_kb(niche)
        )

    elif data.startswith("confirm:"):
        niche = data.split(":", 1)[1]
        set_niche(cid, niche)
        set_state(cid, "idle")
        cfg = NICHES.get(niche, {"label": niche})
        log_event(cid, "niche_set", niche)
        bot.edit_message_text(
            chat_id=cid, message_id=call.message.message_id,
            text=(f"✅ *All set, {fname}!*\n\n"
                  f"{cfg['label']} ← your daily topic\n"
                  f"⏰ Delivery starts: *tomorrow 8:00 AM IST*\n\n"
                  f"Want a sneak peek right now?"),
            parse_mode="Markdown", reply_markup=preview_kb()
        )

    elif data == "pick_again":
        set_state(cid, "picking_niche")
        bot.edit_message_text(
            chat_id=cid, message_id=call.message.message_id,
            text="No problem! 👇 Pick a different topic:",
            parse_mode="Markdown", reply_markup=niche_kb()
        )

    elif data == "get_now":
        user  = get_user(cid)
        niche = user["niche"] if user else "world"
        cfg   = NICHES.get(niche, {"label": "News"})
        bot.edit_message_text(
            chat_id=cid, message_id=call.message.message_id,
            text=f"📡 *Fetching your {cfg['label']} briefing…* ⏳\n_~10 seconds!_",
            parse_mode="Markdown"
        )
        threading.Thread(target=send_news, args=(cid, niche, fname), daemon=True).start()

    elif data == "wait":
        bot.edit_message_text(
            chat_id=cid, message_id=call.message.message_id,
            text="⏰ See you tomorrow at *8:00 AM IST*! 🌅\nUse /news for instant stories anytime.",
            parse_mode="Markdown"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ⏰  SCHEDULER  (daily 8 AM IST broadcast to ALL users)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def daily_broadcast():
    users = get_all_active()
    print(f"[{datetime.now().strftime('%H:%M')}] 📬 Broadcasting to {len(users)} users…")
    for u in users:
        try:
            send_news(u["chat_id"], u.get("niche", "world"), u.get("first_name") or "Friend")
            time.sleep(0.2)
        except Exception as e:
            print(f"  ❌ {u['chat_id']}: {e}")
    print("✅ Broadcast complete.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🌐  FLASK HEALTH SERVER  (keeps Render free tier awake)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

health_app = Flask(__name__)

@health_app.route("/")
def home():
    s = get_stats()
    return (
        f"<h2>🤖 AI News Bot — Live</h2>"
        f"<p>✅ Bot is running and polling Telegram</p>"
        f"<p>👥 Users: <b>{s['total']}</b> &nbsp;|&nbsp; "
        f"📬 Deliveries: <b>{s['deliveries']}</b></p>"
        f"<p>⏰ Daily delivery at 8:00 AM IST</p>"
        f"<hr><small>Ping this URL with UptimeRobot every 5 min to keep bot alive!</small>"
    ), 200

@health_app.route("/health")
def health():
    return {"status": "ok", "bot": "running"}, 200

@health_app.route("/ping")
def ping():
    return "pong", 200

def run_health_server():
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🚀  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    print("━" * 48)
    print("  🤖  AI News Bot — Starting on Render…")
    print("━" * 48)

    # ── Everything EXCEPT Flask runs in a background thread ──────────
    # Flask MUST be the first thing that binds to a port so Render
    # doesn't time out while waiting for an open port.
    def background_startup():
        import time as _t
        _t.sleep(1)                    # tiny pause — let Flask bind first

        # 1. DB
        init_db()
        print("✅ Database initialised")

        # 2. Scheduler
        IST = pytz.timezone(TIMEZONE)
        sched = BackgroundScheduler(timezone=IST)
        sched.add_job(
            daily_broadcast,
            CronTrigger(hour=DAILY_HOUR, minute=DAILY_MINUTE, timezone=IST),
            id="daily_news", replace_existing=True, misfire_grace_time=600,
        )
        sched.start()
        print(f"✅ Scheduler: daily at {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} IST")

        # 3. Notify admin
        try:
            bot.send_message(ADMIN_CHAT_ID,
                "🟢 *AI News Bot is LIVE on Render!* 🎉\n\n"
                "✅ Running & accepting messages\n"
                "✅ 12 news niches available\n"
                "✅ Daily delivery at 8:00 AM IST\n\n"
                "Share your bot — anyone can /start!\n"
                "/stats to see all users."
            )
            print("✅ Admin notified on Telegram")
        except Exception as e:
            print(f"Admin notify: {e}")

        # 4. Start polling
        print("📱 Bot polling started…")
        bot.infinity_polling(timeout=30, long_polling_timeout=20)

    # Kick off everything in the background
    bg = threading.Thread(target=background_startup, daemon=True)
    bg.start()

    # ── Flask on MAIN THREAD — opens port IMMEDIATELY ✅ ─────────────
    # Render scans the main thread for an open port.
    # Flask binds here before any bot code runs → no more timeout.
    print(f"🌐 Opening port {PORT} — Render will detect this now…")
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
