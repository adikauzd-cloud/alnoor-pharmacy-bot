import logging
import os
import threading
import psycopg2
import requests
import json
import base64
import math
import asyncio
from datetime import datetime, timedelta
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
LOGO_FILE_ID = os.environ.get("LOGO_FILE_ID", "")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable ውስጥ አልተገኘም።")

if ADMIN_CHAT_ID == 0:
    raise RuntimeError("❌ ADMIN_CHAT_ID environment variable ውስጥ አልተገኘም።")

# ==============================================================================
# 🤖 OpenRouter AI Configuration
# ==============================================================================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ==============================================================================
# 2. DATABASE CONNECTION & INITIALIZATION
# ==============================================================================

def get_placeholder():
    return "%s" if DATABASE_URL else "?"

def get_db_connection():
    if DATABASE_URL:
        db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        return conn
    else:
        import sqlite3
        return sqlite3.connect("pharmacy_bot.db")

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        
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
        
        if DATABASE_URL:
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

def get_pharmacy_id_by_chat_id(chat_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {p} AND is_verified = 1", (chat_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logging.error(f"Get pharmacy ID error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def register_pharmacy_db(chat_id, name, location, phone, operating_hours, license_photo, lat=None, lon=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        
        try:
            if DATABASE_URL:
                cursor.execute(f"""
                    INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified, latitude, longitude)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, 0, {p}, {p}) RETURNING id
                """, (chat_id, name, location, phone, operating_hours, license_photo, lat, lon))
                pharmacy_id = cursor.fetchone()[0]
            else:
                cursor.execute(f"""
                    INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (chat_id, name, location, phone, operating_hours, license_photo, lat, lon))
                pharmacy_id = cursor.lastrowid
        except Exception as e:
            logging.warning(f"Inserting without latitude/longitude: {e}")
            if DATABASE_URL:
                cursor.execute(f"""
                    INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, 0) RETURNING id
                """, (chat_id, name, location, phone, operating_hours, license_photo))
                pharmacy_id = cursor.fetchone()[0]
            else:
                cursor.execute(f"""
                    INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                """, (chat_id, name, location, phone, operating_hours, license_photo))
                pharmacy_id = cursor.lastrowid
        
        if DATABASE_URL:
            conn.commit()
        return pharmacy_id
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Register pharmacy error: {e}")
        raise e
    finally:
        if conn:
            conn.close()

def verify_pharmacy_db(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"UPDATE pharmacies SET is_verified = 1 WHERE id = {p}", (pharmacy_id,))
        if DATABASE_URL:
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Verify pharmacy error: {e}")
        raise e
    finally:
        if conn:
            conn.close()

def get_pharmacy_info_by_id(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"SELECT name, location, phone, chat_id, operating_hours FROM pharmacies WHERE id = {p}", (pharmacy_id,))
        row = cursor.fetchone()
        return row
    except Exception as e:
        logging.error(f"Get pharmacy by ID error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_pharmacy_info_by_chat_id(chat_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"SELECT name, location, phone, operating_hours FROM pharmacies WHERE chat_id = {p} AND is_verified = 1 ORDER BY id DESC LIMIT 1", (chat_id,))
        row = cursor.fetchone()
        return row
    except Exception as e:
        logging.error(f"Get pharmacy by chat ID error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_verified_pharmacies_by_location(location=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        if location:
            cursor.execute(f"SELECT chat_id FROM pharmacies WHERE is_verified = 1 AND LOWER(location) LIKE LOWER({p})", (f"%{location}%",))
        else:
            cursor.execute("SELECT chat_id FROM pharmacies WHERE is_verified = 1")
        rows = cursor.fetchall()
        return [int(r[0]) for r in rows]
    except Exception as e:
        logging.error(f"Get verified pharmacies by location error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_all_verified_pharmacies():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, location, phone, operating_hours, latitude, longitude FROM pharmacies WHERE is_verified = 1")
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get all pharmacies error: {e}")
        return []
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
    except Exception as e:
        logging.error(f"Get bot statistics error: {e}")
        return 0, 0, 0
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 4. SEARCH HISTORY FUNCTIONS
# ==============================================================================

def save_search_history(user_id, medicine_name, result_summary=""):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            INSERT INTO search_history (user_id, medicine_name, result_summary)
            VALUES ({p}, {p}, {p})
        """, (user_id, medicine_name, result_summary[:500] if result_summary else ""))
        if DATABASE_URL:
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Save search history error: {e}")
    finally:
        if conn:
            conn.close()

def get_user_search_history(user_id, limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            SELECT medicine_name, search_date FROM search_history 
            WHERE user_id = {p} ORDER BY search_date DESC LIMIT {p}
        """, (user_id, limit))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get search history error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_top_medicines(limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            SELECT medicine_name, COUNT(*) as search_count 
            FROM search_history 
            GROUP BY medicine_name 
            ORDER BY search_count DESC 
            LIMIT {p}
        """, (limit,))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get top medicines error: {e}")
        return []
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 5. AI LOGS FUNCTIONS
# ==============================================================================

def log_ai_request(user_id, request_type, request_data, response_data, status_code, response_time, error=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            INSERT INTO ai_logs 
            (user_id, request_type, request_data, response_data, status_code, error_message, response_time)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (user_id, request_type, json.dumps(request_data)[:500], json.dumps(response_data)[:500], status_code, error, response_time))
        if DATABASE_URL:
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Log AI request error: {e}")
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
    except Exception as e:
        logging.error(f"Get AI stats error: {e}")
        return {'total': 0, 'successful': 0, 'errors': 0, 'avg_time': 0}
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 6. NOTIFICATION SYSTEM
# ==============================================================================

def save_notification(user_id, pharmacy_id, notification_type, message):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            INSERT INTO notifications (user_id, pharmacy_id, type, message)
            VALUES ({p}, {p}, {p}, {p})
        """, (user_id, pharmacy_id, notification_type, message))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Save notification error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_notifications(user_id, limit=10):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            SELECT id, type, message, is_read, created_at FROM notifications 
            WHERE user_id = {p} 
            ORDER BY created_at DESC LIMIT {p}
        """, (user_id, limit))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get notifications error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def mark_notification_read(notification_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"UPDATE notifications SET is_read = TRUE WHERE id = {p}", (notification_id,))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Mark notification read error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_unread_notification_count(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id = {p} AND is_read = FALSE", (user_id,))
        count = cursor.fetchone()[0]
        return count
    except Exception as e:
        logging.error(f"Get unread count error: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def save_pharmacy_request(pharmacy_chat_id, customer_id, medicine_name, photo_file_id=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        
        cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {p} AND is_verified = 1", (pharmacy_chat_id,))
        pharm_row = cursor.fetchone()
        
        if not pharm_row:
            logging.warning(f"Pharmacy not found or not verified for chat_id: {pharmacy_chat_id}")
            return False
        
        pharmacy_db_id = pharm_row[0]
        logging.info(f"Saving request for pharmacy DB ID: {pharmacy_db_id}")
        
        cursor.execute(f"""
            INSERT INTO pharmacy_responses (pharmacy_id, customer_id, medicine_name, photo_file_id, status)
            VALUES ({p}, {p}, {p}, {p}, 'pending')
        """, (pharmacy_db_id, customer_id, medicine_name, photo_file_id))
        
        if DATABASE_URL:
            conn.commit()
        logging.info(f"✅ Request saved: pharmacy_db_id={pharmacy_db_id}, customer={customer_id}, medicine={medicine_name}")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Save pharmacy request error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_order_status(order_id, status, price=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        
        if price:
            cursor.execute(f"UPDATE pharmacy_responses SET status = {p}, price = {p} WHERE id = {p}", (status, price, order_id))
        else:
            cursor.execute(f"UPDATE pharmacy_responses SET status = {p} WHERE id = {p}", (status, order_id))
        
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Update order status error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_order_by_id(order_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        cursor.execute(f"""
            SELECT id, pharmacy_id, customer_id, medicine_name, price, photo_file_id, response_time, status
            FROM pharmacy_responses 
            WHERE id = {p}
        """, (order_id,))
        return cursor.fetchone()
    except Exception as e:
        logging.error(f"Get order error: {e}")
        return None
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 7. STATES & KEYBOARDS
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
    ["🏥 ፋርማሲ መዝግብ", "📋 የታዘዙ መድኃኒቶች"],
    ["📞 እገዛ / ድጋፍ", "📊 ስታቲስቲክስ"],
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
# 8. TRANSLATION FUNCTION
# ==============================================================================

async def translate_to_amharic(english_text):
    try:
        response = requests.post(
            url="https://api.lesan.ai/translate",
            json={"text": english_text, "source": "en", "target": "am"},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result.get('translation') or result.get('result')
    except Exception as e:
        logging.error(f"Lesan AI error: {e}")
    
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "am", "dt": "t", "q": english_text}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            result = response.json()
            return ''.join([part[0] for part in result[0] if part[0]])
    except Exception as e:
        logging.error(f"Google Translate error: {e}")
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

# ==============================================================================
# 9. AI HANDLER
# ==============================================================================

import time

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
                except:
                    pass
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            user_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]
        elif text:
            user_content = f"{prompt}\n\nMedicine: {text}"
        else:
            return "❌ No data received. Please send a medicine name or photo."
        
        system_content = """You are a medical professional and pharmacist. 
Provide accurate, evidence-based information about medications.
Always include: name, uses, dosage, side effects, and precautions.
Format your response clearly with bullet points or numbered lists.
Include a disclaimer that this is for informational purposes only."""

        response = requests.post(
            url=OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://alnoor-pharmacy-bot.onrender.com",
                "X-Title": "Al-Noor Pharmacy Bot"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.7,
                "max_tokens": 1024
            },
            timeout=120
        )
        
        response_time = time.time() - start_time
        result_text = ""
        
        if response.status_code != 200:
            error_detail = response.text
            logging.error(f"OpenRouter API Error {response.status_code}: {error_detail}")
            result_text = f"❌ API Error: {response.status_code}"
            log_ai_request(None, "medicine_info", {"prompt": prompt[:100]}, {"error": error_detail}, response.status_code, response_time, error_detail)
            return result_text
        
        result = response.json()
        result_text = result['choices'][0]['message']['content']
        log_ai_request(None, "medicine_info", {"prompt": prompt[:100]}, {"response": result_text[:200]}, 200, response_time)
        return result_text
            
    except requests.exceptions.Timeout:
        return "⏱️ Request timed out. Please try again."
    except Exception as e:
        logging.error(f"OpenRouter API error: {e}")
        return f"❌ Error: {str(e)[:100]}"

# ==============================================================================
# 10. HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "ወዳጄ"
    user_id = update.effective_user.id
    unread_count = get_unread_notification_count(user_id)
    notification_text = f" 🔔{unread_count}" if unread_count > 0 else ""
    
    welcome_text = (
        f"👋 ሰላም {user_name}! ወደ አል-ኑር መድኃኒት አፋላጊ በደህና መጡ።\n\n"
        f"━━━━━━━ ⚖️ ሕጋዊ ማስታወቂያ ━━━━━━━\n"
        f"• 🏥 ከሕጋዊና ፈቃድ ካላቸው ፋርማሲዎች ጋር ብቻ ያገናኛል።\n"
        f"• 📄 መድኃኒት ሲገዙ የሐኪም ማዘዣ (Prescription) ይያዙ።\n"
        f"• ℹ️ ይህ ቦት የመረጃ ማገናኛ እንጂ ሕክምና አይሰጥም።\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 የሚፈልጉትን አገልግሎት ከታች ይምረጡ፦"
    )
    
    if update.message:
        try:
            if LOGO_FILE_ID:
                await update.message.reply_photo(
                    photo=LOGO_FILE_ID,
                    caption=welcome_text,
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
                )
            else:
                await update.message.reply_text(
                    welcome_text,
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

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pharmacy_chat_id = update.effective_user.id
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_CHAT_ID)
    
    pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
    if not pharm_info:
        await update.message.reply_text(
            "⚠️ ይህ ትዕዛዝ ለፋርማሲዎች ብቻ ነው።\n\n"
            "📝 እባክዎ መጀመሪያ ፋርማሲዎን ይመዝገቡ (🏥 ፋርማሲ መዝግብ)",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        p = get_placeholder()
        
        cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {p} AND is_verified = 1", (pharmacy_chat_id,))
        pharm_row = cursor.fetchone()
        
        if not pharm_row:
            await update.message.reply_text(
                "⚠️ የፋርማሲዎ ምዝገባ ገና አልተረጋገጠም።\n\n"
                "⏳ እባክዎ አድሚኑ እስኪያረጋግጥ ይጠብቁ።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return
        
        pharmacy_db_id = pharm_row[0]
        
        cursor.execute(f"""
            SELECT id, customer_id, medicine_name, price, photo_file_id, response_time, status
            FROM pharmacy_responses 
            WHERE pharmacy_id = {p} 
            ORDER BY response_time DESC
        """, (pharmacy_db_id,))
        all_orders = cursor.fetchall()
        
        if not all_orders:
            await update.message.reply_text(
                "📋 **የታዘዙ መድኃኒቶች**\n\n"
                "🔍 ምንም የታዘዙ መድኃኒቶች አልተገኙም።\n\n"
                "💡 መድኃኒት ሲፈልጉ ደንበኞች እዚህ ይታያሉ።",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return
        
    except Exception as e:
        logging.error(f"Error getting orders: {e}")
        await update.message.reply_text(
            f"❌ የታዘዙ መድኃኒቶችን ማግኘት አልተቻለም።\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    finally:
        if conn:
            conn.close()
    
    pending_count = len([o for o in all_orders if o[6] == 'pending'])
    responded_count = len([o for o in all_orders if o[6] == 'responded'])
    completed_count = len([o for o in all_orders if o[6] == 'completed'])
    
    summary_text = (
        f"📋 **የታዘዙ መድኃኒቶች**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔔 ጠቅላላ: {len(all_orders)} ጥያቄዎች\n"
        f"⏳ ምላሽ የሚጠብቁ: {pending_count}\n"
        f"✅ መልስ የተሰጠ: {responded_count}\n"
        f"📦 የተጠናቀቀ: {completed_count}\n\n"
        f"💡 ለመድኃኒት መልስ ለመስጠት '💊 መልስ ስጥ' የሚለውን ይጫኑ።"
    )
    
    await update.message.reply_text(
        summary_text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    
    for idx, order in enumerate(all_orders, 1):
        order_id, customer_id, medicine_name, price, photo_file_id, response_time, status = order
        time_str = response_time.strftime('%Y-%m-%d %H:%M') if hasattr(response_time, 'strftime') else str(response_time)
        
        status_emoji = "⏳" if status == 'pending' else "✅" if status == 'responded' else "📦"
        status_text = "ምላሽ የሚጠብቅ" if status == 'pending' else "መልስ ተሰጥቷል" if status == 'responded' else "ተጠናቅቋል"
        
        text = f"{status_emoji} **{idx}. {medicine_name}**\n"
        text += f"   📅 {time_str}\n"
        text += f"   📊 {status_text}\n"
        
        if status == 'responded' and price:
            text += f"   💰 {price[:50]}\n"
        
        if photo_file_id:
            text += f"   📷 ፎቶ ተያይዟል\n"
        
        if is_admin:
            text += f"   👤 ደንበኛ: {customer_id}\n"
        
        if status == 'pending':
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💊 መልስ ስጥ", callback_data=f"respond_order_{order_id}")]
            ])
            await update.message.reply_text(
                text,
                parse_mode="Markdown",
                reply_markup=inline_keyboard
            )
        else:
            if photo_file_id:
                view_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📷 ፎቶውን ይመልከቱ", callback_data=f"view_photo_{order_id}")]
                ])
                await update.message.reply_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=view_keyboard
                )
            else:
                await update.message.reply_text(
                    text,
                    parse_mode="Markdown"
                )

async def view_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        order_id = int(query.data.split("_")[2])
        order = get_order_by_id(order_id)
        
        if not order or not order[5]:
            await query.edit_message_text("❌ ለዚህ ትዕዛዝ ምንም ፎቶ አልተገኘም።")
            return
        
        photo_file_id = order[5]
        medicine_name = order[3]
        
        inline_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💊 መልስ ስጥ", callback_data=f"respond_from_photo_{order_id}")]
        ])
        
        await query.edit_message_text(f"📷 **{medicine_name}** ፎቶ")
        await context.bot.send_photo(
            chat_id=update.effective_user.id,
            photo=photo_file_id,
            caption=f"📷 ለ **{medicine_name}** የተያያዘ ፎቶ\n\n💡 መልስ ለመስጠት ከታች ያለውን ቁልፍ ይጫኑ",
            reply_markup=inline_keyboard
        )
    except Exception as e:
        logging.error(f"Error in view_photo_callback: {e}")
        await query.edit_message_text(f"❌ የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።")

