import logging
import os
import threading
import psycopg2
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
import google.generativeai as genai

# ==============================================================================
# 0. FLASK WEB SERVER
# ==============================================================================
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "✅ አል-ኑር መድኃኒት አፋላጊ ቦት በስኬት እየሰራ ይገኛል!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# ==============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable ውስጥ አልተገኘም። እባክዎ ያስቀምጡት።")

LOGO_FILE_ID = "AgACAgQAAxkBAAEszTBqZGhpfKNE12Y948HvU4JhQHfZrQAC0g1rG4xKIFPy4FmrrNxjRAEAAwIAA3gAAz0E"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    ai_model = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

def get_db_connection():
    if DATABASE_URL:
        db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
        return psycopg2.connect(db_url)
    else:
        import sqlite3
        return sqlite3.connect("pharmacy_bot.db")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    if DATABASE_URL:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pharmacies (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                phone TEXT NOT NULL,
                operating_hours TEXT DEFAULT 'ያልተጠቀሰ',
                license_photo TEXT,
                is_verified INTEGER DEFAULT 0
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pharmacies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                phone TEXT NOT NULL,
                operating_hours TEXT DEFAULT 'ያልተጠቀሰ',
                license_photo TEXT,
                is_verified INTEGER DEFAULT 0
            )
        """)
    conn.commit()
    conn.close()

def register_pharmacy_db(chat_id, name, location, phone, operating_hours, license_photo):
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute("""
            INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified)
            VALUES (%s, %s, %s, %s, %s, %s, 0) RETURNING id
        """, (chat_id, name, location, phone, operating_hours, license_photo))
        pharmacy_id = cursor.fetchone()[0]
    else:
        cursor.execute("""
            INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (chat_id, name, location, phone, operating_hours, license_photo))
        pharmacy_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return pharmacy_id

def verify_pharmacy_db(pharmacy_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"UPDATE pharmacies SET is_verified = 1 WHERE id = {placeholder}", (pharmacy_id,))
    conn.commit()
    conn.close()

def get_pharmacy_info_by_id(pharmacy_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT name, location, phone, chat_id, operating_hours FROM pharmacies WHERE id = {placeholder}", (pharmacy_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_verified_pharmacies_by_location(location=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if location:
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT chat_id FROM pharmacies WHERE is_verified = 1 AND LOWER(location) LIKE LOWER({placeholder})", (f"%{location}%",))
    else:
        cursor.execute("SELECT chat_id FROM pharmacies WHERE is_verified = 1")
    rows = cursor.fetchall()
    conn.close()
    return list(set([r[0] for r in rows]))

def get_all_verified_pharmacies():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, location, phone, operating_hours FROM pharmacies WHERE is_verified = 1")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_bot_statistics():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pharmacies")
    total_pharmacies = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM pharmacies WHERE is_verified = 1")
    verified_pharmacies = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM pharmacies WHERE is_verified = 0")
    pending_pharmacies = cursor.fetchone()[0]
    conn.close()
    return total_pharmacies, verified_pharmacies, pending_pharmacies

# ==============================================================================
# 2. STATES & KEYBOARDS
# ==============================================================================
WAITING_FOR_SEARCH = 1
WAITING_FOR_PRICE = 2
WAITING_FOR_LOCATION_SET = 3
WAITING_FOR_MED_INFO = 4

REG_NAME = 10
REG_LOCATION = 11
REG_PHONE = 12
REG_HOURS = 14
REG_LICENSE = 13

MAIN_KEYBOARD = [
    ["🔍 መድኃኒት ፈልግ", "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ"],
    ["📍 አካባቢ ምረጥ", "📋 የፋርማሲዎች ዝርዝር"],
    ["🏥 ፋርማሲ መዝግብ", "📞 እገዛ / ድጋፍ"],
    ["🏠 ወደ ዋና ገጽ"]
]

LOCATION_KEYBOARD = [
    ["ቦሌ", "አራዳ", "አዲስ ከተማ"],
    ["የካ", "ቂርቆስ", "ልደታ"],
    ["ኮልፌ ቀራኒዮ", "ንፋስ ስልክ", "አቃቂ ቃሊቲ"],
    ["🏠 ወደ ዋና ገጽ"]
]

