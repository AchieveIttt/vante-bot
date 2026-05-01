import logging
import sqlite3
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ChatJoinRequestHandler, ContextTypes
)

TOKEN = "YOUR_NEW_TOKEN_HERE"
CHANNEL_ID = -0
ADMIN_USERNAME = "YOUR_USERNAME_HERE"
ADMIN_ID = 0
DB_FILE = "referrals.db"

# ─── DATABASE ───────────────────────────────────────────────────────────────

def db_connect():
    return sqlite3.connect(DB_FILE)

def db_init():
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS refs (
            key TEXT PRIMARY KEY,
            owner_id INTEGER,
            first_name TEXT,
            invite_link TEXT,
            registered TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS joins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_key TEXT,
            user_id INTEGER,
            joined_at TEXT
        )
    """)
    con.commit()
    con.close()

def db_save_ref(key, owner_id, first_name, invite_link, registered):
    con = db_connect()
    con.execute(
        "INSERT OR REPLACE INTO refs (key, owner_id, first_name, invite_link, registered) VALUES (?,?,?,?,?)",
        (key, owner_id, first_name, invite_link, registered.isoformat())
    )
    con.commit()
    con.close()

def db_add_join(ref_key, user_id):
    con = db_connect()
    con.execute(
        "INSERT INTO joins (ref_key, user_id, joined_at) VALUES (?,?,?)",
        (ref_key, user_id, datetime.now().isoformat())
    )
    con.commit()
    con.close()

def db_has_joined(ref_key, user_id):
    con = db_connect()
    cur = con.execute(
        "SELECT 1 FROM joins WHERE ref_key=? AND user_id=?", (ref_key, user_id)
    )
    result = cur.fetchone()
    con.close()
    return result is not None

def db_load_all():
    """Загружает всё из БД в память при старте"""
    refs = {}
    link_to_ref = {}
    user_to_ref = {}

    con = db_connect()

    for row in con.execute("SELECT key, owner_id, first_name, invite_link, registered FROM refs"):
        key, owner_id, first_name, invite_link, registered = row
        joins_rows = con.execute(
            "SELECT joined_at FROM joins WHERE ref_key=?", (key,)
        ).fetchall()
        joins = [datetime.fromisoformat(r[0]) for r in joins_rows]

        refs[key] = {
            "owner_id": owner_id,
            "first_name": first_name,
            "invite_link": invite_link,
            "registered": datetime.fromisoformat(registered),
            "joins": joins,
        }
        link_to_ref[invite_link] = key
        user_to_ref[owner_id] = key

    con.close()
    return refs, link_to_ref, user_to_ref

# ─── GLOBALS (загружаются из БД при старте) ──────────────────────────────────

refs = {}
link_to_ref = {}
user_to_ref = {}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_level(n):
    if n < 15:
        return 1, 0.0
    elif n < 40:
        return 2, 0.50
    elif n < 60:
        return 3, 0.80
    else:
        return 4, 1.20

def calc_earnings(joins):
    n = len(joins)
    e = 0.0
    e += max(0, min(n, 39) - 14) * 0.50
    e += max(0, min(n, 59) - 39) * 0.80
    e += max(0, n - 59) * 1.20
    return round(e, 2)

def calc_period(joins, days):
    since = datetime.now() - timedelta(days=days)
    filtered = [j for j in joins if j >= since]
    n_before = len(joins) - len(filtered)
    e = 0.0
    for i in range(len(filtered)):
        pos = n_before + i
        if pos < 14:
            rate = 0.0
        elif pos < 39:
            rate = 0.50
        elif pos < 59:
            rate = 0.80
        else:
            rate = 1.20
        e += rate
    return round(e, 2), len(filtered)

# ─── KEYBOARDS ───────────────────────────────────────────────────────────────

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 My referral link", callback_data="my_link")],
        [InlineKeyboardButton("📊 My stats", callback_data="stats_menu")],
        [InlineKeyboardButton("🏆 Levels", callback_data="levels")],
        [InlineKeyboardButton("💬 Contact admin", url=f"https://t.me/{ADMIN_USERNAME}")],
    ])

def stats_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("7 days", callback_data="s7"),
         InlineKeyboardButton("14 days", callback_data="s14")],
        [InlineKeyboardButton("Month", callback_data="s30"),
         InlineKeyboardButton("Year", callback_data="s365")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])

def profile_text(user_id, name):
    if user_id in user_to_ref:
        d = refs[user_to_ref[user_id]]
        n = len(d["joins"])
        lvl, rate = get_level(n)
        earned = calc_earnings(d["joins"])
        reg = d["registered"].strftime("%d.%m.%Y")
        return (
            f"👋 Hey, *{name}*!\n\n"
            f"📅 Registered: {reg}\n"
            f"✅ Total joins: {n}\n"
            f"📈 Level: {lvl} ({rate}€/join)\n"
            f"💰 Total earned: {earned}€"
        )
    else:
        return (
            f"👋 Hey, *{name}*!\n\n"
            f"Welcome to Vante Affiliate Program.\n"
            f"Press the button below to get your referral link!"
        )

# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        profile_text(user.id, user.first_name),
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = user.id

    if query.data == "back":
        await query.edit_message_text(
            profile_text(uid, user.first_name),
            parse_mode="Markdown",
            reply_markup=main_kb()
        )

    elif query.data == "levels":
        await query.edit_message_text(
            "🏆 *Levels & Rates*\n\n"
            "1️⃣ *Level 1* — 0–14 joins → 0€/join\n"
            "2️⃣ *Level 2* — 15–39 joins → 0.50€/join\n"
            "3️⃣ *Level 3* — 40–59 joins → 0.80€/join\n"
            "4️⃣ *Level 4* — 60+ joins → 1.20€/join\n\n"
            "The more people join via your link — the higher your rate!",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )

    elif query.data == "my_link":
        if uid in user_to_ref:
            link = refs[user_to_ref[uid]]["invite_link"]
            await query.edit_message_text(
                f"🔗 *Your referral link:*\n\n`{link}`\n\nShare it and earn for every join!",
                parse_mode="Markdown",
                reply_markup=back_kb()
            )
        else:
            try:
                link_obj = await context.bot.create_chat_invite_link(
                    chat_id=CHANNEL_ID,
                    name=f"ref_{user.first_name}_{uid}",
                    creates_join_request=True
                )
                invite_link = link_obj.invite_link
            except Exception as e:
                await query.edit_message_text(
                    f"❌ Error creating link: {e}\n\nContact: @{ADMIN_USERNAME}",
                    reply_markup=back_kb()
                )
                return

            key = str(uid)
            now = datetime.now()
            refs[key] = {
                "owner_id": uid,
                "first_name": user.first_name,
                "invite_link": invite_link,
                "registered": now,
                "joins": [],
            }
            link_to_ref[invite_link] = key
            user_to_ref[uid] = key
            db_save_ref(key, uid, user.first_name, invite_link, now)

            await query.edit_message_text(
                f"✅ *Your referral link is ready:*\n\n`{invite_link}`\n\nShare it and earn for every join!",
                parse_mode="Markdown",
                reply_markup=back_kb()
            )

    elif query.data == "stats_menu":
        if uid not in user_to_ref:
            await query.edit_message_text(
                "⚠️ Get your referral link first!",
                reply_markup=back_kb()
            )
            return
        await query.edit_message_text("📊 Choose period:", reply_markup=stats_kb())

    elif query.data in ("s7", "s14", "s30", "s365"):
        if uid not in user_to_ref:
            return
        days_map = {"s7": 7, "s14": 14, "s30": 30, "s365": 365}
        label_map = {"s7": "7 days", "s14": "14 days", "s30": "Month", "s365": "Year"}
        days = days_map[query.data]
        label = label_map[query.data]

        joins = refs[user_to_ref[uid]]["joins"]
        earned_period, count_period = calc_period(joins, days)
        total = len(joins)
        total_earned = calc_earnings(joins)
        lvl, rate = get_level(total)

        await query.edit_message_text(
            f"📊 *Stats — last {label}*\n\n"
            f"✅ Joins: {count_period}\n"
            f"💰 Earned: {earned_period}€\n\n"
            f"*All time:*\n"
            f"✅ Total joins: {total}\n"
            f"📈 Level: {lvl} ({rate}€/join)\n"
            f"💰 Total earned: {total_earned}€",
            parse_mode="Markdown",
            reply_markup=stats_kb()
        )

async def allstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not refs:
        await update.message.reply_text("No partners yet.")
        return
    text = "📋 *All partners:*\n\n"
    for key, d in refs.items():
        n = len(d["joins"])
        earned = calc_earnings(d["joins"])
        lvl, _ = get_level(n)
        text += f"• *{d['first_name']}* — {n} joins | Lvl {lvl} | {earned}€\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    invite_link = request.invite_link.invite_link if request.invite_link else None
    user = request.from_user

    try:
        await context.bot.approve_chat_join_request(
            chat_id=request.chat.id,
            user_id=user.id
        )
    except Exception as e:
        print(f"Approve error: {e}")
        return

    if not invite_link or invite_link not in link_to_ref:
        return

    key = link_to_ref[invite_link]
    d = refs[key]

    # Антискам: владелец не считается
    if user.id == d["owner_id"]:
        return

    # Антискам: один человек один раз
    if db_has_joined(key, user.id):
        return

    # Засчитываем
    db_add_join(key, user.id)
    d["joins"].append(datetime.now())
    n = len(d["joins"])
    lvl, rate = get_level(n)
    earned = calc_earnings(d["joins"])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔔 *New join!*\n\n"
                f"👤 Partner: *{d['first_name']}*\n"
                f"✅ Total joins: {n}\n"
                f"📈 Level: {lvl} ({rate}€/join)\n"
                f"💰 Total earned: {earned}€"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Admin notify error: {e}")

    try:
        await context.bot.send_message(
            chat_id=d["owner_id"],
            text=(
                f"🎉 *New join via your link!*\n\n"
                f"✅ Total joins: {n}\n"
                f"📈 Level: {lvl} ({rate}€/join)\n"
                f"💰 Total earned: {earned}€"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Partner notify error: {e}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    global refs, link_to_ref, user_to_ref

    logging.basicConfig(level=logging.INFO)
    db_init()

    # Загружаем данные из БД
    refs, link_to_ref, user_to_ref = db_load_all()
    print(f"Loaded {len(refs)} partners from database.")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("allstats", allstats))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    print("Bot is running!")
    app.run_polling(allowed_updates=["message", "chat_join_request", "callback_query"])

if __name__ == "__main__":
    main()