async def respond_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        order_id = int(query.data.split("_")[2])
        logging.info(f"📞 Respond order callback - order_id: {order_id}")
        
        order = get_order_by_id(order_id)
        
        if not order:
            await query.edit_message_text("❌ ይህ ትዕዛዝ አልተገኘም።")
            return ConversationHandler.END
        
        customer_id = order[2]
        medicine_name = order[3]
        photo_file_id = order[5]
        
        context.user_data["responding_order_id"] = order_id
        context.user_data["responding_customer_id"] = customer_id
        
        message = (
            f"💊 **ለትዕዛዝ መልስ መስጠት**\n\n"
            f"📋 መድኃኒት: {medicine_name}\n"
            f"👤 ደንበኛ ID: {customer_id}\n\n"
            f"✏️ እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።\n\n"
            f"ምሳሌ: 150 ብር, አለኝ, ከሰአት በኋላ ይምጡ\n\n"
            f"✅ አለኝ ወይም ❌ የለኝም ብለው መመለስ ይችላሉ።"
        )
        
        reply_markup = ReplyKeyboardMarkup(
            [["✅ አለኝ", "❌ የለኝም"], ["🏠 ወደ ዋና ገጽ"]], 
            resize_keyboard=True
        )
        
        if photo_file_id:
            try:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=update.effective_user.id,
                    photo=photo_file_id,
                    caption=message,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.error(f"Error sending photo: {e}")
                await query.edit_message_text(
                    message,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
        else:
            await query.edit_message_text(
                message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        
        return WAITING_FOR_PRICE
        
    except Exception as e:
        logging.error(f"Error in respond_order_callback: {e}")
        await query.edit_message_text(f"❌ የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።")
        return ConversationHandler.END

async def respond_from_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        order_id = int(query.data.split("_")[2])
        logging.info(f"📞 Respond from photo callback - order_id: {order_id}")
        
        order = get_order_by_id(order_id)
        
        if not order:
            await query.edit_message_text("❌ ይህ ትዕዛዝ አልተገኘም።")
            return ConversationHandler.END
        
        customer_id = order[2]
        medicine_name = order[3]
        
        context.user_data["responding_order_id"] = order_id
        context.user_data["responding_customer_id"] = customer_id
        
        message = (
            f"💊 **ለትዕዛዝ መልስ መስጠት**\n\n"
            f"📋 መድኃኒት: {medicine_name}\n"
            f"👤 ደንበኛ ID: {customer_id}\n\n"
            f"✏️ እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።\n\n"
            f"ምሳሌ: 150 ብር, አለኝ, ከሰአት በኋላ ይምጡ\n\n"
            f"✅ አለኝ ወይም ❌ የለኝም ብለው መመለስ ይችላሉ።"
        )
        
        reply_markup = ReplyKeyboardMarkup(
            [["✅ አለኝ", "❌ የለኝም"], ["🏠 ወደ ዋና ገጽ"]], 
            resize_keyboard=True
        )
        
        await query.edit_message_text(
            message,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
        return WAITING_FOR_PRICE
        
    except Exception as e:
        logging.error(f"Error in respond_from_photo_callback: {e}")
        await query.edit_message_text(f"❌ የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።")
        return ConversationHandler.END

async def receive_price_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg:
        return ConversationHandler.END
        
    if msg.text == "🏠 ወደ ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END

    price_details = msg.text
    order_id = context.user_data.get("responding_order_id")
    customer_id = context.user_data.get("responding_customer_id")
    
    logging.info(f"💰 Received price - order_id: {order_id}, customer_id: {customer_id}")
    
    if order_id and customer_id:
        try:
            success = update_order_status(order_id, 'responded', price_details)
            
            if success:
                pharmacy_chat_id = update.effective_user.id
                pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
                pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
                pharm_loc = pharm_info[1] if pharm_info else "ያልተጠቀሰ"
                pharm_phone = pharm_info[2] if pharm_info else "ያልተጠቀሰ"
                pharm_hours = pharm_info[3] if pharm_info and pharm_info[3] else "ያልተጠቀሰ"
                
                try:
                    await context.bot.send_message(
                        chat_id=int(customer_id),
                        text=f"🎉 የመድኃኒት መረጃ ከ{pharm_name} ተገኘ!\n\n"
                             f"🏥 ፋርማሲ፦ {pharm_name}\n"
                             f"📍 አካባቢ፦ {pharm_loc}\n"
                             f"📞 ስልክ፦ {pharm_phone}\n"
                             f"🕒 የስራ ሰዓት፦ {pharm_hours}\n\n"
                             f"💰 የዋጋ እና የዝርዝር መረጃ፦\n{price_details}",
                        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                    )
                    
                    await msg.reply_text(
                        "✅ መልስዎ ለደንበኛው በስኬት ተልኳል!",
                        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                    )
                except Exception as e:
                    logging.error(f"Error sending to customer: {e}")
                    await msg.reply_text(
                        "✅ መልስዎ ተቀምጧል ነገር ግን ለደንበኛው መላክ አልተቻለም።",
                        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                    )
            else:
                await msg.reply_text(
                    "❌ መልስዎን ማስቀመጥ አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
                    reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                )
            
            context.user_data.pop("responding_order_id", None)
            context.user_data.pop("responding_customer_id", None)
            return ConversationHandler.END
            
        except Exception as e:
            logging.error(f"Error in receive_price_details: {e}")
            await msg.reply_text(
                f"❌ የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return ConversationHandler.END
    
    # Legacy flow
    customer_id = context.user_data.get("responding_customer_id")
    if customer_id:
        try:
            pharmacy_chat_id = msg.chat_id
            pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
            pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
            pharm_loc = pharm_info[1] if pharm_info else "ያልተጠቀሰ"
            pharm_phone = pharm_info[2] if pharm_info else "ያልተጠቀሰ"
            pharm_hours = pharm_info[3] if pharm_info and pharm_info[3] else "ያልተጠቀሰ"
            
            await context.bot.send_message(
                chat_id=int(customer_id),
                text=f"🎉 የመድኃኒት መረጃ ከ{pharm_name} ተገኘ!\n\n"
                     f"🏥 ፋርማሲ፦ {pharm_name}\n"
                     f"📍 አካባቢ፦ {pharm_loc}\n"
                     f"📞 ስልክ፦ {pharm_phone}\n"
                     f"🕒 የስራ ሰዓት፦ {pharm_hours}\n\n"
                     f"💰 የዋጋ እና የዝርዝር መረጃ፦\n{price_details}",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            
            await msg.reply_text(
                "✅ መልስዎ ለደንበኛው በስኬት ተልኳል!",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        except Exception as e:
            logging.error(f"Error in legacy flow: {e}")
            await msg.reply_text(
                "❌ መልስዎን ማስቀመጥ አልተቻለም።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
    
    context.user_data.pop("responding_customer_id", None)
    return ConversationHandler.END

async def handle_customer_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return ConversationHandler.END

    if msg.text:
        menu_buttons = [
            "🔍 መድኃኒት ፈልግ",
            "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ",
            "📍 አካባቢ ምረጥ",
            "📋 የፋርማሲዎች ዝርዝር",
            "🏥 ፋርማሲ መዝግብ",
            "📋 የታዘዙ መድኃኒቶች",
            "📞 እገዛ / ድጋፍ",
            "📊 ስታቲስቲክስ",
            "🏠 ወደ ዋና ገጽ"
        ]
        
        if msg.text in menu_buttons:
            if msg.text == "🔍 መድኃኒት ፈልግ":
                return await prompt_search(update, context)
            elif msg.text == "📋 የታዘዙ መድኃኒቶች":
                return await show_orders(update, context)
            elif msg.text == "📊 ስታቲስቲክስ":
                return await stats_command(update, context)
            else:
                await start(update, context)
                return ConversationHandler.END

    user = update.effective_user
    user_loc = context.user_data.get('user_location')
    verified_pharmacies = get_verified_pharmacies_by_location(user_loc) if user_loc else []
    if not verified_pharmacies:
        verified_pharmacies = get_verified_pharmacies_by_location(None)

    if not verified_pharmacies:
        await msg.reply_text(
            "⚠️ በአሁኑ ሰዓት ምንም የተረጋገጡ ፋርማሲዎች የሉም።\n\n"
            "💡 እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return ConversationHandler.END

    photo_file_id = None
    is_doc = False
    medicine_name = msg.text if msg.text else "Prescription Photo"
    order_time = datetime.now()

    if msg.photo:
        photo_file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        photo_file_id = msg.document.file_id
        is_doc = True
    elif msg.text:
        medicine_name = msg.text

    loc_tag = f" (አካባቢ፦ {user_loc})" if user_loc else ""

    sent_count = 0
    if photo_file_id:
        await msg.reply_text(
            f"✅ የሐኪም ማዘዣ ፎቶዎ ተቀብለናል! ለ{len(verified_pharmacies)} ሕጋዊ ፋርማሲዎች ጥያቄው ተልኳል።{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        
        tasks = []
        for chat_id in verified_pharmacies:
            save_result = save_pharmacy_request(chat_id, user.id, medicine_name, photo_file_id)
            if not save_result:
                logging.error(f"Failed to save request for pharmacy chat_id: {chat_id}")
                continue
            
            sent_count += 1
            caption = f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ (በፎቶ)!**\n"
            caption += f"👤 ከደንበኛ፡ {user.first_name}\n"
            caption += f"📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n"
            caption += f"📅 ቀን: {order_time.strftime('%Y-%m-%d')}\n"
            caption += f"🕐 ሰዓት: {order_time.strftime('%H:%M')}\n\n"
            caption += f"⚡ **እባክዎ በፍጥነት ምላሽ ይስጡ!**"
            
            order_id = None
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                p = get_placeholder()
                cursor.execute(f"SELECT id FROM pharmacy_responses WHERE pharmacy_id = (SELECT id FROM pharmacies WHERE chat_id = {p}) AND customer_id = {p} ORDER BY id DESC LIMIT 1", (chat_id, user.id))
                row = cursor.fetchone()
                order_id = row[0] if row else None
            except Exception as e:
                logging.error(f"Error getting order ID: {e}")
            finally:
                if conn:
                    conn.close()
            
            if order_id:
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{order_id}_{user.id}"),
                     InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{order_id}_{user.id}")]
                ])
            else:
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{user.id}"),
                     InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{user.id}")]
                ])
            
            if is_doc:
                tasks.append(context.bot.send_document(chat_id=chat_id, document=photo_file_id, caption=caption, reply_markup=inline_keyboard))
            else:
                tasks.append(context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=caption, reply_markup=inline_keyboard))
        
        if tasks:
            await asyncio.gather(*tasks)
            
    elif msg.text:
        med_name = msg.text
        await msg.reply_text(
            f"✅ የመድኃኒት ስም '{med_name}' ተቀብለናል! ለ{len(verified_pharmacies)} ሕጋዊ ፋርማሲዎች እየተላከ ነው...{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        
        tasks = []
        for chat_id in verified_pharmacies:
            save_result = save_pharmacy_request(chat_id, user.id, med_name)
            if not save_result:
                logging.error(f"Failed to save request for pharmacy chat_id: {chat_id}")
                continue
            
            sent_count += 1
            
            order_id = None
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                p = get_placeholder()
                cursor.execute(f"SELECT id FROM pharmacy_responses WHERE pharmacy_id = (SELECT id FROM pharmacies WHERE chat_id = {p}) AND customer_id = {p} ORDER BY id DESC LIMIT 1", (chat_id, user.id))
                row = cursor.fetchone()
                order_id = row[0] if row else None
            except Exception as e:
                logging.error(f"Error getting order ID: {e}")
            finally:
                if conn:
                    conn.close()
            
            if order_id:
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{order_id}_{user.id}"),
                     InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{order_id}_{user.id}")]
                ])
            else:
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ መድኃኒቱ አለኝ", callback_data=f"available_{user.id}"),
                     InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{user.id}")]
                ])
            
            tasks.append(context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ!**\n"
                     f"💊 የተፈለገው መድኃኒት፡ **{med_name}**\n"
                     f"👤 ከደንበኛ፡ {user.first_name}\n"
                     f"📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n"
                     f"📅 ቀን: {order_time.strftime('%Y-%m-%d')}\n"
                     f"🕐 ሰዓት: {order_time.strftime('%H:%M')}\n\n"
                     f"⚡ **እባክዎ በፍጥነት ምላሽ ይስጡ!**",
                reply_markup=inline_keyboard
            ))
        
        if tasks:
            await asyncio.gather(*tasks)

    if sent_count == 0:
        await msg.reply_text(
            "❌ ጥያቄዎን ለማስተላለፍ አልተቻለም። እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

    return ConversationHandler.END

async def handle_pharmacy_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    parts = data.split("_")
    action = parts[0]
    
    try:
        if len(parts) >= 3 and parts[1].isdigit():
            order_id = int(parts[1])
            customer_id = parts[2]
            context.user_data["responding_order_id"] = order_id
            context.user_data["responding_customer_id"] = customer_id
        elif len(parts) == 2:
            customer_id = parts[1]
            context.user_data["responding_customer_id"] = customer_id
            context.user_data["responding_order_id"] = None
        else:
            await query.edit_message_text("❌ የትዕዛዝ መረጃ አልተገኘም።")
            return ConversationHandler.END

        if action == "available":
            msg_text = (
                "✅ 'መድኃኒቱ አለኝ' የሚለው ምላሽዎ ተመዝግቧል!\n\n"
                "እባክዎ የመድኃቶቹን ዋጋ እና ተጨማሪ መረጃ ያስገቡ።"
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
    except Exception as e:
        logging.error(f"Error in handle_pharmacy_response: {e}")
        await query.edit_message_text("❌ የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።")
        return ConversationHandler.END

# ==============================================================================
# 11. OTHER HANDLERS (stats, location, pharmacy registration, etc.)
# ==============================================================================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_CHAT_ID)
    
    ai_stats = get_ai_stats()
    total_pharms, verified_pharms, pending_pharms = get_bot_statistics()
    top_meds = get_top_medicines(5)
    top_pharms = get_top_pharmacies(5)
    search_history = get_user_search_history(user_id, 5)
    unread_count = get_unread_notification_count(user_id)
    
    text = f"📊 **የስርዓት ስታቲስቲክስ**\n\n"
    text += f"🔔 ያልተነበቡ ማሳወቂያዎች: {unread_count}\n\n"
    text += f"🤖 **AI አጠቃቀም**\n"
    text += f"• ጠቅላላ ጥያቄዎች: {ai_stats['total']}\n"
    text += f"• ስኬታማ: {ai_stats['successful']}\n"
    if is_admin:
        text += f"• ስህተቶች: {ai_stats['errors']}\n"
    text += f"• አማካይ ምላሽ ጊዜ: {ai_stats['avg_time']} ሰከንድ\n\n"
    text += f"🏥 **ፋርማሲዎች**\n"
    text += f"• ጠቅላላ: {total_pharms}\n"
    text += f"• የተረጋገጡ: {verified_pharms}\n"
    if is_admin:
        text += f"• ማረጋገጫ የሚጠብቁ: {pending_pharms}\n"
    text += "\n"
    
    if top_meds:
        text += f"🏆 **ከፍተኛ መድኃኒቶች**\n"
        for idx, (med, count) in enumerate(top_meds, 1):
            text += f"• {idx}. {med} ({count} ጊዜ)\n"
        text += "\n"
    
    if top_pharms:
        text += f"🏆 **ከፍተኛ ፋርማሲዎች**\n"
        for idx, (pid, name, loc, phone, count) in enumerate(top_pharms, 1):
            text += f"• {idx}. {name} ({count} ምላሾች)\n"
        text += "\n"
    
    if search_history:
        text += f"📝 **የቅርብ ጊዜ ፍለጋዎች**\n"
        for med, date in search_history:
            date_str = date.strftime('%Y-%m-%d %H:%M') if isinstance(date, datetime) else str(date)
            text += f"• {med} - {date_str}\n"
    
    await update.message.reply_text(
        text, 
        parse_mode="Markdown", 
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ ይቅርታ! ይህንን ትዕዛዝ መጠቀም የሚችለው አድሚኑ ብቻ ነው።")
        return

    total, verified, pending = get_bot_statistics()
    ai_stats = get_ai_stats()
    
    stats_text = (
        f"📊 **የአድሚን ስታቲስቲክስ**\n\n"
        f"🤖 **AI አጠቃቀም**\n"
        f"• ጠቅላላ ጥያቄዎች: {ai_stats['total']}\n"
        f"• ስኬታማ: {ai_stats['successful']}\n"
        f"• ስህተቶች: {ai_stats['errors']}\n"
        f"• አማካይ ምላሽ ጊዜ: {ai_stats['avg_time']} ሰከንድ\n\n"
        f"🏥 **ፋርማሲዎች**\n"
        f"• ጠቅላላ የተመዘገቡ: {total}\n"
        f"• የተረጋገጡ (ሕጋዊ): {verified}\n"
        f"• ማረጋገጫ የሚጠብቁ (Pending): {pending}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 *አል-ኑር መድኃኒት አፋላጊ ሲስተም*"
    )
    await update.message.reply_text(
        stats_text, 
        parse_mode="Markdown", 
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def prompt_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_loc = context.user_data.get("user_location")
    loc_text = f"📍 የተመረጠው አካባቢ፦ {user_loc}\n\n" if user_loc else "📍 *(አካባቢ አልመረጡም)*\n\n"
    await update.message.reply_text(
        f"{loc_text}"
        f"እባክዎ የሚፈልጉትን መድኃኒት፦\n"
        f"1. በጽሑፍ የመድኃኒቱን ስም ይጻፉልን፡ ወይም\n"
        f"2. የሐኪም ማዘዣውን (Prescription) ፎቶ አንስተው ይላኩልን።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )
    return WAITING_FOR_SEARCH

async def select_location_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_loc = context.user_data.get("user_location", "አልተመረጠም")
    await update.message.reply_text(
        f"📍 **የአካባቢ መምረጫ**\n\n"
        f"አሁን የተመረጠው አካባቢ፦ {current_loc}\n\n"
        f"እባክዎ የሚገኙበትን ክፍለ ከተማ ከታች ካሉት አዝራሮች ይምረጡ ወይም ይጻፉልን፦",
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
        f"✅ አካባቢዎ በስኬት ወደ '{selected_loc}' ተቀይሯል!",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )
    return ConversationHandler.END

async def list_pharmacies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        msg = query.message
    else:
        msg = update.message
    
    if not msg:
        return

    try:
        pharmacies = get_all_verified_pharmacies()
    except Exception as e:
        logging.error(f"Error getting pharmacies: {e}")
        await msg.reply_text(
            "❌ የፋርማሲ ዝርዝር ማግኘት አልተቻለም። እባክዎ በኋላ ይሞክሩ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return

    if not pharmacies:
        await msg.reply_text(
            "ℹ️ በአሁኑ ሰዓት የተረጋገጡ ሕጋዊ ፋርማሲዎች አልተገኙም።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return

    try:
        top_pharms = get_top_pharmacies(100)
        pharm_rank = {pid: idx+1 for idx, (pid, _, _, _, _) in enumerate(top_pharms)}
    except:
        pharm_rank = {}
    
    text = "🏥 **የተመዘገቡ ሕጋዊ ፋርማሲዎች ዝርዝር**\n\n"
    
    for pharmacy in pharmacies:
        try:
            if len(pharmacy) >= 5:
                pid, name, loc, phone, hours = pharmacy[:5]
            else:
                continue
            
            rank = pharm_rank.get(pid, '—')
            rank_emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"#{rank}" if rank != '—' else ""
            
            text += f"{rank_emoji} **{name}**\n"
            text += f"   📍 አካባቢ፦ {loc}\n"
            text += f"   📞 ስልክ፦ {phone}\n"
            text += f"   🕒 የስራ ሰዓት፦ {hours}\n"
            text += "────────────────────\n"
            
            if len(text) > 3500:
                await msg.reply_text(text, parse_mode="Markdown")
                text = ""
        except Exception as e:
            logging.error(f"Error processing pharmacy: {e}")
            continue

    if text:
        await msg.reply_text(
            text, 
            parse_mode="Markdown", 
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

async def prompt_med_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 ስለ ታዘዘልዎት መድኃኒት መረጃ ማወቂያ\n\n"
        "እባክዎ ስለ መድኃኒቱ መረጃ ለማግኘት፦\n"
        "1. የመድኃኒቱን ስም በጽሑፍ ይጻፉልን፡ ወይም\n"
        "2. የሐኪም ማዘዣውን (Prescription) ፎቶ አንስተው ይላኩልን።\n\n"
        "🤖 *AI መድኃኒቱ ለምን እንደሚያገለግል፣ አወሳሰዱን እና ጥንቃቄዎችን ያብራራልዎታል።*",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return WAITING_FOR_MED_INFO

async def analyze_med_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return WAITING_FOR_MED_INFO

    if msg.text == "🏠 ወደ ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END

    if not OPENROUTER_API_KEY:
        await msg.reply_text(
            "⚠️ AI service is not configured.",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
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
        prompt = """Analyze this prescription or medicine photo and provide detailed information about the medication:
1. Name (generic and brand names if applicable)
2. Primary uses and indications
3. Dosage and administration
4. Common side effects
5. Precautions and contraindications"""
    else:
        prompt = f"""Provide detailed medical information about the following medication:

1. Name (generic and brand names if applicable)
2. Primary uses and indications
3. Dosage and administration
4. Common side effects
5. Precautions and contraindications

Medication: {text}"""

    try:
        english_response = await analyze_with_openrouter(prompt, text=text if not image_bytes else None, image_bytes=image_bytes)
        
        if english_response.startswith("❌") or english_response.startswith("⚠️"):
            await wait_msg.edit_text(english_response)
            return ConversationHandler.END

        if text:
            save_search_history(user_id, text, english_response[:200])
        else:
            save_search_history(user_id, "photo_prescription", english_response[:200])

        context.user_data["last_english_response"] = english_response

        inline_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Translate to Amharic", callback_data="translate_amharic")],
            [InlineKeyboardButton("🏠 ወደ ዋና ገጽ", callback_data="go_home")]
        ])

        await wait_msg.delete()
        await msg.reply_text(
            f"💡 Medical Information (English)\n\n{english_response}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ This is for informational purposes only. Always consult your doctor.",
            reply_markup=inline_keyboard
        )
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Error: {error_msg}")
        await wait_msg.edit_text(
            f"❌ Failed to get information.\n\n`{error_msg[:200]}`",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

    return ConversationHandler.END

async def translate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                f"💡 የመድኃኒት መረጃ (አማርኛ)\n\n{amharic_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ ይህ መረጃ ለግንዛቤ ብቻ ነው። ሁልጊዜ የሐኪምዎን መመሪያ ይከተሉ።",
                reply_markup=inline_keyboard
            )
            context.user_data["last_amharic_response"] = amharic_text
        else:
            await query.edit_message_text("❌ ትርጉሙ አልተሳካም። እባክዎ እንደገና ይሞክሩ።")
    except Exception as e:
        logging.error(f"Translation error: {e}")
        await query.edit_message_text(f"❌ የትርጉም ስህተት: {str(e)[:200]}")

async def show_english_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        f"💡 Medical Information (English)\n\n{english_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ This is for informational purposes only. Always consult your doctor.",
        reply_markup=inline_keyboard
    )

async def start_pharmacy_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏥 የፋርማሲ መመዝገቢያ ክፍል\n\nእባክዎ የፋርማሲዎን ሙሉ ስም ያስገቡ፦",
        reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
    )
    return REG_NAME

async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_name"] = msg.text
    await msg.reply_text(
        "📍 ፋርማሲዎ የሚገኝበትን ክፍለ ከተማ / አካባቢ ከታች ይምረጡ ወይም ይጻፉ፦",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
    )
    return REG_LOCATION

async def reg_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_location"] = msg.text
    await msg.reply_text("📞 ደንበኞች የሚያገኙበትን የፋርማሲ የስልክ ቁጥር ያስገቡ፦", reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    return REG_PHONE

async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_phone"] = msg.text
    await msg.reply_text("🕒 የፋርማሲዎ የስራ ሰዓት መቼ ነው?", reply_markup=ReplyKeyboardMarkup(HOURS_KEYBOARD, resize_keyboard=True))
    return REG_HOURS

async def reg_get_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_hours"] = msg.text
    await msg.reply_text("📄 የንግድ ፈቃድ ወይም የመድኃኒት መሸጫ ፈቃድ ፎቶ አንስተው ይላኩልን፦", reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    return REG_LICENSE

async def reg_get_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return REG_LICENSE
    
    if msg.text and msg.text == "🏠 ወደ ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END

    photo_file_id = None
    is_doc = False
    
    if msg.photo:
        photo_file_id = msg.photo[-1].file_id
    elif msg.document:
        if msg.document.mime_type and msg.document.mime_type.startswith('image/'):
            photo_file_id = msg.document.file_id
            is_doc = True
        else:
            await msg.reply_text(
                "❌ እባክዎ የንግድ ፈቃዱን በፎቶ መልኩ ያያይዙልን።\n\n📸 የምስል ፋይል (JPG, PNG, JPEG) ይላኩ።",
                reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
            )
            return REG_LICENSE
    else:
        await msg.reply_text(
            "❌ እባክዎ የንግድ ፈቃዱን በፎቶ መልኩ ያያይዙልን።\n\n📸 ፎቶ ወይም የምስል ፋይል ይላኩ።",
            reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True)
        )
        return REG_LICENSE

    chat_id = msg.chat_id
    name = context.user_data.get("pharm_name", "ያልተጠቀሰ")
    location = context.user_data.get("pharm_location", "ያልተጠቀሰ")
    phone = context.user_data.get("pharm_phone", "ያልተጠቀሰ")
    hours = context.user_data.get("pharm_hours", "ያልተጠቀሰ")

    try:
        pharm_id = register_pharmacy_db(chat_id, name, location, phone, hours, photo_file_id)
        
        await msg.reply_text(
            "📝 **የምዝገባ ጥያቄዎ ተቀብለናል!**\n\n"
            "⏳ የላኩት የንግድ ፈቃድ በአድሚን ተመርምሮ ሲረጋገጥ ማሳወቂያ ይደርስዎታል።\n\n"
            "✅ የፋርማሲዎ መረጃ:\n"
            f"🏥 ስም: {name}\n"
            f"📍 አካባቢ: {location}\n"
            f"📞 ስልክ: {phone}\n"
            f"🕒 የስራ ሰዓት: {hours}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

        admin_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ፈቅድ (Approve)", callback_data=f"verify_{pharm_id}")]
        ])
        
        caption_text = (
            f"🔔 **አዲስ የፋርማሲ ምዝገባ ጥያቄ!**\n\n"
            f"🏥 ስም፦ {name}\n"
            f"📍 አካባቢ፦ {location}\n"
            f"📞 ስልክ፦ {phone}\n"
            f"🕒 የስራ ሰዓት፦ {hours}\n"
            f"🆔 መለያ: {pharm_id}\n\n"
            f"ሕጋዊነቱን አረጋግጠው ይፍቀዱ፦"
        )

        try:
            if is_doc:
                await context.bot.send_document(
                    chat_id=ADMIN_CHAT_ID, 
                    document=photo_file_id, 
                    caption=caption_text, 
                    parse_mode="Markdown", 
                    reply_markup=admin_keyboard
                )
            else:
                await context.bot.send_photo(
                    chat_id=ADMIN_CHAT_ID, 
                    photo=photo_file_id, 
                    caption=caption_text, 
                    parse_mode="Markdown", 
                    reply_markup=admin_keyboard
                )
        except Exception as e:
            logging.error(f"ለአድሚን ኖቲፊኬሽን መላክ አልተቻለም፦ {e}")

    except Exception as e:
        logging.error(f"Registration error: {e}")
        await msg.reply_text(
            f"❌ ምዝገባው አልተሳካም። እባክዎ እንደገና ይሞክሩ።\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return REG_LICENSE

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
            await query.edit_message_caption(caption=f"✅ '{pharm_name}' በስኬት ተረጋግጧል! (ID: {pharmacy_id})")
        except Exception:
            await query.edit_message_text(text=f"✅ '{pharm_name}' በስኬት ተረጋግጧል! (ID: {pharmacy_id})")

        if pharm_chat_id:
            try:
                await context.bot.send_message(chat_id=pharm_chat_id, text=f"🎉 እንኳን ደስ አለዎት!\n\nየፋርማሲዎት ({pharm_name}) ምዝገባ በአድሚኑ ተረጋግጧል።")
            except Exception as e:
                logging.error(f"ለፋርማሲው ማሳወቂያ መላክ አልተቻለም፦ {e}")

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 **አል-ኑር መድኃኒት አፋላጊ - የደንበኞች ድጋፍ**\n\n"
        "ማንኛውም ጥያቄ ወይም አስተያየት ካለዎት፦\n"
        "• ስልክ፦ +251 911 00 00 00\n"
        "• ቴሌግራም፦ @AlNoorSupport",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )

async def handle_unmatched(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ ይህንን መልእክት አልገባኝም።\n\n"
        "💡 እባክዎ ከታች ካሉት አዝራሮች ይምረጡ፦",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def error_handler_func(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(msg="Exception while handling an update:", exc_info=context.error)
    
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ ወይም /start ይጫኑ።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        except:
            pass

# ==============================================================================
# 12. MAIN FUNCTION
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
        entry_points=[
            CallbackQueryHandler(handle_pharmacy_response, pattern="^(available_|not_available_)"),
            CallbackQueryHandler(respond_order_callback, pattern="^respond_order_"),
            CallbackQueryHandler(respond_from_photo_callback, pattern="^respond_from_photo_")
        ],
        states={
            WAITING_FOR_PRICE: [
                MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)
            ]
        },
        fallbacks=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)
        ],
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

    app.add_handler(CallbackQueryHandler(view_photo_callback, pattern="^view_photo_"))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("adminstats", admin_stats_command))
    app.add_handler(MessageHandler(filters.Regex("^📊 ስታቲስቲክስ$"), stats_command))
    app.add_handler(MessageHandler(filters.Regex("^📞 እገዛ / ድጋፍ$"), show_help))
    app.add_handler(MessageHandler(filters.Regex("^📋 የፋርማሲዎች ዝርዝር$"), list_pharmacies))
    app.add_handler(MessageHandler(filters.Regex("^📋 የታዘዙ መድኃኒቶች$"), show_orders))
    app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^verify_"))
    app.add_handler(CallbackQueryHandler(translate_callback, pattern="^(translate_amharic|go_home)$"))
    app.add_handler(CallbackQueryHandler(show_english_callback, pattern="^show_english$"))

    app.add_handler(loc_conv)
    app.add_handler(search_conv)
    app.add_handler(med_info_conv)
    app.add_handler(pharmacy_reply_conv)
    app.add_handler(pharmacy_conv)

    app.add_handler(MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unmatched))

    app.add_error_handler(error_handler_func)

    print("🤖 አል-ኑር መድኃኒት አፋላጊ ቦት በ PostgreSQL ዳታቤዝ ስራ ጀምሯል...")
    app.run_polling()

if __name__ == "__main__":
    main()
