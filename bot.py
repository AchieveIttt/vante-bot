import logging
import sqlite3
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ChatJoinRequestHandler, ContextTypes
)

TOKEN = os.environ.get("TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
ADMIN_USERNAME = "innbetween"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7644277689"))
DB_FILE = "referrals.db"

refs = {}
link_to_ref = {}
user_to_ref = {}
warned = set()
banned = set()

RULES_TEXT = (
    "📋 *Vante Affiliate Program — Rules*\n\n"
    "1️⃣ You get paid for every *real* person who joins the channel via your link.\n\n"
    "2️⃣ *No cheating* — fake joins, bots, or self-joins are strictly forbidden.\n\n"
    "3️⃣ First violation = warning + stats reset.\n"
    "    Second violation = permanent ban.\n\n"
    "4️⃣ Payouts are made at the end of each month.\n"
    "    Contact admin to receive your payment.\n\n"
    "5️⃣ Level 1 (0–14 joins) is a *trial period* — no payout.\n"
    "    Reach Level 2 to start earning.\n\n"
    "6️⃣ Do not spam or mislead people to get joins.\n\n"
    "7️⃣ Admin reserves the right to review and adjust stats\n"
    "    in case of suspicious activity.\n\n"
    "By using this bot you agree to these rules. ✅"
)

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def db_connect():
    return sqlite3.connect(DB_FILE)

def db_init():
    con = db_connect()
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS refs (
        key TEXT PRIMARY KEY, owner_id INTEGER, first_name TEXT,
        invite_link TEXT, registered TEXT, bonus REAL DEFAULT 0,
        warned INTEGER DEFAULT 0, banned INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS joins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref_key TEXT, user_id INTEGER, joined_at TEXT
    )""")
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
    con.execute("INSERT INTO joins (ref_key, user_id, joined_at) VALUES (?,?,?)",
                (ref_key, user_id, datetime.now().isoformat()))
    con.commit()
    con.close()

def db_has_joined(ref_key, user_id):
    con = db_connect()
    cur = con.execute("SELECT 1 FROM joins WHERE ref_key=? AND user_id=?", (ref_key, user_id))
    result = cur.fetchone()
    con.close()
    return result is not None

def db_clear_joins(ref_key):
    con = db_connect()
    con.execute("DELETE FROM joins WHERE ref_key=?", (ref_key,))
    con.commit()
    con.close()

def db_set_warned(ref_key, value):
    con = db_connect()
    con.execute("UPDATE refs SET warned=? WHERE key=?", (1 if value else 0, ref_key))
    con.commit()
    con.close()

def db_set_banned(ref_key, value):
    con = db_connect()
    con.execute("UPDATE refs SET banned=? WHERE key=?", (1 if value else 0, ref_key))
    con.commit()
    con.close()

def db_add_bonus(ref_key, amount):
    con = db_connect()
    con.execute("UPDATE refs SET bonus=bonus+? WHERE key=?", (amount, ref_key))
    con.commit()
    con.close()

def db_load_all():
    refs = {}
    link_to_ref = {}
    user_to_ref = {}
    warned = set()
    banned = set()
    con = db_connect()
    for row in con.execute("SELECT key, owner_id, first_name, invite_link, registered, bonus, warned, banned FROM refs"):
        key, owner_id, first_name, invite_link, registered, bonus, is_warned, is_banned = row
        joins_rows = con.execute("SELECT joined_at FROM joins WHERE ref_key=?", (key,)).fetchall()
        joins = [datetime.fromisoformat(r[0]) for r in joins_rows]
        refs[key] = {
            "owner_id": owner_id, "first_name": first_name,
            "invite_link": invite_link,
            "registered": datetime.fromisoformat(registered),
            "joins": joins, "bonus": bonus or 0.0,
        }
        link_to_ref[invite_link] = key
        user_to_ref[owner_id] = key
        if is_warned:
            warned.add(key)
        if is_banned:
            banned.add(key)
    con.close()
    return refs, link_to_ref, user_to_ref, warned, banned

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_level(n):
    if n < 15: return 1, 0.0
    elif n < 40: return 2, 0.50
    elif n < 60: return 3, 0.80
    else: return 4, 1.20

def calc_earnings(joins, bonus=0.0):
    n = len(joins)
    e = 0.0
    e += max(0, min(n, 39) - 14) * 0.50
    e += max(0, min(n, 59) - 39) * 0.80
    e += max(0, n - 59) * 1.20
    return round(e + bonus, 2)

def calc_period(joins, days):
    since = datetime.now() - timedelta(days=days)
    filtered = [j for j in joins if j >= since]
    n_before = len(joins) - len(filtered)
    e = 0.0
    for i in range(len(filtered)):
        pos = n_before + i
        if pos < 14: rate = 0.0
        elif pos < 39: rate = 0.50
        elif pos < 59: rate = 0.80
        else: rate = 1.20
        e += rate
    return round(e, 2), len(filtered)

def is_admin(user_id):
    return user_id == ADMIN_ID

# ─── KEYBOARDS ────────────────────────────────────────────────────────────────

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 My referral link", callback_data="my_link")],
        [InlineKeyboardButton("📊 My stats", callback_data="stats_menu")],
        [InlineKeyboardButton("🏆 Levels", callback_data="levels")],
        [InlineKeyboardButton("📋 Rules", callback_data="rules")],
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

def payout_kb(key):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Chat", url=f"tg://user?id={refs[key]['owner_id']}"),
            InlineKeyboardButton("⚠️ Warn", callback_data=f"warn_{key}"),
            InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{key}"),
        ]
    ])

def profile_text(user_id, name):
    if user_id in user_to_ref:
        key = user_to_ref[user_id]
        if key in banned:
            return f"🚫 *{name}*, your account has been banned.\nContact admin: @{ADMIN_USERNAME}"
        d = refs[key]
        n = len(d["joins"])
        lvl, rate = get_level(n)
        earned = calc_earnings(d["joins"], d.get("bonus", 0))
        reg = d["registered"].strftime("%d.%m.%Y")
        warn_text = "\n⚠️ *You have a warning. Next violation = ban.*" if key in warned else ""
        return (
            f"👋 Hey, *{name}*!\n\n"
            f"📅 Registered: {reg}\n"
            f"✅ Total joins: {n}\n"
            f"📈 Level: {lvl} ({rate}€/join)\n"
            f"💰 Total earned: {earned}€"
            f"{warn_text}"
        )
    else:
        return (
            f"👋 Hey, *{name}*!\n\n"
            f"Welcome to Vante Affiliate Program.\n"
            f"Press the button below to get your referral link!"
        )

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        profile_text(user.id, user.first_name),
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = user.id

    if query.data == "back":
        await query.edit_message_text(
            profile_text(uid, user.first_name),
            parse_mode="Markdown", reply_markup=main_kb()
        )

    elif query.data == "rules":
        await query.edit_message_text(RULES_TEXT, parse_mode="Markdown", reply_markup=back_kb())

    elif query.data == "levels":
        await query.edit_message_text(
            "🏆 *Levels & Rates*\n\n"
            "1️⃣ *Level 1* — 0–14 joins → 0€/join *(trial)*\n"
            "2️⃣ *Level 2* — 15–39 joins → 0.50€/join\n"
            "3️⃣ *Level 3* — 40–59 joins → 0.80€/join\n"
            "4️⃣ *Level 4* — 60+ joins → 1.20€/join\n\n"
            "The more people join via your link — the higher your rate!",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif query.data == "my_link":
        key = user_to_ref.get(uid)
        if key and key in banned:
            await query.edit_message_text("🚫 You are banned. Contact admin.", reply_markup=back_kb())
            return
        if key:
            link = refs[key]["invite_link"]
            await query.edit_message_text(
                f"🔗 *Your referral link:*\n\n`{link}`\n\nShare it and earn for every join!",
                parse_mode="Markdown", reply_markup=back_kb()
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
                await query.edit_message_text(f"❌ Error: {e}", reply_markup=back_kb())
                return
            key = str(uid)
            now = datetime.now()
            refs[key] = {"owner_id": uid, "first_name": user.first_name,
                         "invite_link": invite_link, "registered": now, "joins": [], "bonus": 0.0}
            link_to_ref[invite_link] = key
            user_to_ref[uid] = key
            db_save_ref(key, uid, user.first_name, invite_link, now)
            await query.edit_message_text(
                f"✅ *Your referral link is ready:*\n\n`{invite_link}`\n\nShare it and earn for every join!",
                parse_mode="Markdown", reply_markup=back_kb()
            )

    elif query.data == "stats_menu":
        if uid not in user_to_ref:
            await query.edit_message_text("⚠️ Get your referral link first!", reply_markup=back_kb())
            return
        await query.edit_message_text("📊 Choose period:", reply_markup=stats_kb())

    elif query.data in ("s7", "s14", "s30", "s365"):
        if uid not in user_to_ref:
            return
        days_map = {"s7": 7, "s14": 14, "s30": 30, "s365": 365}
        label_map = {"s7": "7 days", "s14": "14 days", "s30": "Month", "s365": "Year"}
        days = days_map[query.data]
        label = label_map[query.data]
        key = user_to_ref[uid]
        joins = refs[key]["joins"]
        earned_period, count_period = calc_period(joins, days)
        total = len(joins)
        total_earned = calc_earnings(joins, refs[key].get("bonus", 0))
        lvl, rate = get_level(total)
        await query.edit_message_text(
            f"📊 *Stats — last {label}*\n\n"
            f"✅ Joins: {count_period}\n"
            f"💰 Earned: {earned_period}€\n\n"
            f"*All time:*\n"
            f"✅ Total joins: {total}\n"
            f"📈 Level: {lvl} ({rate}€/join)\n"
            f"💰 Total earned: {total_earned}€",
            parse_mode="Markdown", reply_markup=stats_kb()
        )

    # Admin warn/ban buttons
    elif query.data.startswith("warn_") and is_admin(uid):
        key = query.data[5:]
        if key not in refs:
            await query.answer("Partner not found", show_alert=True)
            return
        if key in warned:
            # Second violation = ban
            banned.add(key)
            warned.discard(key)
            db_set_banned(key, True)
            db_set_warned(key, False)
            try:
                await context.bot.send_message(
                    chat_id=refs[key]["owner_id"],
                    text="🚫 *You have been banned* from Vante Affiliate Program due to repeated violations.",
                    parse_mode="Markdown"
                )
            except:
                pass
            await query.answer("Banned!", show_alert=True)
        else:
            warned.add(key)
            db_set_warned(key, True)
            refs[key]["joins"] = []
            db_clear_joins(key)
            try:
                await context.bot.send_message(
                    chat_id=refs[key]["owner_id"],
                    text="⚠️ *Warning!*\n\nSuspicious activity detected. Your stats have been reset.\nNext violation will result in a permanent ban.",
                    parse_mode="Markdown"
                )
            except:
                pass
            await query.answer("Warned + stats reset!", show_alert=True)

    elif query.data.startswith("ban_") and is_admin(uid):
        key = query.data[4:]
        if key not in refs:
            await query.answer("Partner not found", show_alert=True)
            return
        banned.add(key)
        warned.discard(key)
        db_set_banned(key, True)
        try:
            await context.bot.send_message(
                chat_id=refs[key]["owner_id"],
                text="🚫 *You have been banned* from Vante Affiliate Program.",
                parse_mode="Markdown"
            )
        except:
            pass
        await query.answer("Banned!", show_alert=True)

# ─── ADMIN COMMANDS ────────────────────────────────────────────────────────────

async def payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not refs:
        await update.message.reply_text("No partners yet.")
        return
    now = datetime.now()
    days_this_month = now.day
    for key, d in refs.items():
        if key in banned:
            continue
        joins = d["joins"]
        earned_month, count_month = calc_period(joins, days_this_month)
        total_earned = calc_earnings(joins, d.get("bonus", 0))
        lvl, rate = get_level(len(joins))
        warn_tag = "⚠️ " if key in warned else ""
        text = (
            f"{warn_tag}👤 *{d['first_name']}*\n"
            f"✅ Joins this month: {count_month}\n"
            f"💰 To pay this month: *{earned_month}€*\n"
            f"📈 Level: {lvl} | Total earned: {total_earned}€"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=payout_kb(key))

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    text = " ".join(context.args)
    sent = 0
    for key, d in refs.items():
        if key in banned:
            continue
        try:
            await context.bot.send_message(chat_id=d["owner_id"], text=f"📢 *Announcement:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            pass
    await update.message.reply_text(f"✅ Sent to {sent} partners.")

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not refs:
        await update.message.reply_text("No partners yet.")
        return
    now = datetime.now()
    days_this_month = now.day
    sorted_refs = sorted(refs.items(), key=lambda x: calc_period(x[1]["joins"], days_this_month)[1], reverse=True)
    text = "🏆 *Top Partners This Month:*\n\n"
    for i, (key, d) in enumerate(sorted_refs[:10], 1):
        _, count = calc_period(d["joins"], days_this_month)
        earned, _ = calc_period(d["joins"], days_this_month)
        text += f"{i}. *{d['first_name']}* — {count} joins | {earned}€\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def partnerinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /partnerinfo <@username or first_name>")
        return
    search = context.args[0].lower()
    found = None
    for key, d in refs.items():
        if d["first_name"].lower() == search or str(d["owner_id"]) == search:
            found = (key, d)
            break
    if not found:
        await update.message.reply_text("Partner not found.")
        return
    key, d = found
    joins = d["joins"]
    total = len(joins)
    earned = calc_earnings(joins, d.get("bonus", 0))
    lvl, rate = get_level(total)
    _, month_count = calc_period(joins, datetime.now().day)
    status = "🚫 Banned" if key in banned else ("⚠️ Warned" if key in warned else "✅ Active")
    await update.message.reply_text(
        f"👤 *Partner Info: {d['first_name']}*\n\n"
        f"🆔 ID: `{d['owner_id']}`\n"
        f"📅 Registered: {d['registered'].strftime('%d.%m.%Y')}\n"
        f"✅ Total joins: {total}\n"
        f"📅 This month: {month_count}\n"
        f"📈 Level: {lvl} ({rate}€/join)\n"
        f"💰 Total earned: {earned}€\n"
        f"🎁 Bonus: {d.get('bonus', 0)}€\n"
        f"📊 Status: {status}\n"
        f"🔗 Link: `{d['invite_link']}`",
        parse_mode="Markdown"
    )

async def addbonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbonus <first_name> <amount>")
        return
    search = context.args[0].lower()
    try:
        amount = float(context.args[1])
    except:
        await update.message.reply_text("Amount must be a number.")
        return
    found = None
    for key, d in refs.items():
        if d["first_name"].lower() == search or str(d["owner_id"]) == search:
            found = (key, d)
            break
    if not found:
        await update.message.reply_text("Partner not found.")
        return
    key, d = found
    d["bonus"] = d.get("bonus", 0) + amount
    db_add_bonus(key, amount)
    try:
        await context.bot.send_message(
            chat_id=d["owner_id"],
            text=f"🎁 *Bonus added!*\n\n+{amount}€ has been added to your account by admin.",
            parse_mode="Markdown"
        )
    except:
        pass
    await update.message.reply_text(f"✅ Added {amount}€ bonus to {d['first_name']}.")

async def allstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not refs:
        await update.message.reply_text("No partners yet.")
        return
    text = "📋 *All partners:*\n\n"
    for key, d in refs.items():
        n = len(d["joins"])
        earned = calc_earnings(d["joins"], d.get("bonus", 0))
        lvl, _ = get_level(n)
        status = "🚫" if key in banned else ("⚠️" if key in warned else "✅")
        text += f"{status} *{d['first_name']}* — {n} joins | Lvl {lvl} | {earned}€\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    invite_link = request.invite_link.invite_link if request.invite_link else None
    user = request.from_user
    try:
        await context.bot.approve_chat_join_request(chat_id=request.chat.id, user_id=user.id)
    except Exception as e:
        print(f"Approve error: {e}")
        return
    if not invite_link or invite_link not in link_to_ref:
        return
    key = link_to_ref[invite_link]
    if key in banned:
        return
    d = refs[key]
    if user.id == d["owner_id"]:
        return
    if db_has_joined(key, user.id):
        return
    db_add_join(key, user.id)
    d["joins"].append(datetime.now())
    n = len(d["joins"])
    lvl, rate = get_level(n)
    earned = calc_earnings(d["joins"], d.get("bonus", 0))
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 *New join!*\n\n👤 Partner: *{d['first_name']}*\n✅ Total joins: {n}\n📈 Level: {lvl} ({rate}€/join)\n💰 Total earned: {earned}€",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Admin notify error: {e}")
    try:
        await context.bot.send_message(
            chat_id=d["owner_id"],
            text=f"🎉 *New join via your link!*\n\n✅ Total joins: {n}\n📈 Level: {lvl} ({rate}€/join)\n💰 Total earned: {earned}€",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Partner notify error: {e}")

# ─── MONTHLY REPORT ────────────────────────────────────────────────────────────

async def send_monthly_report(context):
    now = datetime.now()
    days_this_month = now.day
    for key, d in refs.items():
        if key in banned:
            continue
        joins = d["joins"]
        earned_month, count_month = calc_period(joins, days_this_month)
        total = len(joins)
        total_earned = calc_earnings(joins, d.get("bonus", 0))
        lvl, rate = get_level(total)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Write to admin", url=f"https://t.me/{ADMIN_USERNAME}")]])
        try:
            await context.bot.send_message(
                chat_id=d["owner_id"],
                text=(
                    f"📅 *Monthly Report*\n\n"
                    f"✅ Joins this month: {count_month}\n"
                    f"💰 Earned this month: {earned_month}€\n\n"
                    f"*All time:*\n"
                    f"✅ Total joins: {total}\n"
                    f"📈 Level: {lvl} ({rate}€/join)\n"
                    f"💰 Total earned: {total_earned}€\n\n"
                    f"✍️ Write to admin to receive your payout!"
                ),
                parse_mode="Markdown", reply_markup=kb
            )
        except Exception as e:
            print(f"Monthly report error for {key}: {e}")

    text = "📊 *Monthly Report — All Partners*\n\n"
    total_all = 0
    earned_all = 0.0
    for key, d in refs.items():
        if key in banned:
            continue
        joins = d["joins"]
        earned_month, count_month = calc_period(joins, days_this_month)
        total_all += count_month
        earned_all += earned_month
        text += f"• *{d['first_name']}* — {count_month} joins | {earned_month}€\n"
    text += f"\n*Total: {total_all} joins | {round(earned_all, 2)}€*"
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"Admin monthly report error: {e}")

async def check_monthly_report(context):
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    if tomorrow.day == 1 and now.hour == 20 and now.minute == 0:
        await send_monthly_report(context)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    global refs, link_to_ref, user_to_ref, warned, banned
    logging.basicConfig(level=logging.INFO)
    db_init()
    refs, link_to_ref, user_to_ref, warned, banned = db_load_all()
    print(f"Loaded {len(refs)} partners from database.")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("allstats", allstats))
    app.add_handler(CommandHandler("payout", payout))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("partnerinfo", partnerinfo))
    app.add_handler(CommandHandler("addbonus", addbonus))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    # job_queue disabled

    print("Bot is running!")
    app.run_polling(allowed_updates=["message", "chat_join_request", "callback_query"])

if __name__ == "__main__":
    main()
