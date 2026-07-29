import logging
import os
import threading
import time
import json
import base64
import math
from datetime import datetime, timedelta
import psycopg2
import requests
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
# 1. CONFIGURATION & DATABASE
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable ውስጥ አልተገኘም።")

LOGO_FILE_ID = "AgACAgQAAxkBAAEszTBqZGhpfKNE12Y948HvU4JhQHfZrQAC0g1rG4xKIFPy4FmrrNxjRAEAAwIAA3gAAz0E"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ==============================================================================
# 2. DATABASE SETUP
# ==============================================================================

def get_db_connection():
    if DATABASE_URL:
        db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect("pharmacy_bot.db")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Pharmacies table
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
                    is_verified INTEGER DEFAULT 0,
                    latitude REAL,
                    longitude REAL
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
                    is_verified INTEGER DEFAULT 0,
                    latitude REAL,
                    longitude REAL
                )
            """)
        
        # Search history table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    search_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    result_summary TEXT
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    medicine_name TEXT NOT NULL,
                    search_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    result_summary TEXT
                )
            """)
        
        # AI logs table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    request_type TEXT,
                    request_data TEXT,
                    response_data TEXT,
                    status_code INTEGER,
                    error_message TEXT,
                    response_time REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    request_type TEXT,
                    request_data TEXT,
                    response_data TEXT,
                    status_code INTEGER,
                    error_message TEXT,
                    response_time REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        
        # Pharmacy responses table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pharmacy_responses (
                    id SERIAL PRIMARY KEY,
                    pharmacy_id BIGINT NOT NULL,
                    customer_id BIGINT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    price TEXT,
                    photo_file_id TEXT,
                    response_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pharmacy_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pharmacy_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    medicine_name TEXT NOT NULL,
                    price TEXT,
                    photo_file_id TEXT,
                    response_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """)
        
        # Notifications table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    pharmacy_id INTEGER,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pharmacy_id INTEGER,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        
        conn.commit()
        logging.info("✅ Database initialized successfully")
        
    except Exception as e:
        logging.error(f"Database initialization error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 3. DATABASE HELPER FUNCTIONS
# ==============================================================================

def register_pharmacy_db(chat_id, name, location, phone, operating_hours, license_photo, lat=None, lon=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("""
                INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified, latitude, longitude)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s) RETURNING id
            """, (chat_id, name, location, phone, operating_hours, license_photo, lat, lon))
            pharmacy_id = cursor.fetchone()[0]
        else:
            cursor.execute("""
                INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified, latitude, longitude)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """, (chat_id, name, location, phone, operating_hours, license_photo, lat, lon))
            pharmacy_id = cursor.lastrowid
        conn.commit()
        return pharmacy_id
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()

def verify_pharmacy_db(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"UPDATE pharmacies SET is_verified = 1 WHERE id = {placeholder}", (pharmacy_id,))
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error verifying pharmacy: {e}")
    finally:
        if conn:
            conn.close()

def get_pharmacy_info_by_id(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT name, location, phone, chat_id, operating_hours FROM pharmacies WHERE id = {placeholder}", (pharmacy_id,))
        return cursor.fetchone()
    finally:
        if conn:
            conn.close()

def get_pharmacy_info_by_chat_id(chat_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT name, location, phone, operating_hours FROM pharmacies WHERE chat_id = {placeholder} ORDER BY id DESC LIMIT 1", (chat_id,))
        return cursor.fetchone()
    finally:
        if conn:
            conn.close()

def get_pharmacy_db_id_by_chat_id(chat_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {placeholder} AND is_verified = 1", (chat_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    finally:
        if conn:
            conn.close()

def get_verified_pharmacies_by_location(location=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if location:
            placeholder = "%s" if DATABASE_URL else "?"
            cursor.execute(f"SELECT chat_id FROM pharmacies WHERE is_verified = 1 AND LOWER(location) LIKE LOWER({placeholder})", (f"%{location}%",))
        else:
            cursor.execute("SELECT chat_id FROM pharmacies WHERE is_verified = 1")
        return [int(r[0]) for r in cursor.fetchall()]
    finally:
        if conn:
            conn.close()

def get_all_verified_pharmacies():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, name, location, phone, operating_hours, latitude, longitude FROM pharmacies WHERE is_verified = 1")
        except:
            cursor.execute("SELECT id, name, location, phone, operating_hours FROM pharmacies WHERE is_verified = 1")
        return cursor.fetchall()
    finally:
        if conn:
            conn.close()

def get_bot_statistics():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM pharmacies")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM pharmacies WHERE is_verified = 1")
        verified = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM pharmacies WHERE is_verified = 0")
        pending = cursor.fetchone()[0]
        return total, verified, pending
    finally:
        if conn:
            conn.close()

def save_search_history(user_id, medicine_name, result_summary=""):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO search_history (user_id, medicine_name, result_summary)
            VALUES ({placeholder}, {placeholder}, {placeholder})
        """, (user_id, medicine_name, result_summary[:500] if result_summary else ""))
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error saving search history: {e}")
    finally:
        if conn:
            conn.close()

def get_user_search_history(user_id, limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT medicine_name, search_date FROM search_history 
            WHERE user_id = {placeholder} ORDER BY search_date DESC LIMIT {placeholder}
        """, (user_id, limit))
        return cursor.fetchall()
    finally:
        if conn:
            conn.close()

def get_top_medicines(limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT medicine_name, COUNT(*) as search_count 
            FROM search_history 
            GROUP BY medicine_name 
            ORDER BY search_count DESC 
            LIMIT %s
        """ if DATABASE_URL else """
            SELECT medicine_name, COUNT(*) as search_count 
            FROM search_history 
            GROUP BY medicine_name 
            ORDER BY search_count DESC 
            LIMIT ?
        """, (limit,))
        return cursor.fetchall()
    finally:
        if conn:
            conn.close()

