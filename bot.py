import os
import sys
import logging
import sqlite3
import threading
import requests
from datetime import datetime
from flask import Flask, jsonify

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# ==============================================================================
# 1. LOGGING CONFIGURATION
# ==============================================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. CONFIGURATION & ENVIRONMENT VARIABLES
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", None)  # PostgreSQL if provided, else SQLite
DB_NAME = "alnoor_medicine.db"
LOGO_FILE_ID = os.getenv("LOGO_FILE_ID", None)

# Conversation States
(
    WAITING_FOR_SEARCH,
    WAITING_FOR_MED_INFO,
    WAITING_FOR_LOCATION_SET,
    REG_NAME,
    REG_LOCATION,
    REG_PHONE,
    REG_HOURS,
    REG_LICENSE,
    WAITING_FOR_ORDER_PRICE,
    WAITING_FOR_MEDICINE_REMINDER,
) = range(10)

# ==============================================================================
# 3. DATABASE INITIALIZATION & HELPERS
# ==============================================================================
def get_db_connection():
    """Returns a database connection (SQLite or PostgreSQL based on env)."""
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    """Creates required tables if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Pharmacies Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pharmacies (
            id INTEGER PRIMARY KEY AUTOINCREMENT if not DATABASE_URL else SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE,
            name TEXT,
            location TEXT,
            phone TEXT,
            operating_hours TEXT,
            license_photo TEXT,
            is_verified INTEGER DEFAULT 0
        )
    """)

    # Pharmacy Responses / Orders Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pharmacy_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT if not DATABASE_URL else SERIAL PRIMARY KEY,
            pharmacy_id INTEGER,
            customer_id BIGINT,
            medicine_name TEXT,
            status TEXT DEFAULT 'pending',
            response_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Search History Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT if not DATABASE_URL else SERIAL PRIMARY KEY,
            user_id BIGINT,
            query TEXT,
            result TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Reminders Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT if not DATABASE_URL else SERIAL PRIMARY KEY,
            user_id BIGINT,
            medicine_name TEXT,
            dosage TEXT,
            time_str TEXT
        )
    """)

    # AI Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT if not DATABASE_URL else SERIAL PRIMARY KEY,
            user_id BIGINT,
            action TEXT,
            payload TEXT,
            response TEXT,
            status_code INTEGER,
            elapsed_time REAL,
            error TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    logging.info("✅ Database tables initialized.")

# ==============================================================================
# 4. LOGGING & AI LOGGING HELPERS
# ==============================================================================
def log_ai_request(user_id, action, payload, response, status_code, elapsed_time, error=None):
    """Logs AI API calls into database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(
            f"""INSERT INTO ai_logs 
               (user_id, action, payload, response, status_code, elapsed_time, error) 
               VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})""",
            (user_id, action, str(payload), str(response), status_code, elapsed_time, str(error) if error else None)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to log AI request: {e}")

# ==============================================================================
# 5. KEYBOARDS & UI
# ==============================================================================
MAIN_KEYBOARD = [
    ["🔍 መድኃኒት ፈልግ", "📖 የመድኃኒት መረጃ"],
    ["📍 አካባቢ ምረጥ", "📋 ፋርማሲዎች"],
    ["🏥 ፋርማሲ መዝግብ", "📋 ትዕዛዞች"],
    ["📦 ክምችት", "💊 ማሳሰቢያ"],
    ["📞 ድጋፍ"]
]

LOCATION_KEYBOARD = [
    ["አራዳ", "ቦሌ", "ቂርቆስ"],
    ["ልደታ", "አዲስ ከተማ", "የካ"],
    ["ጉለሌ", "ኮልፌ ቀራኒዮ", "አካቂ ቃሊቲ"],
    ["ንፋስ ስልክ ላፍቶ", "ለሚ ኩራ"],
    ["🏠 ዋና ገጽ"]
]

HOURS_KEYBOARD = [
    ["24 ሰዓት (24 Hours)", "መደበኛ (8:00 AM - 8:00 PM)"],
    ["🏠 ዋና ገጽ"]
]

STOCK_KEYBOARD = [
    ["➕ አዲስ መድኃኒት ጨምር", "📋 የክምችት ዝርዝር"],
    ["🏠 ዋና ገጽ"]
]

# ==============================================================================
# 6. PHARMACY DATABASE HELPERS
# ==============================================================================
def register_pharmacy_db(chat_id, name, location, phone, operating_hours, license_photo):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(
        f"""INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo) 
           VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})""",
        (chat_id, name, location, phone, operating_hours, license_photo)
    )
    conn.commit()
    pharmacy_id = cursor.lastrowid
    conn.close()
    return pharmacy_id

def verify_pharmacy_db(pharmacy_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"UPDATE pharmacies SET is_verified = 1 WHERE id = {placeholder}", (pharmacy_id,))
    conn.commit()
    conn.close()

def delete_pharmacy_db(pharmacy_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"DELETE FROM pharmacies WHERE id = {placeholder}", (pharmacy_id,))
    conn.commit()
    conn.close()

def get_verified_pharmacies_by_location(location=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if location:
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT chat_id FROM pharmacies WHERE is_verified = 1 AND location = {placeholder}", (location,))
    else:
        cursor.execute("SELECT chat_id FROM pharmacies WHERE is_verified = 1")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_all_verified_pharmacies():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, location, phone, operating_hours FROM pharmacies WHERE is_verified = 1")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_pharmacy_info_by_chat_id(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT name, location, phone FROM pharmacies WHERE chat_id = {placeholder}", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_pharmacy_info_by_id(pharmacy_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT name, location, phone, chat_id FROM pharmacies WHERE id = {placeholder}", (pharmacy_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_pharmacy_id_by_chat_id(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {placeholder}", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# ==============================================================================
# 7. SEARCH & ORDER HELPERS
# ==============================================================================
def save_search_history(user_id, query, result):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(
        f"INSERT INTO search_history (user_id, query, result) VALUES ({placeholder}, {placeholder}, {placeholder})",
        (user_id, query, result)
    )
    conn.commit()
    conn.close()

def save_pharmacy_request(pharmacy_chat_id, customer_id, medicine_name):
    p_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    if not p_id:
        return False
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(
        f"INSERT INTO pharmacy_responses (pharmacy_id, customer_id, medicine_name) VALUES ({placeholder}, {placeholder}, {placeholder})",
        (p_id, customer_id, medicine_name)
    )
    conn.commit()
    conn.close()
    return True

def update_order_status(order_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(
        f"UPDATE pharmacy_responses SET status = {placeholder} WHERE id = {placeholder}",
        (status, order_id)
    )
    conn.commit()
    conn.close()

# ==============================================================================
# 8. USER REMINDERS HELPERS
# ==============================================================================
def get_user_medicine_reminders(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT id, medicine_name, dosage, time_str FROM reminders WHERE user_id = {placeholder}", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def save_user_reminder(user_id, medicine_name, dosage, time_str):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(
        f"INSERT INTO reminders (user_id, medicine_name, dosage, time_str) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})",
        (user_id, medicine_name, dosage, time_str)
    )
    conn.commit()
    conn.close()

# ==============================================================================
# 9. STATS & ADMIN HELPERS
# ==============================================================================
def get_bot_statistics():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pharmacies")
    total_pharmacies = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM pharmacies WHERE is_verified = 1")
    verified_pharmacies = cursor.fetchone()[0]
    pending_pharmacies = total_pharmacies - verified_pharmacies
    conn.close()
    return total_pharmacies, verified_pharmacies, pending_pharmacies

def get_ai_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), AVG(elapsed_time) FROM ai_logs WHERE status_code = 200")
    success_row = cursor.fetchone()
    total_success = success_row[0] or 0
    avg_time = round(success_row[1] or 0, 2)

    cursor.execute("SELECT COUNT(*) FROM ai_logs WHERE status_code != 200")
    total_errors = cursor.fetchone()[0] or 0

    conn.close()
    return {
        "total": total_success + total_errors,
        "successful": total_success,
        "errors": total_errors,
        "avg_time": avg_time
    }

# ==============================================================================
# 10. NOTIFICATION HELPERS & AUTOMATIC REMINDER JOB
# ==============================================================================
async def send_telegram_notification(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message: str):
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Failed to send notification to {chat_id}: {e}")

async def send_order_notification(context: ContextTypes.DEFAULT_TYPE, pharmacy_chat_id: int, customer_id: int, med_name: str, time_obj):
    msg = (
        f"🔔 **አዲስ የመድኃኒት ጥያቄ!**\n\n"
        f"💊 የመድኃኒት ስም: **{med_name}**\n"
        f"⏰ ሰዓት: {time_obj.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"እባክዎን በ '📋 ትዕዛዞች' ማውጫ ውስጥ ገብተው ዋጋ ይስጡ።"
    )
    await send_telegram_notification(context, pharmacy_chat_id, msg)

async def check_and_send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Checks database every minute and sends due medicine reminders to users."""
    now_str = datetime.now().strftime("%I:%M %p")  # Example: 08:00 AM / 02:30 PM
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT user_id, medicine_name, dosage FROM reminders WHERE time_str = {placeholder}", (now_str,))
        due_reminders = cursor.fetchall()
        conn.close()

        for r in due_reminders:
            user_id, med_name, dosage = r[0], r[1], r[2]
            msg = (
                f"⏰ **የመድኃኒት ማሳሰቢያ!**\n\n"
                f"💊 መድኃኒት: **{med_name}**\n"
                f"🥄 መጠን: **{dosage}**\n\n"
                f"እባክዎን መድኃኒትዎን በሰዓቱ ይውሰዱ!"
            )
            await send_telegram_notification(context, user_id, msg)
    except Exception as e:
        logging.error(f"Error checking reminders: {e}")

# ==============================================================================
# 12. FLASK KEEP-ALIVE SERVER
# ==============================================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({"status": "active", "service": "Al-Noor Medicine Finder Bot"}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ==============================================================================
# 13. TEXT CLEANING UTILITY
# ==============================================================================
def clean_translation(text: str) -> str:
    """Removes duplicate lines from input text."""
    lines = text.split("\n")
    unique_lines = []
    for line in lines:
        if line.strip() not in unique_lines:
            unique_lines.append(line.strip())
    return "\n".join(unique_lines)

# ==============================================================================
# 14. OPENROUTER / GEMINI AI INTEGRATION
# ==============================================================================
async def get_ai_medicine_info(medicine_name: str, user_id: int = 0) -> str:
    """Fetch detailed medical/pharmaceutical information using OpenRouter API."""
    start_time = datetime.now()
    if not OPENROUTER_API_KEY:
        return "⚠️ የአርቲፊሻል ኢንቴሊጀንስ አገልግሎት (OpenRouter API) አልተዋቀረም።"

    prompt = (
        f"ለሚከተለው መድኃኒት ማጠቃለያ እና ዝርዝር መረጃ በአማርኛ ቋንቋ ስጥ: '{medicine_name}'።\n\n"
        f"እባክዎን የሚከተሉትን ነጥቦች አካትት:\n"
        f"1. መድኃኒቱ ለምን ይጠቅማል? (Primary Uses)\n"
        f"2. የሚወሰደው መጠን እና አወሳሰድ (General Dosage Advice)\n"
        f"3. የጎንዮሽ ጉዳቶች (Side Effects)\n"
        f"4. ጥንቃቄዎች እና ማስጠንቀቂያዎች (Precautions)\n\n"
        f"መልስህን ግልጽ እና ለአንባቢ ምቹ በሆነ መልኩ በMarkdown አቅርብ።"
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://telegram.org",
        "X-Title": "Al-Noor Medicine Finder Bot"
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional medical and pharmaceutical assistant providing clear information in Amharic."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=30)
        elapsed = (datetime.now() - start_time).total_seconds()
        
        if response.status_code == 200:
            res_data = response.json()
            ai_message = res_data['choices'][0]['message']['content']
            log_ai_request(user_id, "medicine_info", payload, res_data, 200, elapsed)
            return ai_message
        else:
            log_ai_request(user_id, "medicine_info", payload, response.text, response.status_code, elapsed, error="API Error")
            return "⚠️ መረጃውን በጽሑፍ ማግኘት አልተቻለም። እባክዎ ቆየት ብለው እንደገና ይሞክሩ።"
    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        logging.error(f"OpenRouter API call failed: {e}")
        log_ai_request(user_id, "medicine_info", payload, {}, 500, elapsed, error=str(e))
        return "⚠️ ከአርቲፊሻል ኢንቴሊጀንስ ጋር መገናኘት አልተቻለም።"

# ==============================================================================
# 15. TELEGRAM COMMAND & HANDLER FUNCTIONS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    welcome_text = (
        "👋 **እንኳን ወደ አል-ኑር መድኃኒት አፋላጊ ቦት በሰላም መጡ!**\n\n"
        "ይህ ቦት የሚፈልጉትን መድኃኒት በቅርብዎ ከሚገኙ ታማኝ ፋርማሲዎች በቀላሉ እንዲያገኙ ይረዳዎታል።\n\n"
        "👇 እባክዎ ከታች ካሉት አማራጮች አንዱን ይምረጡ:"
    )
    reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    
    if LOGO_FILE_ID:
        try:
            await update.message.reply_photo(
                photo=LOGO_FILE_ID,
                caption=welcome_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return
        except Exception as e:
            logging.warning(f"Failed to send logo photo: {e}")
            
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes main menu button selections and general text input."""
    text = update.message.text
    user_id = update.effective_user.id

    if text == "🔍 መድኃኒት ፈልግ":
        await update.message.reply_text(
            "💊 እባክዎ የሚፈልጉትን የመድኃኒት ስም ያስገቡ (ለምሳሌ፦ Paracetamol, Amoxicillin):"
        )
        return WAITING_FOR_SEARCH

    elif text == "📖 የመድኃኒት መረጃ":
        await update.message.reply_text(
            "📖 መረጃ ማግኘት የሚፈልጉትን የመድኃኒት ስም ያስገቡ:"
        )
        return WAITING_FOR_MED_INFO

    elif text == "📍 አካባቢ ምረጥ":
        reply_markup = ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("📍 እባክዎ የሚገኙበትን ክፍለ ከተማ ይምረጡ:", reply_markup=reply_markup)
        return WAITING_FOR_LOCATION_SET

    elif text == "📋 ፋርማሲዎች":
        pharmacies = get_all_verified_pharmacies()
        if not pharmacies:
            await update.message.reply_text("⚠️ በአሁኑ ጊዜ የተመዘገቡ እና የተረጋገጡ ፋርማሲዎች የሉም።")
            return ConversationHandler.END

        msg = "🏥 **የተረጋገጡ ፋርማሲዎች ዝርዝር:**\n\n"
        for p in pharmacies:
            msg += f"🔹 **{p[1]}**\n📍 ቦታ: {p[2]}\n📞 ስልክ: {p[3]}\n🕒 ሰዓት: {p[4]}\n\n"
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "🏥 ፋርማሲ መዝግብ":
        await update.message.reply_text("📝 የፋርማሲዎን ሙሉ ስም ያስገቡ:")
        return REG_NAME

    elif text == "📋 ትዕዛዞች":
        p_info = get_pharmacy_info_by_chat_id(user_id)
        if not p_info:
            await update.message.reply_text("⚠️ ይህ አገልግሎት ለተመዘገቡ ፋርማሲዎች ብቻ የተከለከለ ነው።")
            return ConversationHandler.END

        p_id = get_pharmacy_id_by_chat_id(user_id)
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(
            f"SELECT id, medicine_name, customer_id, status, response_time FROM pharmacy_responses WHERE pharmacy_id = {placeholder} ORDER BY id DESC LIMIT 10",
            (p_id,)
        )
        orders = cursor.fetchall()
        conn.close()

        if not orders:
            await update.message.reply_text("📭 እስካሁን ምንም የቀረበ ትዕዛዝ/ጥያቄ የለም።")
            return ConversationHandler.END

        msg = "📋 **የቅርብ ጊዜ የመድኃኒት ጥያቄዎች:**\n\n"
        keyboard = []
        for o in orders:
            msg += f"🆔 ID: #{o[0]} | 💊 {o[1]} | 📊 ሁኔታ: {o[3]}\n"
            if o[3] == 'pending':
                keyboard.append([InlineKeyboardButton(f"💵 ለ#{o[0]} ዋጋ ስጥ", callback_data=f"price_{o[0]}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

    elif text == "📦 ክምችት":
        p_id = get_pharmacy_id_by_chat_id(user_id)
        if not p_id:
            await update.message.reply_text("⚠️ የመድኃኒት ክምችት ለማስተዳደር አስቀድመው በፋርማሲነት መመዝገብ አለብዎት።")
            return ConversationHandler.END
        reply_markup = ReplyKeyboardMarkup(STOCK_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("📦 **የክምችት ማኔጅመንት ገጽ:**", parse_mode="Markdown", reply_markup=reply_markup)

    elif text == "💊 ማሳሰቢያ":
        reminders = get_user_medicine_reminders(user_id)
        msg = "⏰ **የእርስዎ የመድኃኒት ማሳሰቢያዎች:**\n\n"
        if reminders:
            for r in reminders:
                msg += f"🔹 💊 {r[1]} | 🥄 መጠን: {r[2]} | ⏰ ሰዓት: {r[3]}\n"
        else:
            msg += "📭 እስካሁን የተመዘገበ ማሳሰቢያ የለም።\n\n"
        
        msg += "➕ አዲስ ማሳሰቢያ ለመመዝገብ በያዘው ፎርማት ያስገቡ፦\n`[የመድኃኒት ስም] - [መጠን] - [ሰዓት]`\n(ምሳሌ፦ `Paracetamol - 1 ታብሌት - 08:00 AM`)"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return WAITING_FOR_MEDICINE_REMINDER

    elif text == "📞 ድጋፍ":
        support_msg = (
            "📞 **የደንበኞች አገልግሎት እና ድጋፍ**\n\n"
            "ለማንኛውም ጥያቄ፣ አስተያየት ወይም የቴክኒክ ድጋፍ በሚከተሉት አድራሻዎች ያግኙን:\n"
            "💬 Telegram: @AlNoorSupport\n"
            "📞 ስልክ: +251900000000\n"
            "🌐 ድህረ-ገጽ: https://alnoor-pharmacy.example.com"
        )
        await update.message.reply_text(support_msg, parse_mode="Markdown")

    elif text == "🏠 ዋና ገጽ":
        reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("🏠 ወደ ዋናው ማውጫ ተመልሰዋል።", reply_markup=reply_markup)

    return ConversationHandler.END

# ==============================================================================
# 16. SEARCH & AI INFO CONVERSATION HANDLERS
# ==============================================================================
async def process_medicine_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles searching for a medicine across pharmacies."""
    med_name = update.message.text
    user_id = update.effective_user.id
    selected_loc = context.user_data.get('location')

    pharmacies_chat_ids = get_verified_pharmacies_by_location(selected_loc)
    
    save_search_history(user_id, med_name, f"Found {len(pharmacies_chat_ids)} pharmacies")

    if not pharmacies_chat_ids:
        await update.message.reply_text(
            f"❌ በጥያቄዎ ('{med_name}') መሠረት በቅርብዎ ምንም የተረጋገጠ ፋርማሲ አልተገኘም።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return ConversationHandler.END

    sent_count = 0
    for p_chat_id in pharmacies_chat_ids:
        success = save_pharmacy_request(p_chat_id, user_id, med_name)
        if success:
            await send_order_notification(context, p_chat_id, user_id, med_name, datetime.now())
            sent_count += 1

    await update.message.reply_text(
        f"✅ የትዕዛዝ ጥያቄዎ ለ {sent_count} ፋርማሲዎች በስኬት ተልኳል።\nፋርማሲዎች ዋጋቸውን ሲመልሱ ወዲያውኑ መልእክት ይደርስዎታል!",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END

async def process_ai_info_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes AI requests for medicine details."""
    med_name = update.message.text
    user_id = update.effective_user.id
    
    await update.message.reply_text(f"⏳ ለ '{med_name}' አስፈላጊውን መረጃ በመፈለግ ላይ... እባክዎ ትንሽ ይጠብቁ።")
    
    ai_response = await get_ai_medicine_info(med_name, user_id)
    await update.message.reply_text(
        ai_response,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END

async def set_user_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the user location filter."""
    loc = update.message.text
    if loc != "🏠 ዋና ገጽ":
        context.user_data['location'] = loc
        await update.message.reply_text(
            f"📍 አሁን የተመረጠው ክፍለ ከተማ: **{loc}**",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
    else:
        await update.message.reply_text("🏠 ወደ ዋና ገጽ ተመልሰዋል", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    return ConversationHandler.END

async def process_reminder_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves user medicine reminder input."""
    text = update.message.text
    user_id = update.effective_user.id

    if text == "🏠 ዋና ገጽ":
        await update.message.reply_text("🏠 ወደ ዋና ገጽ ተመልሰዋል", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return ConversationHandler.END

    try:
        parts = [p.strip() for p in text.split("-")]
        if len(parts) == 3:
            med_name, dosage, time_str = parts[0], parts[1], parts[2]
            save_user_reminder(user_id, med_name, dosage, time_str)
            await update.message.reply_text(
                f"✅ ማሳሰቢያዎ ተመዝግቧል!\n💊 መድኃኒት: {med_name}\n🥄 መጠን: {dosage}\n⏰ ሰዓት: {time_str}",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                "❌ እባክዎ የተሳሳተ ፎርማት ተጠቅመዋል። እንደገና ይሞክሩ፦\n`[የመድኃኒት ስም] - [መጠን] - [ሰዓት]`",
                parse_mode="Markdown"
            )
    except Exception as e:
        logging.error(f"Reminder registration error: {e}")
        await update.message.reply_text("❌ ማሳሰቢያውን መመዝገብ አልተቻለም። እባክዎ ደግመው ይሞክሩ።")

    return ConversationHandler.END

# ==============================================================================
# 17. PHARMACY REGISTRATION FLOW
# ==============================================================================
async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 እባክዎ የፋርማሲውን ሙሉ ስም ያስገቡ:")
    return REG_NAME

async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reg_name'] = update.message.text
    reply_markup = ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("📍 የፋርማሲው የሚገኝበትን ክፍለ ከተማ ይምረጡ:", reply_markup=reply_markup)
    return REG_LOCATION

async def reg_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reg_location'] = update.message.text
    await update.message.reply_text("📞 የፋርማሲውን የስልክ ቁጥር ያስገቡ:")
    return REG_PHONE

async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reg_phone'] = update.message.text
    reply_markup = ReplyKeyboardMarkup(HOURS_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("🕒 የሥራ ሰዓት ይምረጡ:", reply_markup=reply_markup)
    return REG_HOURS

async def reg_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reg_hours'] = update.message.text
    await update.message.reply_text("📷 እባክዎ የፋርማሲውን ንግድ ፈቃድ (License) ፎቶ ይላኩ:")
    return REG_LICENSE

async def reg_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file_id = photo.file_id
    context.user_data['reg_license'] = file_id
    chat_id = update.effective_user.id

    try:
        pharmacy_id = register_pharmacy_db(
            chat_id=chat_id,
            name=context.user_data['reg_name'],
            location=context.user_data['reg_location'],
            phone=context.user_data['reg_phone'],
            operating_hours=context.user_data['reg_hours'],
            license_photo=file_id
        )

        await update.message.reply_text(
            "✅ የፋርማሲ ምዝገባ ጥያቄዎ በስኬት ተልኳል!\nበአስተዳዳሪው ሲረጋገጥ ማሳወቂያ ይደርስዎታል።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

        # Notify Admin
        if ADMIN_CHAT_ID != 0:
            admin_btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ አጽድቅ (Verify)", callback_data=f"verify_{pharmacy_id}")],
                [InlineKeyboardButton("❌ ውድቅ አድርግ (Delete)", callback_data=f"delpharm_{pharmacy_id}")]
            ])
            admin_msg = (
                f"🆕 **አዲስ የፋርማሲ ምዝገባ ጥያቄ!**\n\n"
                f"🆔 ID: {pharmacy_id}\n"
                f"🏥 ስም: {context.user_data['reg_name']}\n"
                f"📍 ቦታ: {context.user_data['reg_location']}\n"
                f"📞 ስልክ: {context.user_data['reg_phone']}\n"
                f"🕒 ሰዓት: {context.user_data['reg_hours']}"
            )
            await context.bot.send_photo(
                chat_id=ADMIN_CHAT_ID,
                photo=file_id,
                caption=admin_msg,
                parse_mode="Markdown",
                reply_markup=admin_btn
            )

    except Exception as e:
        logging.error(f"Pharmacy registration flow error: {e}")
        await update.message.reply_text("❌ ምዝገባው አልተሳካም። እባክዎ ደግመው ይሞክሩ።")

    return ConversationHandler.END

# ==============================================================================
# 18. CALLBACK QUERY HANDLER (Admin Actions & Pharmacy Pricing)
# ==============================================================================
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("verify_"):
        pharmacy_id = int(data.split("_")[1])
        verify_pharmacy_db(pharmacy_id)
        p_info = get_pharmacy_info_by_id(pharmacy_id)
        
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ **ይህ ፋርማሲ በስኬት ተረጋግጧል!**")
        
        if p_info:
            target_chat_id = p_info[3]
            await send_telegram_notification(
                context, 
                target_chat_id, 
                "🎉 **እንኳን ደስ አለዎት!**\nፋርማሲዎ በአስተዳዳሪው ተረጋግጧል። አሁን የመድኃኒት ጥያቄዎችን መቀበል ይችላሉ።"
            )

    elif data.startswith("delpharm_"):
        pharmacy_id = int(data.split("_")[1])
        delete_pharmacy_db(pharmacy_id)
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ **ይህ ፋርማሲ ተሰርዟል/ውድቅ ተደርጓል።**")

    elif data.startswith("price_"):
        order_id = int(data.split("_")[1])
        context.user_data['active_order_id'] = order_id
        await query.message.reply_text(f"💵 እባክዎ ለትዕዛዝ #{order_id} የመድኃኒቱን ዋጋ (በብር) ያስገቡ:")
        return WAITING_FOR_ORDER_PRICE

async def process_order_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price_text = update.message.text
    order_id = context.user_data.get('active_order_id')

    if not order_id:
        await update.message.reply_text("⚠️ የትዕዛዝ ቁጥር አልተገኘም።")
        return ConversationHandler.END

    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(
        f"SELECT customer_id, medicine_name, pharmacy_id FROM pharmacy_responses WHERE id = {placeholder}",
        (order_id,)
    )
    row = cursor.fetchone()
    
    if row:
        customer_id, med_name, pharmacy_id = row
        update_order_status(order_id, f"Price: {price_text} ETB")
        p_info = get_pharmacy_info_by_id(pharmacy_id)
        pharmacy_name = p_info[0] if p_info else "ፋርማሲ"

        customer_msg = (
            f"🔔 **የመድኃኒት ዋጋ ምላሽ!**\n\n"
            f"🏥 ፋርማሲ: {pharmacy_name}\n"
            f"💊 መድኃኒት: {med_name}\n"
            f"💵 የቀረበ ዋጋ: **{price_text} ብር**\n"
            f"📞 ስልክ: {p_info[2] if p_info else 'ያልተጠቀሰ'}"
        )
        await send_telegram_notification(context, customer_id, customer_msg)
        await update.message.reply_text(f"✅ ለትዕዛዝ #{order_id} የሰጡት ዋጋ ለደንበኛው ተልኳል።")
    else:
        await update.message.reply_text("❌ ትዕዛዙ አልተገኘም።")

    conn.close()
    return ConversationHandler.END

# ==============================================================================
# 19. ADMIN & STATS COMMANDS
# ==============================================================================
async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ ይህ ትእዛዝ ለአስተዳዳሪ ብቻ የተፈቀደ ነው!")
        return

    total, verified, pending = get_bot_statistics()
    ai_stats = get_ai_stats()

    msg = (
        "📊 **የአል-ኑር ቦት አጠቃላይ ስታቲስቲክስ:**\n\n"
        f"🏥 **ፋርማሲዎች:**\n"
        f" • ጠቅላላ: {total}\n"
        f" • የተረጋገጡ: {verified}\n"
        f" • የሚጠብቁ: {pending}\n\n"
        f"🤖 **የAI አጠቃቀም:**\n"
        f" • ጠቅላላ ጥያቄዎች: {ai_stats['total']}\n"
        f" • የተሳኩ: {ai_stats['successful']}\n"
        f" • ስህተቶች: {ai_stats['errors']}\n"
        f" • አማካኝ ጊዜ: {ai_stats['avg_time']} ሰከንድ"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ ክዋኔው ተሰርዟል።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END

# ==============================================================================
# 20. MAIN APPLICATION INITIALIZATION & RUNNER
# ==============================================================================
def main():
    # 1. Initialize DB tables
    init_db()

    # 2. Run Flask in a separate thread
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info("🚀 Flask web server started.")

    # 3. Build Telegram Application
    application = Application.builder().token(BOT_TOKEN).build()

    # 4. Conversation Handlers
    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), handle_message)],
        states={
            WAITING_FOR_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_medicine_search)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    ai_info_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📖 የመድኃኒት መረጃ$"), handle_message)],
        states={
            WAITING_FOR_MED_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ai_info_request)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    location_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 አካባቢ ምረጥ$"), handle_message)],
        states={
            WAITING_FOR_LOCATION_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_user_location)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    reminder_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💊 ማሳሰቢያ$"), handle_message)],
        states={
            WAITING_FOR_MEDICINE_REMINDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_reminder_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    reg_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏥 ፋርማሲ መዝግብ$"), reg_start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_location)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)],
            REG_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_hours)],
            REG_LICENSE: [MessageHandler(filters.PHOTO, reg_license)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback_query, pattern="^price_")],
        states={
            WAITING_FOR_ORDER_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_order_price_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # 5. Add Handlers to Application
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", admin_stats_command))
    application.add_handler(CommandHandler("cancel", cancel))

    application.add_handler(search_conv)
    application.add_handler(ai_info_conv)
    application.add_handler(location_conv)
    application.add_handler(reminder_conv)
    application.add_handler(reg_conv)
    application.add_handler(price_conv)

    # General callback handler for non-conversation inline buttons (like Admin verification)
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # General message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 6. Add Job Queue for Auto-Notification (Every 60 Seconds)
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_and_send_reminders, interval=60, first=10)

    # 7. Start the Telegram Bot
    logging.info("🤖 Starting Telegram Bot polling...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