HOURS_KEYBOARD = [
    ["🕒 24 ሰዓት ክፍት"],
    ["☀️ በቀን ብቻ (ከጠዋቱ 2:00 - ማታ 2:00)"],
    ["🏠 ወደ ዋና ገጽ"]
]

# ==============================================================================
# 3. HANDLERS & LOGIC
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "ወዳጄ"

    welcome_text = (
        f"👋 ሰላም **{user_name}**! ወደ **አል-ኑር መድኃኒት አፋላጊ** በደህና መጡ።\n\n"
        f"━━━━━━━ ⚖️ **ሕጋዊ ማስታወቂያ** ━━━━━━━\n"
        f"• 🏥 ከሕጋዊና ፈቃድ ካላቸው ፋርማሲዎች ጋር ብቻ ያገናኛል።\n"
        f"• 📄 መድኃኒት ሲገዙ የሐኪም ማዘዣ (Prescription) ይያዙ።\n"
        f"• ℹ️ ይህ ቦት የመረጃ ማገናኛ እንጂ ሕክምና አይሰጥም።\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 **የሚፈልጉትን አገልግሎት ከታች ይምረጡ፦**"
    )

    if update.message:
        try:
            await update.message.reply_photo(
                photo=LOGO_FILE_ID,
                caption=welcome_text,
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
            )
        except Exception as e:
            logging.error(f"ፎቶ መጫን አልተቻለም፦ {e}")
            await update.message.reply_text(
                welcome_text,
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
            )
    return ConversationHandler.END

# ----------------- AI የመድኃኒት መረጃ ማብራሪያ SECTION -----------------
async def prompt_med_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 **ስለ ታዘዘልዎት መድኃኒት መረጃ ማወቂያ**\n\n"
        "እባክዎ ስለ መድኃኒቱ መረጃ ለማግኘት፦\n"
        "1. **የመድኃኒቱን ስም በጽሑፍ** ይጻፉልን፡ ወይም\n"
        "2. **የሐኪም ማዘዣውን (Prescription) ፎቶ** አንስተው ይላኩልን።\n\n"
        "🤖 *AI መድኃኒቱ ለምን እንደሚያገለግል፣ አወሳሰዱን እና ጥንቃቄዎችን ያብራራልዎታል።*",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return WAITING_FOR_MED_INFO