def log_ai_request(user_id, request_type, request_data, response_data, status_code, response_time, error=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO ai_logs 
            (user_id, request_type, request_data, response_data, status_code, error_message, response_time)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        """, (user_id, request_type, json.dumps(request_data)[:500], json.dumps(response_data)[:500], status_code, error, response_time))
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error logging AI request: {e}")
    finally:
        if conn:
            conn.close()

def get_ai_stats():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ai_logs")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ai_logs WHERE status_code = 200")
        successful = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ai_logs WHERE status_code != 200")
        errors = cursor.fetchone()[0]
        cursor.execute("SELECT AVG(response_time) FROM ai_logs")
        avg_time = cursor.fetchone()[0] or 0
        return {'total': total, 'successful': successful, 'errors': errors, 'avg_time': round(avg_time, 2)}
    finally:
        if conn:
            conn.close()

def get_top_pharmacies(limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.name, p.location, p.phone, COALESCE(COUNT(pr.id), 0) as response_count
            FROM pharmacies p
            LEFT JOIN pharmacy_responses pr ON p.id = pr.pharmacy_id
            WHERE p.is_verified = 1
            GROUP BY p.id, p.name, p.location, p.phone
            ORDER BY response_count DESC
            LIMIT %s
        """ if DATABASE_URL else """
            SELECT p.id, p.name, p.location, p.phone, COALESCE(COUNT(pr.id), 0) as response_count
            FROM pharmacies p
            LEFT JOIN pharmacy_responses pr ON p.id = pr.pharmacy_id
            WHERE p.is_verified = 1
            GROUP BY p.id, p.name, p.location, p.phone
            ORDER BY response_count DESC
            LIMIT ?
        """, (limit,))
        return cursor.fetchall()
    finally:
        if conn:
            conn.close()

def save_notification(user_id, pharmacy_id, notification_type, message):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO notifications (user_id, pharmacy_id, type, message)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})
        """, (user_id, pharmacy_id, notification_type, message))
        conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error saving notification: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_notifications(user_id, limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, type, message, is_read, created_at FROM notifications 
            WHERE user_id = {placeholder} 
            ORDER BY created_at DESC LIMIT {placeholder}
        """, (user_id, limit))
        return cursor.fetchall()
    finally:
        if conn:
            conn.close()

def mark_notification_read(notification_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"UPDATE notifications SET is_read = TRUE WHERE id = {placeholder}", (notification_id,))
        conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def get_unread_notification_count(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id = {placeholder} AND is_read = FALSE", (user_id,))
        return cursor.fetchone()[0]
    finally:
        if conn:
            conn.close()

def save_pharmacy_request(pharmacy_chat_id, customer_id, medicine_name, photo_file_id=None):
    """Save a new pharmacy request and return the order ID"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {placeholder} AND is_verified = 1", (pharmacy_chat_id,))
        pharm_row = cursor.fetchone()
        if not pharm_row:
            return None
        pharmacy_db_id = pharm_row[0]
        
        if DATABASE_URL:
            cursor.execute(f"""
                INSERT INTO pharmacy_responses (pharmacy_id, customer_id, medicine_name, photo_file_id, status)
                VALUES (%s, %s, %s, %s, 'pending') RETURNING id
            """, (pharmacy_db_id, customer_id, medicine_name, photo_file_id))
            order_id = cursor.fetchone()[0]
        else:
            cursor.execute(f"""
                INSERT INTO pharmacy_responses (pharmacy_id, customer_id, medicine_name, photo_file_id, status)
                VALUES (?, ?, ?, ?, 'pending')
            """, (pharmacy_db_id, customer_id, medicine_name, photo_file_id))
            order_id = cursor.lastrowid
        
        conn.commit()
        return order_id
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error saving pharmacy request: {e}")
        return None
    finally:
        if conn:
            conn.close()

def update_order_status(order_id, status, price=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        if price:
            cursor.execute(f"UPDATE pharmacy_responses SET status = {placeholder}, price = {placeholder} WHERE id = {placeholder}", (status, price, order_id))
        else:
            cursor.execute(f"UPDATE pharmacy_responses SET status = {placeholder} WHERE id = {placeholder}", (status, order_id))
        conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error updating order status: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_order_by_id(order_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT customer_id, medicine_name, photo_file_id, pharmacy_id FROM pharmacy_responses 
            WHERE id = {placeholder}
        """, (order_id,))
        result = cursor.fetchone()
        return result
    finally:
        if conn:
            conn.close()

def get_pharmacy_chat_id_by_db_id(pharmacy_db_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT chat_id FROM pharmacies WHERE id = {placeholder}", (pharmacy_db_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    finally:
        if conn:
            conn.close()

def get_order_details_with_pharmacy(order_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT pr.customer_id, pr.medicine_name, pr.photo_file_id, pr.status, pr.price,
                   p.id, p.name, p.location, p.phone, p.operating_hours, p.chat_id
            FROM pharmacy_responses pr
            JOIN pharmacies p ON pr.pharmacy_id = p.id
            WHERE pr.id = {placeholder}
        """, (order_id,))
        return cursor.fetchone()
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 4. STATES & KEYBOARDS
# ==============================================================================
WAITING_FOR_SEARCH = 1
WAITING_FOR_PRICE = 2
WAITING_FOR_LOCATION_SET = 3
WAITING_FOR_MED_INFO = 4
REG_NAME, REG_LOCATION, REG_PHONE, REG_HOURS, REG_LICENSE = 10, 11, 12, 14, 13

MAIN_KEYBOARD = [
    ["🔍 መድኃኒት ፈልግ", "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ"],
    ["📍 አካባቢ ምረጥ", "📋 የፋርማሲዎች ዝርዝር"],
    ["🏥 ፋርማሲ መዝግብ", "📋 የታዘዙ መድኃኒቶች"],
    ["📞 እገዛ / ድጋፍ", "🏠 ወደ ዋና ገጽ"]
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
# 5. AI & TRANSLATION FUNCTIONS
# ==============================================================================

async def translate_to_amharic(english_text):
    try:
        response = requests.post("https://api.lesan.ai/translate", json={"text": english_text, "source": "en", "target": "am"}, timeout=30)
        if response.status_code == 200:
            return response.json().get('translation') or response.json().get('result')
    except: pass
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "am", "dt": "t", "q": english_text}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return ''.join([part[0] for part in response.json()[0] if part[0]])
    except: pass
    return None

def clean_translation(text):
    lines = text.split('\n')
    seen = set()
    unique_lines = []
    for line in lines:
        line = line.strip()
        if line and line not in seen:
            unique_lines.append(line)
            seen.add(line)
    return '\n'.join(unique_lines)

async def analyze_with_openrouter(prompt, text=None, image_bytes=None):
    if not OPENROUTER_API_KEY:
        return "⚠️ OpenRouter API key is missing."
    start_time = time.time()
    try:
        if image_bytes:
            if len(image_bytes) > 1024 * 1024:
                try:
                    from PIL import Image
                    import io
                    image = Image.open(io.BytesIO(image_bytes))
                    image.thumbnail((1024, 1024))
                    buffer = io.BytesIO()
                    image.save(buffer, format='JPEG', quality=85)
                    image_bytes = buffer.getvalue()
                except: pass
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            user_content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]
        elif text:
            user_content = f"{prompt}\n\nMedicine: {text}"
        else:
            return "❌ No data received."
        system_content = """You are a medical professional and pharmacist. Provide accurate, evidence-based information about medications. Always include: name, uses, dosage, side effects, and precautions. Include a disclaimer."""
        response = requests.post(OPENROUTER_API_URL, headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://alnoor-pharmacy-bot.onrender.com", "X-Title": "Al-Noor Pharmacy Bot"}, json={"model": OPENROUTER_MODEL, "messages": [{"role": "system", "content": system_content}, {"role": "user", "content": user_content}], "temperature": 0.7, "max_tokens": 1024}, timeout=120)
        if response.status_code != 200:
            log_ai_request(None, "medicine_info", {"prompt": prompt[:100]}, {"error": response.text}, response.status_code, time.time() - start_time, response.text)
            return f"❌ API Error: {response.status_code}"
        result = response.json()
        result_text = result['choices'][0]['message']['content']
        log_ai_request(None, "medicine_info", {"prompt": prompt[:100]}, {"response": result_text[:200]}, 200, time.time() - start_time)
        return result_text
    except Exception as e:
        return f"❌ Error: {str(e)[:100]}"

# ==============================================================================
# 6. HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "ወዳጄ"
    unread_count = get_unread_notification_count(update.effective_user.id)
    notification_text = f" 🔔{unread_count}" if unread_count > 0 else ""
    welcome_text = f"👋 ሰላም {user_name}! ወደ አል-ኑር መድኃኒት አፋላጊ በደህና መጡ።\n\n━━━━━━━ ⚖️ ሕጋዊ ማስታወቂያ ━━━━━━━\n• 🏥 ከሕጋዊና ፈቃድ ካላቸው ፋርማሲዎች ጋር ብቻ ያገናኛል።\n• 📄 መድኃኒት ሲገዙ የሐኪም ማዘዣ (Prescription) ይያዙ።\n• ℹ️ ይህ ቦት የመረጃ ማገናኛ እንጂ ሕክምና አይሰጥም።\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👇 የሚፈልጉትን አገልግሎት ከታች ይምረጡ፦"
    if update.message:
        try:
            await update.message.reply_photo(photo=LOGO_FILE_ID, caption=welcome_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        except:
            await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    return ConversationHandler.END

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pharmacy_chat_id = update.effective_user.id
    is_admin = (update.effective_user.id == ADMIN_CHAT_ID)
    
    # Check if pharmacy is registered and verified
    pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
    if not pharm_info:
        await update.message.reply_text("⚠️ ይህ ትዕዛዝ ለፋርማሲዎች ብቻ ነው።\n\n📝 እባክዎ መጀመሪያ ፋርማሲዎን ይመዝገቡ (🏥 ፋርማሲ መዝግብ)", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return
    
    pharmacy_db_id = get_pharmacy_db_id_by_chat_id(pharmacy_chat_id)
    if not pharmacy_db_id:
        await update.message.reply_text("⚠️ የፋርማሲዎ ምዝገባ ገና አልተረጋገጠም።\n\n⏳ እባክዎ አድሚኑ እስኪያረጋግጥ ይጠብቁ።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT id, customer_id, medicine_name, price, photo_file_id, response_time, status FROM pharmacy_responses WHERE pharmacy_id = {placeholder} ORDER BY response_time DESC", (pharmacy_db_id,))
        all_orders = cursor.fetchall()
        
        if not all_orders:
            await update.message.reply_text("📋 **የታዘዙ መድኃኒቶች**\n\n🔍 ምንም የታዘዙ መድኃኒቶች አልተገኙም።\n\n💡 መድኃኒት ሲፈልጉ ደንበኞች እዚህ ይታያሉ።", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
            return
    except Exception as e:
        await update.message.reply_text(f"❌ የታዘዙ መድኃኒቶችን ማግኘት አልተቻለም።\n\n`{str(e)[:200]}`", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return
    finally:
        if conn:
            conn.close()
    
    # Show statistics
    pending = len([o for o in all_orders if o[6] == 'pending'])
    responded = len([o for o in all_orders if o[6] == 'responded'])
    completed = len([o for o in all_orders if o[6] == 'completed'])
    
    await update.message.reply_text(f"📋 **የታዘዙ መድኃኒቶች**\n━━━━━━━━━━━━━━━━━━━━━━━\n🔔 ጠቅላላ: {len(all_orders)} ጥያቄዎች\n⏳ ምላሽ የሚጠብቁ: {pending}\n✅ መልስ የተሰጠ: {responded}\n📦 የተጠናቀቀ: {completed}\n\n💡 ለመድኃኒት መልስ ለመስጠት '💊 መልስ ስጥ' የሚለውን ይጫኑ።", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    
    # Show orders from newest to oldest (በቅድሚያ አዲሱን አሳይ)
    for idx, order in enumerate(all_orders, 1):
        order_id, customer_id, medicine_name, price, photo_file_id, response_time, status = order
        time_str = response_time.strftime('%Y-%m-%d %H:%M') if hasattr(response_time, 'strftime') else str(response_time)
        status_emoji = "⏳" if status == 'pending' else "✅" if status == 'responded' else "📦"
        status_text = "ምላሽ የሚጠብቅ" if status == 'pending' else "መልስ ተሰጥቷል" if status == 'responded' else "ተጠናቅቋል"
        text = f"{status_emoji} **{idx}. {medicine_name}**\n   📅 {time_str}\n   📊 {status_text}"
        if status == 'responded' and price:
            text += f"\n   💰 {price[:50]}"
        if photo_file_id:
            text += f"\n   📷 ፎቶ ተያይዟል"
        if is_admin:
            text += f"\n   👤 ደንበኛ: {customer_id}"
        
        # For pending orders, show respond button
        if status == 'pending':
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💊 መልስ ስጥ", callback_data=f"respond_{order_id}")]
            ])
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
        elif photo_file_id:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 ፎቶውን ይመልከቱ", callback_data=f"view_photo_{order_id}")]
            ])
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text(text, parse_mode="Markdown")

async def view_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split("_")[2])
    order = get_order_by_id(order_id)
    if not order or not order[2]:
        await query.edit_message_text("❌ ለዚህ ትዕዛዝ ምንም ፎቶ አልተገኘም።")
        return
    customer_id, medicine_name, photo_file_id, pharmacy_id = order
    context.user_data["viewing_order_id"] = order_id
    context.user_data["viewing_customer_id"] = customer_id
    try:
        await query.edit_message_text(f"📷 **{medicine_name}** ፎቶ")
        await context.bot.send_photo(chat_id=update.effective_user.id, photo=photo_file_id, caption=f"📷 ለ **{medicine_name}** የተያያዘ ፎቶ\n\n💡 መልስ ለመስጠት ከታች ያለውን ቁልፍ ይጫኑ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💊 መልስ ስጥ", callback_data=f"respond_{order_id}")]]))
    except:
        await query.edit_message_text("❌ ፎቶውን መላክ አልተቻለም።")

async def respond_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 'respond' button click from pending orders"""
    query = update.callback_query
    await query.answer()
    
    # Get order_id from callback data (respond_123)
    order_id = int(query.data.split("_")[1])
    
    # Get order details
    order = get_order_details_with_pharmacy(order_id)
    if not order:
        await query.edit_message_text("❌ ይህ ትዕዛዝ አልተገኘም።")
        return
    
    customer_id, medicine_name, photo_file_id, status, price, pharm_id, pharm_name, pharm_loc, pharm_phone, pharm_hours, pharm_chat_id = order
    
    # Store in context
    context.user_data["responding_order_id"] = order_id
    context.user_data["responding_customer_id"] = customer_id
    
    message = f"💊 **ለትዕዛዝ መልስ መስጠት**\n\n📋 መድኃኒት: {medicine_name}\n👤 ደንበኛ: {customer_id}\n\n✏️ እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።\n\nምሳሌ: 150 ብር, አለኝ, ከሰአት በኋላ ይምጡ"
    
    # Show photo if available
    if photo_file_id:
        try:
            await query.edit_message_text("📷 ፎቶውን እያየን ነው...")
            keyboard = ReplyKeyboardMarkup([
                ["🏠 ወደ ዋና ገጽ"]
            ], resize_keyboard=True)
            await context.bot.send_photo(
                chat_id=update.effective_user.id,
                photo=photo_file_id,
                caption=message,
                reply_markup=keyboard
            )
            await query.delete()
        except Exception as e:
            logging.error(f"Error sending photo: {e}")
            await query.edit_message_text(message, reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    else:
        await query.edit_message_text(message, reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    
    return WAITING_FOR_PRICE

async def receive_price_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive price details from pharmacy and send to customer"""
    msg = update.message
    
    if msg.text == "🏠 ወደ ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END
    
    price_details = msg.text
    order_id = context.user_data.get("responding_order_id")
    customer_id = context.user_data.get("responding_customer_id")
    
    if not order_id or not customer_id:
        await msg.reply_text("❌ የትዕዛዝ መለያ አልተገኘም። እባክዎ እንደገና ይሞክሩ።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return ConversationHandler.END
    
    # Update the order status
    if update_order_status(order_id, 'responded', price_details):
        # Get pharmacy details to send to customer
        order_info = get_order_details_with_pharmacy(order_id)
        if order_info:
            _, _, _, _, _, _, pharm_name, pharm_loc, pharm_phone, pharm_hours, _ = order_info
            
            # Send response to customer
            try:
                await context.bot.send_message(
                    chat_id=int(customer_id),
                    text=f"🎉 የመድኃኒት መረጃ ተገኘ!\n\n🏥 ፋርማሲ፦ {pharm_name}\n📍 አካባቢ፦ {pharm_loc}\n📞 ስልክ፦ {pharm_phone}\n🕒 የስራ ሰዓት፦ {pharm_hours}\n\n💰 የዋጋ እና የዝርዝር መረጃ፦\n{price_details}",
                    reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                )
            except Exception as e:
                logging.error(f"Error sending to customer: {e}")
            
            await msg.reply_text(
                "✅ መልስዎ ለደንበኛው በስኬት ተልኳል!\n\n📋 ይህ ትዕዛዝ አሁን 'መልስ ተሰጥቷል' በሚለው ሁኔታ ላይ ነው።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        else:
            await msg.reply_text("❌ የፋርማሲ መረጃ ማግኘት አልተቻለም።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    else:
        await msg.reply_text("❌ መልስዎን ማስቀመጥ አልተቻለም። እባክዎ እንደገና ይሞክሩ።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    
    # Clear context
    context.user_data.pop("responding_order_id", None)
    context.user_data.pop("responding_customer_id", None)
    return ConversationHandler.END

# ==============================================================================
# 7. MAIN FUNCTION
# ==============================================================================

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation Handlers
    loc_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 አካባቢ ምረጥ$"), select_location_prompt)],
        states={
            WAITING_FOR_LOCATION_SET: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_location)
            ]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), prompt_search)],
        states={
            WAITING_FOR_SEARCH: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.ALL, handle_customer_request)
            ]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    med_info_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ$"), prompt_med_info)],
        states={
            WAITING_FOR_MED_INFO: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.ALL, analyze_med_info)
            ]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    pharmacy_reply_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(respond_order_callback, pattern="^respond_"),
            CallbackQueryHandler(view_photo_callback, pattern="^view_photo_"),
            CallbackQueryHandler(handle_pharmacy_response, pattern="^available_"),
            CallbackQueryHandler(handle_pharmacy_response, pattern="^not_available_")
        ],
        states={
            WAITING_FOR_PRICE: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False
    )
    
    pharmacy_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏥 ፋርማሲ መዝግብ$"), start_pharmacy_reg)],
        states={
            REG_NAME: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_name)
            ],
            REG_LOCATION: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_location)
            ],
            REG_PHONE: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_phone)
            ],
            REG_HOURS: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_hours)
            ],
            REG_LICENSE: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.ALL, reg_get_license)
            ]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(MessageHandler(filters.Regex("^📋 የፋርማሲዎች ዝርዝር$"), list_pharmacies))
    app.add_handler(MessageHandler(filters.Regex("^📋 የታዘዙ መድኃኒቶች$"), show_orders))
    app.add_handler(MessageHandler(filters.Regex("^📞 እገዛ / ድጋፍ$"), show_help))
    app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^verify_"))
    app.add_handler(CallbackQueryHandler(translate_callback, pattern="^(translate_amharic|go_home)$"))
    app.add_handler(CallbackQueryHandler(show_english_callback, pattern="^show_english$"))
    app.add_handler(loc_conv)
    app.add_handler(search_conv)
    app.add_handler(med_info_conv)
    app.add_handler(pharmacy_reply_conv)
    app.add_handler(pharmacy_conv)
    app.add_handler(MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start))
    app.add_error_handler(error_handler_func)
    
    print("🤖 አል-ኑር መድኃኒት አፋላጊ ቦት በ PostgreSQL ዳታቤዝ ስራ ጀምሯል...")
    app.run_polling()

# ==============================================================================
# 8. REMAINING HANDLERS
# ==============================================================================

async def select_location_prompt(update: Update, context):
    current_loc = context.user_data.get("user_location", "አልተመረጠም")
    await update.message.reply_text(
        f"📍 **የአካባቢ መምረጫ**\n\nአሁን የተመረጠው አካባቢ፦ {current_loc}\n\nእባክዎ የሚገኙበትን ክፍለ ከተማ ከታች ካሉት አዝራሮች ይምረጡ ወይም ይጻፉልን፦",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
    )
    return WAITING_FOR_LOCATION_SET

async def save_user_location(update: Update, context):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["user_location"] = msg.text
    await msg.reply_text(
        f"✅ አካባቢዎ በስኬት ወደ '{msg.text}' ተቀይሯል!",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END

async def prompt_search(update: Update, context):
    user_loc = context.user_data.get("user_location")
    loc_text = f"📍 የተመረጠው አካባቢ፦ {user_loc}\n\n" if user_loc else "📍 *(አካባቢ አልመረጡም)*\n\n"
    await update.message.reply_text(
        f"{loc_text}እባክዎ የሚፈልጉትን መድኃኒት፦\n1. በጽሑፍ የመድኃኒቱን ስም ይጻፉልን፡ ወይም\n2. የሐኪም ማዘዣውን (Prescription) ፎቶ አንስተው ይላኩልን።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return WAITING_FOR_SEARCH

async def handle_customer_request(update: Update, context):
    msg = update.message
    if not msg:
        return ConversationHandler.END
    
    if msg.text and msg.text in ["🔍 መድኃኒት ፈልግ", "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ", "📍 አካባቢ ምረጥ", "📋 የፋርማሲዎች ዝርዝር", "🏥 ፋርማሲ መዝግብ", "📋 የታዘዙ መድኃኒቶች", "📞 እገዛ / ድጋፍ", "🏠 ወደ ዋና ገጽ"]:
        if msg.text == "🔍 መድኃኒት ፈልግ":
            return await prompt_search(update, context)
        elif msg.text == "📋 የታዘዙ መድኃኒቶች":
            return await show_orders(update, context)
        else:
            await start(update, context)
            return ConversationHandler.END
    
    user = update.effective_user
    user_loc = context.user_data.get('user_location')
    verified_pharmacies = get_verified_pharmacies_by_location(user_loc) if user_loc else get_verified_pharmacies_by_location(None)
    
    if not verified_pharmacies:
        await msg.reply_text(
            "⚠️ በአሁኑ ሰዓት ምንም የተረጋገጡ ፋርማሲዎች የሉም።\n\n💡 እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    photo_file_id = msg.photo[-1].file_id if msg.photo else (msg.document.file_id if msg.document else None)
    is_doc = bool(msg.document)
    medicine_name = msg.text if msg.text else "Prescription Photo"
    order_time = datetime.now()
    loc_tag = f" (አካባቢ፦ {user_loc})" if user_loc else ""
    
    sent_count = 0
    
    if photo_file_id:
        await msg.reply_text(
            f"✅ የሐኪም ማዘዣ ፎቶዎ ተቀብለናል! ለ{len(verified_pharmacies)} ሕጋዊ ፋርማሲዎች ጥያቄው ተልኳል።{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        
        for chat_id in verified_pharmacies:
            order_id = save_pharmacy_request(chat_id, user.id, medicine_name, photo_file_id)
            if order_id:
                sent_count += 1
                
                caption = f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ (በፎቶ)!**\n👤 ከደንበኛ፡ {user.first_name}\n📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n📅 ቀን: {order_time.strftime('%Y-%m-%d')}\n🕐 ሰዓት: {order_time.strftime('%H:%M')}\n\n⚡ **እባክዎ በፍጥነት ምላሽ ይስጡ!**"
                
                # Create inline keyboard with order_id
                keyboard = [
                    [InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{order_id}")],
                    [InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{order_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    if is_doc:
                        await context.bot.send_document(chat_id=chat_id, document=photo_file_id, caption=caption, reply_markup=reply_markup)
                    else:
                        await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=caption, reply_markup=reply_markup)
                except Exception as e:
                    logging.error(f"Error sending to pharmacy {chat_id}: {e}")
    
    elif msg.text:
        med_name = msg.text
        await msg.reply_text(
            f"✅ የመድኃኒት ስም '{med_name}' ተቀብለናል! ለ{len(verified_pharmacies)} ሕጋዊ ፋርማሲዎች እየተላከ ነው...{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        
        for chat_id in verified_pharmacies:
            order_id = save_pharmacy_request(chat_id, user.id, med_name)
            if order_id:
                sent_count += 1
                
                # Create inline keyboard with order_id
                keyboard = [
                    [InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{order_id}")],
                    [InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{order_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ!**\n💊 የተፈለገው መድኃኒት፡ **{med_name}**\n👤 ከደንበኛ፡ {user.first_name}\n📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n📅 ቀን: {order_time.strftime('%Y-%m-%d')}\n🕐 ሰዓት: {order_time.strftime('%H:%M')}\n\n⚡ **እባክዎ በፍጥነት ምላሽ ይስጡ!**",
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logging.error(f"Error sending to pharmacy {chat_id}: {e}")
    
    if sent_count == 0:
        await msg.reply_text(
            "❌ ጥያቄዎን ለማስተላለፍ አልተቻለም። እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
    
    return ConversationHandler.END

async def handle_pharmacy_response(update: Update, context):
    """Handle pharmacy response (available/not available) with order_id"""
    query = update.callback_query
    await query.answer()
    
    # Parse the callback data
    parts = query.data.split("_")
    action = parts[0]  # "available" or "not_available"
    order_id = int(parts[1])
    
    # Get order details
    order = get_order_details_with_pharmacy(order_id)
    if not order:
        await query.edit_message_text("❌ ይህ ትዕዛዝ አልተገኘም።")
        return
    
    customer_id, medicine_name, photo_file_id, status, price, pharm_id, pharm_name, pharm_loc, pharm_phone, pharm_hours, pharm_chat_id = order
    
    if action == "available":
        # Store order_id in context for the price entry
        context.user_data["responding_order_id"] = order_id
        context.user_data["responding_customer_id"] = customer_id
        
        # Show photo if available
        if photo_file_id:
            try:
                await query.edit_message_text("📷 ፎቶውን እያየን ነው...")
                keyboard = ReplyKeyboardMarkup([
                    ["🏠 ወደ ዋና ገጽ"]
                ], resize_keyboard=True)
                await context.bot.send_photo(
                    chat_id=update.effective_user.id,
                    photo=photo_file_id,
                    caption="✅ 'መድኃኒቱ አለኝ' የሚለው ምላሽዎ ተመዝግቧል!\n\n✏️ እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።",
                    reply_markup=keyboard
                )
                await query.delete()
            except Exception as e:
                logging.error(f"Error sending photo: {e}")
                await query.edit_message_text(
                    "✅ 'መድኃኒቱ አለኝ' የሚለው ምላሽዎ ተመዝግቧል!\n\n✏️ እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።",
                    reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
                )
        else:
            await query.edit_message_text(
                "✅ 'መድኃኒቱ አለኝ' የሚለው ምላሽዎ ተመዝግቧል!\n\n✏️ እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።",
                reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
            )
        return WAITING_FOR_PRICE
    else:
        # Not available - update status
        update_order_status(order_id, 'not_available')
        await query.edit_message_text("❌ 'የለኝም' የሚለው ምላሽዎ ተመዝግቧል።")
        return ConversationHandler.END

async def prompt_med_info(update: Update, context):
    await update.message.reply_text(
        "📖 ስለ ታዘዘልዎት መድኃኒት መረጃ ማወቂያ\n\nእባክዎ ስለ መድኃኒቱ መረጃ ለማግኘት፦\n1. የመድኃኒቱን ስም በጽሑፍ ይጻፉልን፡ ወይም\n2. የሐኪም ማዘዣውን (Prescription) ፎቶ አንስተው ይላኩልን።\n\n🤖 *AI መድኃኒቱ ለምን እንደሚያገለግል፣ አወሳሰዱን እና ጥንቃቄዎችን ያብራራልዎታል።*",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return WAITING_FOR_MED_INFO

async def analyze_med_info(update: Update, context):
    msg = update.message
    if not msg:
        return WAITING_FOR_MED_INFO
    
    if msg.text == "🏠 ወደ ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END
    
    if not OPENROUTER_API_KEY:
        await msg.reply_text("⚠️ AI service is not configured.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return ConversationHandler.END
    
    wait_msg = await msg.reply_text("⏳ Fetching medical information... Please wait...")
    image_bytes = None
    text = None
    user_id = update.effective_user.id if update.effective_user else None
    
    if msg.photo:
        photo_file = await msg.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        await msg.reply_text("📷 Photo received! Analyzing...")
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        doc_file = await msg.document.get_file()
        image_bytes = await doc_file.download_as_bytearray()
        await msg.reply_text("📷 Image document received! Analyzing...")
    elif msg.text:
        text = msg.text
    else:
        await msg.reply_text("❌ Please send a medicine name or photo.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return WAITING_FOR_MED_INFO
    
    if image_bytes:
        prompt = "Analyze this prescription or medicine photo and provide detailed information about the medication: 1. Name 2. Uses 3. Dosage 4. Side effects 5. Precautions"
    else:
        prompt = f"Provide detailed medical information about the following medication: 1. Name 2. Uses 3. Dosage 4. Side effects 5. Precautions\nMedication: {text}"
    
    try:
        english_response = await analyze_with_openrouter(prompt, text=text if not image_bytes else None, image_bytes=image_bytes)
        
        if english_response.startswith("❌") or english_response.startswith("⚠️"):
            await wait_msg.edit_text(english_response)
            return ConversationHandler.END
        
        save_search_history(user_id, text if text else "photo_prescription", english_response[:200])
        context.user_data["last_english_response"] = english_response
        
        inline_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Translate to Amharic", callback_data="translate_amharic")],
            [InlineKeyboardButton("🏠 ወደ ዋና ገጽ", callback_data="go_home")]
        ])
        await wait_msg.delete()
        await msg.reply_text(
            f"💡 Medical Information (English)\n\n{english_response}\n\n━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ This is for informational purposes only. Always consult your doctor.",
            reply_markup=inline_keyboard
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ Failed to get information.\n\n`{str(e)[:200]}`", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    
    return ConversationHandler.END

async def translate_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "go_home":
        await query.message.delete()
        await start(update, context)
        return
    
    english_text = context.user_data.get("last_english_response")
    if not english_text:
        await query.edit_message_text("⚠️ No English text to translate.")
        return
    
    await query.edit_message_text("⏳ ወደ አማርኛ እየተረጎመ ነው... እባክዎ ይጠብቁ...")
    
    try:
        amharic_text = await translate_to_amharic(english_text)
        if amharic_text:
            amharic_text = clean_translation(amharic_text)
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Show English", callback_data="show_english")],
                [InlineKeyboardButton("🏠 ወደ ዋና ገጽ", callback_data="go_home")]
            ])
            await query.edit_message_text(
                f"💡 የመድኃኒት መረጃ (አማርኛ)\n\n{amharic_text}\n\n━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ ይህ መረጃ ለግንዛቤ ብቻ ነው። ሁልጊዜ የሐኪምዎን መመሪያ ይከተሉ።",
                reply_markup=inline_keyboard
            )
            context.user_data["last_amharic_response"] = amharic_text
        else:
            # If translation fails, show English version with option to retry
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 እንደገና ሞክር", callback_data="translate_amharic")],
                [InlineKeyboardButton("📝 Show English", callback_data="show_english")],
                [InlineKeyboardButton("🏠 ወደ ዋና ገጽ", callback_data="go_home")]
            ])
            await query.edit_message_text(
                f"❌ ትርጉሙ አልተሳካም። እባክዎ እንደገና ይሞክሩ ወይም እንግሊዝኛውን ይመልከቱ።\n\n{english_text[:500]}...",
                reply_markup=inline_keyboard
            )
    except Exception as e:
        await query.edit_message_text(f"❌ የትርጉም ስህተት: {str(e)[:200]}")

async def show_english_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    english_text = context.user_data.get("last_english_response")
    if not english_text:
        await query.edit_message_text("⚠️ No English text found.")
        return
    
    inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Translate to Amharic", callback_data="translate_amharic")],
        [InlineKeyboardButton("🏠 ወደ ዋና ገጽ", callback_data="go_home")]
    ])
    await query.edit_message_text(
        f"💡 Medical Information (English)\n\n{english_text}\n\n━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ This is for informational purposes only. Always consult your doctor.",
        reply_markup=inline_keyboard
    )

async def admin_stats(update: Update, context):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ ይቅርታ! ይህንን ትዕዛዝ መጠቀም የሚችለው አድሚኑ ብቻ ነው።")
        return
    
    total, verified, pending = get_bot_statistics()
    ai_stats = get_ai_stats()
    await update.message.reply_text(
        f"📊 **የአድሚን ስታቲስቲክስ**\n\n🤖 **AI አጠቃቀም**\n• ጠቅላላ ጥያቄዎች: {ai_stats['total']}\n• ስኬታማ: {ai_stats['successful']}\n• ስህተቶች: {ai_stats['errors']}\n• አማካይ ምላሽ ጊዜ: {ai_stats['avg_time']} ሰከንድ\n\n🏥 **ፋርማሲዎች**\n• ጠቅላላ: {total}\n• የተረጋገጡ: {verified}\n• ማረጋገጫ የሚጠብቁ: {pending}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def list_pharmacies(update: Update, context):
    pharmacies = get_all_verified_pharmacies()
    if not pharmacies:
        await update.message.reply_text("ℹ️ በአሁኑ ሰዓት የተረጋገጡ ሕጋዊ ፋርማሲዎች አልተገኙም።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return
    
    top_pharms = get_top_pharmacies(100)
    pharm_rank = {pid: idx+1 for idx, (pid, _, _, _, _) in enumerate(top_pharms)}
    
    text = "🏥 **የተመዘገቡ ሕጋዊ ፋርማሲዎች ዝርዝር**\n\n"
    for pharmacy in pharmacies:
        try:
            pid, name, loc, phone, hours = pharmacy[:5]
            rank = pharm_rank.get(pid, '—')
            rank_emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"#{rank}" if rank != '—' else ""
            text += f"{rank_emoji} **{name}**\n   📍 {loc}\n   📞 {phone}\n   🕒 {hours}\n────────────────────\n"
            if len(text) > 3500:
                await update.message.reply_text(text, parse_mode="Markdown")
                text = ""
        except:
            continue
    
    if text:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

async def handle_admin_approval(update: Update, context):
    query = update.callback_query
    await query.answer()
    pharmacy_id = int(query.data.split("_")[1])
    verify_pharmacy_db(pharmacy_id)
    pharm_info = get_pharmacy_info_by_id(pharmacy_id)
    pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
    pharm_chat_id = pharm_info[3] if pharm_info else None
    await query.edit_message_caption(caption=f"✅ '{pharm_name}' በስኬት ተረጋግጧል! (ID: {pharmacy_id})")
    if pharm_chat_id:
        try:
            await context.bot.send_message(chat_id=pharm_chat_id, text=f"🎉 እንኳን ደስ አለዎት!\n\nየፋርማሲዎት ({pharm_name}) ምዝገባ በአድሚኑ ተረጋግጧል።")
        except:
            pass

async def start_pharmacy_reg(update: Update, context):
    await update.message.reply_text(
        "🏥 የፋርማሲ መመዝገቢያ ክፍል\n\nእባክዎ የፋርማሲዎን ሙሉ ስም ያስገቡ፦",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return REG_NAME

async def reg_get_name(update: Update, context):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_name"] = msg.text
    await msg.reply_text(
        "📍 ፋርማሲዎ የሚገኝበትን ክፍለ ከተማ / አካባቢ ከታች ይምረጡ ወይም ይጻፉ፦",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
    )
    return REG_LOCATION

async def reg_get_location(update: Update, context):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_location"] = msg.text
    await msg.reply_text(
        "📞 ደንበኞች የሚያገኙበትን የፋርማሲ የስልክ ቁጥር ያስገቡ፦",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return REG_PHONE

async def reg_get_phone(update: Update, context):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_phone"] = msg.text
    await msg.reply_text(
        "🕒 የፋርማሲዎ የስራ ሰዓት መቼ ነው?",
        reply_markup=ReplyKeyboardMarkup(HOURS_KEYBOARD, resize_keyboard=True)
    )
    return REG_HOURS

async def reg_get_hours(update: Update, context):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_hours"] = msg.text
    await msg.reply_text(
        "📄 የንግድ ፈቃድ ወይም የመድኃኒት መሸጫ ፈቃድ ፎቶ አንስተው ይላኩልን፦",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return REG_LICENSE

async def reg_get_license(update: Update, context):
    msg = update.message
    if not msg or (msg.text and msg.text == "🏠 ወደ ዋና ገጽ"):
        return await start(update, context)
    
    photo_file_id = msg.photo[-1].file_id if msg.photo else (msg.document.file_id if msg.document else None)
    is_doc = bool(msg.document)
    
    if not photo_file_id:
        await msg.reply_text(
            "❌ እባክዎ የንግድ ፈቃዱን በፎቶ መልኩ ያያይዙልን።",
            reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
        )
        return REG_LICENSE
    
    chat_id = msg.chat_id
    name = context.user_data.get("pharm_name", "ያልተጠቀሰ")
    location = context.user_data.get("pharm_location", "ያልተጠቀሰ")
    phone = context.user_data.get("pharm_phone", "ያልተጠቀሰ")
    hours = context.user_data.get("pharm_hours", "ያልተጠቀሰ")
    
    pharm_id = register_pharmacy_db(chat_id, name, location, phone, hours, photo_file_id)
    
    await msg.reply_text(
        "📝 የምዝገባ ጥያቄዎ ተቀብለናል!\n\n⏳ የላኩት የንግድ ፈቃድ በአድሚን ተመርምሮ ሲረጋገጥ ማሳወቂያ ይደርስዎታል።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    
    admin_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ፈቅድ (Approve)", callback_data=f"verify_{pharm_id}")]])
    caption_text = f"🔔 **አዲስ የፋርማሲ ምዝገባ ጥያቄ!**\n\n🏥 ስም፦ {name}\n📍 አካባቢ፦ {location}\n📞 ስልክ፦ {phone}\n🕒 የስራ ሰዓት፦ {hours}\n\nሕጋዊነቱን አረጋግጠው ይፍቀዱ፦"
    
    try:
        if is_doc:
            await context.bot.send_document(chat_id=ADMIN_CHAT_ID, document=photo_file_id, caption=caption_text, parse_mode="Markdown", reply_markup=admin_keyboard)
        else:
            await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=photo_file_id, caption=caption_text, parse_mode="Markdown", reply_markup=admin_keyboard)
    except:
        pass
    
    return ConversationHandler.END

async def show_help(update: Update, context):
    await update.message.reply_text(
        "📞 **አል-ኑር መድኃኒት አፋላጊ - የደንበኞች ድጋፍ**\n\nማንኛውም ጥያቄ ወይም አስተያየት ካለዎት፦\n• ስልክ፦ +251 911 00 00 00\n• ቴሌግራም፦ @AlNoorSupport",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def error_handler_func(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(msg="Exception while handling an update:", exc_info=context.error)

if __name__ == "__main__":
    main()