async def analyze_med_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return WAITING_FOR_MED_INFO

    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)

    if not ai_model:
        await msg.reply_text(
            "⚠️ የ AI አገልግሎቱ ለጊዜው አልተዋቀረም። እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return ConversationHandler.END

    await msg.reply_text("⏳ **መረጃው በ AI በመተንተን ላይ ነው... እባክዎ ትንሽ ይጠብቁ...**")

    prompt = (
        "እባክህ የዚህን መድኃኒት ወይም የሐኪም ማዘዣ ፎቶ መዝገብ ተንትነህ በሚከተለው መልኩ በአማርኛ አብራራ፦\n"
        "1. የመድኃኒቱ ስም (Medication Name)\n"
        "2. ዋነኛ ጥቅም (Primary Usage)\n"
        "3. አወሳሰድ እና ጥንቃቄዎች (Dosage & Precautions)\n"
        "4. ሊከሰቱ የሚችሉ የጎንዮሽ ጉዳቶች (Side Effects)\n\n"
        "ማስታወሻ፦ መረጃው ለግንዛቤ ብቻ እንደሆነ እና የሐኪም ምክርን እንደማይተካ በጥሩ ስነ-ምግባር ግለጽ።"
    )

    try:
        if msg.photo:
            photo_file = await msg.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image_part = {"mime_type": "image/jpeg", "data": bytes(photo_bytes)}
            response = ai_model.generate_content([prompt, image_part])

        elif msg.text:
            response = ai_model.generate_content(f"{prompt}\n\nየመድኃኒቱ ስም፦ {msg.text}")
        else:
            await msg.reply_text(
                "❌ የላኩት ግብዓት ስላልገባኝ ድጋሚ ይሞክሩ።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return ConversationHandler.END

        await msg.reply_text(
            f"💡 **የመድኃኒት መረጃ ማብራሪያ፦**\n\n{response.text}\n\n"
            f"⚠️ *ማስታወሻ፦ ይህ መረጃ በ AI የተዘጋጀ ለግንዛቤ ብቻ የሚያገለግል ነው። ሁልጊዜ የሐኪምዎን ወይም የፋርማሲስቱን መመሪያ ይከተሉ።*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
    except Exception as e:
        logging.error(f"AI error: {e}")
        await msg.reply_text(
            "❌ መረጃውን መተንተን አልተቻለም። እባክዎ የምስሉ ጥራት ጥሩ መሆኑን ያረጋግጡ ወይም የመድኃኒቱን ስም በጽሑፍ ይጻፉልን።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

    return ConversationHandler.END

# ----------------- ADMIN & OTHERS -----------------
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ ይቅርታ! ይህንን ትዕዛዝ መጠቀም የሚችለው አድሚኑ ብቻ ነው።")
        return

    total, verified, pending = get_bot_statistics()
    stats_text = (
        f"📊 **የቦቱ ስታቲስቲክስ እና መረጃ (PostgreSQL)**\n\n"
        f"🏥 **ጠቅላላ የተመዘገቡ ፋርማሲዎች፦** {total}\n"
        f"✅ **የተረጋገጡ (ሕጋዊ) ፋርማሲዎች፦** {verified}\n"
        f"⏳ **ማረጋገጫ የሚጠብቁ (Pending)፦** {pending}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 *አል-ኑር መድኃኒት አፋላጊ ሲስተም*"
    )
    await update.message.reply_text(stats_text, parse_mode="Markdown")

async def list_pharmacies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pharmacies = get_all_verified_pharmacies()
    if not pharmacies:
        await update.message.reply_text(
            "ℹ️ በአሁኑ ሰዓት የተረጋገጡ ሕጋዊ ፋርማሲዎች አልተገኙም።\n(አዲስ ከተመዘገቡ በአድሚን 'Approve' መደረጋቸውን ያረጋግጡ)",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return

    text = "🏥 **የተመዘገቡ ሕጋዊ ፋርማሲዎች ዝርዝር፦**\n\n"
    for idx, (name, loc, phone, hours) in enumerate(pharmacies, 1):
        text += f"{idx}. **{name}**\n"
        text += f"   📍 አካባቢ፦ {loc}\n"
        text += f"   📞 ስልክ፦ {phone}\n"
        text += f"   🕒 የስራ ሰዓት፦ {hours}\n"
        text += "────────────────────\n"

    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def select_location_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_loc = context.user_data.get("user_location", "አልተመረጠም")
    await update.message.reply_text(
        f"📍 **የአካባቢ መምረጫ**\n\n"
        f"አሁን የተመረጠው አካባቢ፦ **{current_loc}**\n\n"
        f"እባክዎ የሚገኙበትን ወይም የሚቀርብዎትን ክፍለ ከተማ ከታች ካሉት አዝራሮች ይምረጡ ወይም ይጻፉልን፦",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True),
    )
    return WAITING_FOR_LOCATION_SET

async def save_user_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)

    selected_loc = msg.text
    context.user_data["user_location"] = selected_loc
    await msg.reply_text(
        f"✅ አካባቢዎ በስኬት ወደ **'{selected_loc}'** ተቀይሯል!\n\n"
        f"አሁን መድኃኒት ሲፈልጉ ጥያቄዎ በቅድሚያ ለ**{selected_loc}** አካባቢ ፋርማሲዎች ይላካል።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )
    return ConversationHandler.END

async def handle_customer_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return ConversationHandler.END

    # 🛡️ የሜኑ አዝራር ከሆነ በሌላ ቦታ ይታከላል
    # (ነገር ግን እዚህ ላይ እንደ ጥበቃ እንቆይ)
    if msg.text and msg.text in MENU_BUTTONS:
        return await handle_menu_buttons(update, context)

    user = update.effective_user
    user_loc = context.user_data.get('user_location')
    verified_pharmacies = get_verified_pharmacies_by_location(user_loc) if user_loc else []
    if not verified_pharmacies:
        verified_pharmacies = get_verified_pharmacies_by_location(None)

    keyboard = [[
        InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{user.id}"),
        InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{user.id}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    target_chats = verified_pharmacies if verified_pharmacies else [msg.chat_id]

    photo_file_id = msg.photo[-1].file_id if msg.photo else (msg.document.file_id if msg.document else None)
    is_doc = True if msg.document else False
    loc_tag = f" (አካባቢ፦ {user_loc})" if user_loc else ""

    if photo_file_id:
        await msg.reply_text(
            f"✅ የሐኪም ማዘዣ ፎቶዎ ተቀብለናል! ለ{len(target_chats)} ሕጋዊ ፋርማሲዎች ጥያቄው ተልኳል።{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        for chat_id in target_chats:
            try:
                caption = f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ (በፎቶ)!**\nከደንበኛ፡ {user.first_name}\n📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n\nመድኃኒቱ አለዎት?"
                if is_doc:
                    await context.bot.send_document(chat_id=chat_id, document=photo_file_id, caption=caption, reply_markup=reply_markup)
                else:
                    await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=caption, reply_markup=reply_markup)
            except Exception as e:
                logging.error(f"Pharmacy notify error: {e}")
    elif msg.text:
        med_name = msg.text
        await msg.reply_text(
            f"✅ የመድኃኒት ስም '{med_name}' ተቀብለናል! ለ{len(target_chats)} ሕጋዊ ፋርማሲዎች እየተላከ ነው...{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        for chat_id in target_chats:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ!**\nየተፈለገው መድኃኒት፡ **{med_name}**\n📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n\nመድኃኒቱ አለዎት?",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.error(f"Pharmacy notify error: {e}")

    return ConversationHandler.END
# 🛑 የተስተካከለው ConversationHandler
search_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), prompt_search)],
    states={
        WAITING_FOR_SEARCH: [
            # ተጠቃሚው ሌላ ሜኑ አዝራር ከነካ አዲስ ሀንድለር እንዲጀምር ይደረጋል
            MessageHandler(MENU_BUTTONS_REGEX, start),
            MessageHandler(filters.ALL & ~filters.COMMAND, handle_customer_request)
        ]
    },
    fallbacks=[CommandHandler("start", start)]
)

async def handle_pharmacy_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # 🔴 BUG FIX:
    # callback_data "not_available_123" ን በ split("_") ስንከፍለው 3 ክፍሎች ይፈጠራሉ
    # ("not", "available", "123") ስለዚህ unpack ማድረግ ላይ ValueError ያመጣል እና ቦቱ ይሰበራል።
    # rsplit("_", 1) በመጠቀም ከመጨረሻው አንድ ጊዜ ብቻ በመክፈል ትክክለኛውን action እና customer_id እናገኛለን።
    action, customer_id = data.rsplit("_", 1)
    context.chat_data["target_customer_id"] = customer_id

    if action == "available":
        msg_text = (
            "✅ **'መድኃኒቱ አለኝ' የሚለው ምላሽዎ ተመዝግቧል!**\n\n"
            "እባክዎ የመድኃቶቹን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።\n\n"
            "**ምሳሌ አጻጻፍ፦**\n• አሞክሳሲሊን - 150 ብር\n• ፓራሲታሞል - 50 ብር"
        )
        try:
            await query.edit_message_caption(caption=msg_text)
        except Exception:
            await query.edit_message_text(text=msg_text)
        return WAITING_FOR_PRICE
    elif action == "not_available":
        try:
            await query.edit_message_caption(caption="❌ 'የለኝም' የሚለው ምላሽዎ ተመዝግቧል።")
        except Exception:
            await query.edit_message_text(text="❌ 'የለኝም' የሚለው ምላሽዎ ተመዝግቧል።")
        return ConversationHandler.END

async def receive_price_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)

    price_details = msg.text
    customer_id = context.chat_data.get("target_customer_id")
    pharmacy_chat_id = msg.chat_id

    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT name, location, phone, operating_hours FROM pharmacies WHERE chat_id = {placeholder} ORDER BY id DESC LIMIT 1", (pharmacy_chat_id,))
    pharm_info = cursor.fetchone()
    conn.close()

    pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
    pharm_loc = pharm_info[1] if pharm_info else "ያልተጠቀሰ"
    pharm_phone = pharm_info[2] if pharm_info else "ያልተጠቀሰ"
    pharm_hours = pharm_info[3] if pharm_info and pharm_info[3] else "ያልተጠቀሰ"

    await msg.reply_text("✅ ዋጋው እና መረጃው ለደንበኛው በስኬት ተልኳል!", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

    if customer_id:
        try:
            await context.bot.send_message(
                chat_id=int(customer_id),
                text=f"🎉 **የመድኃኒት መረጃ ከ{pharm_name} ተገኘ!**\n\n"
                     f"🏥 **ፋርማሲ፦** {pharm_name}\n"
                     f"📍 **አካባቢ፦** {pharm_loc}\n"
                     f"📞 **ስልክ፦** {pharm_phone}\n"
                     f"🕒 **የስራ ሰዓት፦** {pharm_hours}\n\n"
                     f"💰 **የዋጋ እና የዝርዝር መረጃ፦**\n{price_details}\n\n"
                     f"📄 *እባክዎ በአካል ሲሄዱ የሐኪም ማዘዣ (Prescription) መያዝዎን አይረሱ!*",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
            )
        except Exception as e:
            logging.error(f"ለደንበኛው {customer_id} መላክ አልተቻለም፦ {e}")

    return ConversationHandler.END

async def handle_admin_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("verify_"):
        pharmacy_id = int(data.split("_")[1])
        verify_pharmacy_db(pharmacy_id)
        pharm_info = get_pharmacy_info_by_id(pharmacy_id)
        pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
        pharm_chat_id = pharm_info[3] if pharm_info else None

        try:
            await query.edit_message_caption(caption=f"✅ **'{pharm_name}' በስኬት ተረጋግጧል!** (ID: {pharmacy_id})")
        except Exception:
            await query.edit_message_text(text=f"✅ **'{pharm_name}' በስኬት ተረጋግጧል!** (ID: {pharmacy_id})")

        if pharm_chat_id:
            try:
                await context.bot.send_message(chat_id=pharm_chat_id, text=f"🎉 **እንኳን ደስ አለዎት!**\n\nየፋርማሲዎት (**{pharm_name}**) ምዝገባ በአድሚኑ ተረጋግጧል።")
            except Exception as e:
                logging.error(f"ለፋርማሲው ማሳወቂያ መላክ አልተቻለም፦ {e}")

async def start_pharmacy_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏥 **የፋርማሲ መመዝገቢያ ክፍል**\n\nእባክዎ የፋርማሲዎን **ሙሉ ስም** ያስገቡ፦",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return REG_NAME

async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_name"] = msg.text
    await msg.reply_text(
        "📍 ፋርማሲዎ የሚገኝበትን **ክፍለ ከተማ / አካባቢ** ከታች ይምረጡ ወይም ይጻፉ፦",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
    )
    return REG_LOCATION

async def reg_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_location"] = msg.text
    await msg.reply_text("📞 ደንበኞች የሚያገኙበትን **የፋርማሲ የስልክ ቁጥር** ያስገቡ፦", reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    return REG_PHONE

async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_phone"] = msg.text
    await msg.reply_text("🕒 **የፋርማሲዎ የስራ ሰዓት መቼ ነው?**", reply_markup=ReplyKeyboardMarkup(HOURS_KEYBOARD, resize_keyboard=True))
    return REG_HOURS

async def reg_get_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_hours"] = msg.text
    await msg.reply_text("📄 **የንግድ ፈቃድ ወይም የመድኃኒት መሸጫ ፈቃድ ፎቶ** አንስተው ይላኩልን፦", reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    return REG_LICENSE

async def reg_get_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or (msg.text and msg.text == "🏠 ወደ ዋና ገጽ"):
        return await start(update, context)

    photo_file_id = msg.photo[-1].file_id if msg.photo else (msg.document.file_id if msg.document else None)
    is_doc = True if msg.document else False

    if not photo_file_id:
        await msg.reply_text("❌ እባክዎ የንግድ ፈቃዱን **በፎቶ መልኩ** ያያይዙልን።")
        return REG_LICENSE

    chat_id = msg.chat_id
    name = context.user_data.get("pharm_name", "ያልተጠቀሰ")
    location = context.user_data.get("pharm_location", "ያልተጠቀሰ")
    phone = context.user_data.get("pharm_phone", "ያልተጠቀሰ")
    hours = context.user_data.get("pharm_hours", "ያልተጠቀሰ")

    pharm_id = register_pharmacy_db(chat_id, name, location, phone, hours, photo_file_id)
    await msg.reply_text("📝 **የምዝገባ ጥያቄዎ ተቀብለናል!**\n\n⏳ የላኩት የንግድ ፈቃድ በአድሚን ተመርምሮ ሲረጋገጥ ማሳወቂያ ይደርስዎታል።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

    admin_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ፈቅድ (Approve)", callback_data=f"verify_{pharm_id}")]])
    caption_text = f"🔔 **አዲስ የፋርማሲ ምዝገባ ጥያቄ!**\n\n🏥 **ስም፦** {name}\n📍 **አካባቢ፦** {location}\n📞 **ስልክ፦** {phone}\n🕒 **የስራ ሰዓት፦** {hours}\n\nሕጋዊነቱን አረጋግጠው ይፍቀዱ፦"

    try:
        if is_doc:
            await context.bot.send_document(chat_id=ADMIN_CHAT_ID, document=photo_file_id, caption=caption_text, parse_mode="Markdown", reply_markup=admin_keyboard)
        else:
            await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=photo_file_id, caption=caption_text, parse_mode="Markdown", reply_markup=admin_keyboard)
    except Exception as e:
        logging.error(f"ለአድሚን ኖቲፊኬሽን መላክ አልተቻለም፦ {e}")

    return ConversationHandler.END

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 **አል-ኑር መድኃኒት አፋላጊ - የደንበኞች ድጋፍ**\n\n"
        "ማንኛውም ጥያቄ ወይም አስተያየት ካለዎት፦\n"
        "• **ስልክ፦** +251 911 00 00 00\n"
        "• **ቴሌግራም፦** @AlNoorSupport",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )

async def error_handler_func(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(msg="Exception while handling an update:", exc_info=context.error)

# ==============================================================================
# 4. MAIN FUNCTION
# ==============================================================================
def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    loc_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 አካባቢ ምረጥ$"), select_location_prompt)],
        states={WAITING_FOR_LOCATION_SET: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_location)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    )

    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), prompt_search)],
        states={WAITING_FOR_SEARCH: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.ALL, handle_customer_request)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    )

    med_info_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ$"), prompt_med_info)],
        states={WAITING_FOR_MED_INFO: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.ALL, analyze_med_info)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    )

    pharmacy_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_pharmacy_response, pattern="^(available_|not_available_)")],
        states={WAITING_FOR_PRICE: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
        per_message=False,
    )

    pharmacy_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏥 ፋርማሲ መዝግብ$"), start_pharmacy_reg)],
        states={
            REG_NAME: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_name)],
            REG_LOCATION: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_location)],
            REG_PHONE: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_phone)],
            REG_HOURS: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_hours)],
            REG_LICENSE: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.ALL, reg_get_license)],
        },
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(MessageHandler(filters.Regex("^📞 እገዛ / ድጋፍ$"), show_help))
    app.add_handler(MessageHandler(filters.Regex("^📋 የፋርማሲዎች ዝርዝር$"), list_pharmacies))
    app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^verify_"))

    app.add_handler(loc_conv)
    app.add_handler(search_conv)
    app.add_handler(med_info_conv)
    app.add_handler(pharmacy_reply_conv)
    app.add_handler(pharmacy_conv)

    # 🔴 ORDER FIX:
    # ይህ "🏠 ወደ ዋና ገጽ" ግሎባል handler ከ ConversationHandler-ዎቹ በፊት ተመዝግቦ ስለነበር፣
    # ተጠቃሚ በምዝገባ ወይም በሌላ ውይይት መሃል ላይ ሆኖ "🏠" ሲጫን ይህ ቀድሞ ይይዘውና
    # ውይይቱ በትክክል ሳይዘጋ (state stuck) ይቀር ነበር። አሁን ከ ConversationHandler-ዎቹ
    # በኋላ በመመዝገቡ፣ ንቁ ውይይት ካለ የራሱ fallback በትክክል ይይዘዋል፤ ውይይት ከሌለ ደግሞ
    # ይህ ግሎባል handler ይይዘዋል።
    app.add_handler(MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start))

    app.add_error_handler(error_handler_func)

    print("🤖 አል-ኑር መድኃኒት አፋላጊ ቦት በ PostgreSQL ዳታቤዝ ስራ ጀምሯል...")
    app.run_polling()

if __name__ == "__main__":
    main()
