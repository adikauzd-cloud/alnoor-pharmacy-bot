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
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
        
        # Add latitude and longitude columns if they don't exist
        try:
            cursor.execute("ALTER TABLE pharmacies ADD COLUMN latitude REAL")
            logging.info("✅ latitude column added")
        except Exception as e:
            logging.info(f"latitude column already exists: {e}")
        
        try:
            cursor.execute("ALTER TABLE pharmacies ADD COLUMN longitude REAL")
            logging.info("✅ longitude column added")
        except Exception as e:
            logging.info(f"longitude column already exists: {e}")
        
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
        
        # Pharmacy responses table with BIGINT for pharmacy_id
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pharmacy_responses (
                    id SERIAL PRIMARY KEY,
                    pharmacy_id BIGINT NOT NULL,
                    customer_id BIGINT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    price TEXT,
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
                    response_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """)
        
        # Add status column if it doesn't exist
        try:
            cursor.execute("ALTER TABLE pharmacy_responses ADD COLUMN status TEXT DEFAULT 'pending'")
            logging.info("✅ status column added")
        except Exception as e:
            logging.info(f"status column already exists: {e}")
        
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
        
        # ✅ NEW: Medicine Reminders table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS medicine_reminders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    dosage TEXT,
                    reminder_time TEXT NOT NULL,
                    frequency TEXT DEFAULT 'daily',
                    days_of_week TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_reminded TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS medicine_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    medicine_name TEXT NOT NULL,
                    dosage TEXT,
                    reminder_time TEXT NOT NULL,
                    frequency TEXT DEFAULT 'daily',
                    days_of_week TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_reminded TIMESTAMP
                )
            """)
        
        # ✅ NEW: Appointment Reminders table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointment_reminders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    doctor_name TEXT NOT NULL,
                    hospital_name TEXT,
                    appointment_date TEXT NOT NULL,
                    appointment_time TEXT NOT NULL,
                    notes TEXT,
                    reminder_days INTEGER DEFAULT 1,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointment_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    doctor_name TEXT NOT NULL,
                    hospital_name TEXT,
                    appointment_date TEXT NOT NULL,
                    appointment_time TEXT NOT NULL,
                    notes TEXT,
                    reminder_days INTEGER DEFAULT 1,
                    is_active BOOLEAN DEFAULT TRUE,
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

def register_pharmacy_db(chat_id, name, location, phone, operating_hours, license_photo, lat=None, lon=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
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
        except Exception as e:
            logging.warning(f"Inserting without latitude/longitude: {e}")
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"UPDATE pharmacies SET is_verified = 1 WHERE id = {placeholder}", (pharmacy_id,))
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

def delete_pharmacy_db(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"DELETE FROM pharmacy_responses WHERE pharmacy_id = {placeholder}", (pharmacy_id,))
        cursor.execute(f"DELETE FROM pharmacies WHERE id = {placeholder}", (pharmacy_id,))
        if DATABASE_URL:
            conn.commit()
        logging.info(f"✅ Pharmacy {pharmacy_id} deleted successfully")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Delete pharmacy error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_all_pharmacies_with_status():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, location, phone, is_verified, chat_id FROM pharmacies ORDER BY id DESC")
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get all pharmacies error: {e}")
        return []
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT name, location, phone, operating_hours FROM pharmacies WHERE chat_id = {placeholder} ORDER BY id DESC LIMIT 1", (chat_id,))
        row = cursor.fetchone()
        return row
    except Exception as e:
        logging.error(f"Get pharmacy by chat ID error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_pharmacy_id_by_chat_id(chat_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT id FROM pharmacies WHERE chat_id = {placeholder} ORDER BY id DESC LIMIT 1", (chat_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logging.error(f"Get pharmacy ID by chat ID error: {e}")
        return None
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
        try:
            cursor.execute("SELECT id, name, location, phone, operating_hours, latitude, longitude FROM pharmacies WHERE is_verified = 1")
        except:
            cursor.execute("SELECT id, name, location, phone, operating_hours FROM pharmacies WHERE is_verified = 1")
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO search_history (user_id, medicine_name, result_summary)
            VALUES ({placeholder}, {placeholder}, {placeholder})
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT medicine_name, search_date FROM search_history 
            WHERE user_id = {placeholder} ORDER BY search_date DESC LIMIT {placeholder}
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO ai_logs 
            (user_id, request_type, request_data, response_data, status_code, error_message, response_time)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
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
# 6. DISTANCE CALCULATION AND TOP PHARMACIES
# ==============================================================================

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    try:
        lat1_rad = math.radians(float(lat1))
        lat2_rad = math.radians(float(lat2))
        delta_lat = math.radians(float(lat2) - float(lat1))
        delta_lon = math.radians(float(lon2) - float(lon1))
        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return round(R * c, 2)
    except:
        return None

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
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get top pharmacies error: {e}")
        return []
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 7. NOTIFICATION SYSTEM
# ==============================================================================

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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, type, message, is_read, created_at FROM notifications 
            WHERE user_id = {placeholder} 
            ORDER BY created_at DESC LIMIT {placeholder}
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"UPDATE notifications SET is_read = TRUE WHERE id = {placeholder}", (notification_id,))
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
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id = {placeholder} AND is_read = FALSE", (user_id,))
        count = cursor.fetchone()[0]
        return count
    except Exception as e:
        logging.error(f"Get unread count error: {e}")
        return 0
    finally:
        if conn:
            conn.close()

async def send_telegram_notification(context, user_id, message):
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🔔 {message}",
            parse_mode="Markdown"
        )
        return True
    except Exception as e:
        logging.error(f"Telegram notification error: {e}")
        return False

async def send_order_notification(context, pharmacy_chat_id, customer_id, medicine_name, order_time):
    message = (
        f"🆕 **አዲስ የመድኃኒት ትዕዛዝ!**\n\n"
        f"💊 መድኃኒት: {medicine_name}\n"
        f"📅 የታዘዘበት ቀን: {order_time.strftime('%Y-%m-%d')}\n"
        f"🕐 የታዘዘበት ሰዓት: {order_time.strftime('%H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *እባክዎ በፍጥነት ምላሽ ይስጡ!*"
    )
    save_notification(pharmacy_chat_id, None, "order", f"አዲስ ትዕዛዝ: {medicine_name} - ከደንበኛ: {customer_id}")
    await send_telegram_notification(context, pharmacy_chat_id, message)

# ==============================================================================
# 8. SAVE PHARMACY REQUEST
# ==============================================================================

def save_pharmacy_request(pharmacy_chat_id, customer_id, medicine_name):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
        if not pharmacy_internal_id:
            logging.error(f"Pharmacy not found for chat_id: {pharmacy_chat_id}")
            return False
        
        try:
            pharmacy_internal_id = int(pharmacy_internal_id)
            customer_id = int(customer_id)
        except Exception as e:
            logging.error(f"Conversion error: {e}")
            return False
        
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO pharmacy_responses (pharmacy_id, customer_id, medicine_name, price, status)
            VALUES ({placeholder}, {placeholder}, {placeholder}, NULL, 'pending')
        """, (pharmacy_internal_id, customer_id, medicine_name))
        
        if DATABASE_URL:
            conn.commit()
        logging.info(f"✅ Request saved: pharmacy_id={pharmacy_internal_id}, customer={customer_id}, medicine={medicine_name}")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Save pharmacy request error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_order_status(order_id, status):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"UPDATE pharmacy_responses SET status = {placeholder} WHERE id = {placeholder}", (status, order_id))
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

# ==============================================================================
# 9. REMINDER FUNCTIONS
# ==============================================================================

def save_medicine_reminder_db(user_id, medicine_name, dosage, reminder_time, frequency, days_of_week=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO medicine_reminders 
            (user_id, medicine_name, dosage, reminder_time, frequency, days_of_week, is_active)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, TRUE)
        """, (user_id, medicine_name, dosage, reminder_time, frequency, days_of_week))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Save medicine reminder error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_user_medicine_reminders(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, medicine_name, dosage, reminder_time, frequency, is_active
            FROM medicine_reminders 
            WHERE user_id = {placeholder} AND is_active = TRUE
            ORDER BY reminder_time
        """, (user_id,))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get medicine reminders error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def delete_medicine_reminder_db(reminder_id, user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"DELETE FROM medicine_reminders WHERE id = {placeholder} AND user_id = {placeholder}", (reminder_id, user_id))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Delete medicine reminder error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def save_appointment_reminder_db(user_id, doctor_name, hospital_name, appointment_date, appointment_time, reminder_days, notes=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO appointment_reminders 
            (user_id, doctor_name, hospital_name, appointment_date, appointment_time, notes, reminder_days, is_active)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, TRUE)
        """, (user_id, doctor_name, hospital_name, appointment_date, appointment_time, notes, reminder_days))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Save appointment reminder error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_user_appointment_reminders(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, doctor_name, hospital_name, appointment_date, appointment_time, reminder_days, is_active
            FROM appointment_reminders 
            WHERE user_id = {placeholder} AND is_active = TRUE
            ORDER BY appointment_date, appointment_time
        """, (user_id,))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get appointment reminders error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def delete_appointment_reminder_db(reminder_id, user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"DELETE FROM appointment_reminders WHERE id = {placeholder} AND user_id = {placeholder}", (reminder_id, user_id))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Delete appointment reminder error: {e}")
        return False
    finally:
        if conn:
            conn.close()

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """⏰ Check and send reminders"""
    current_time = datetime.now()
    current_time_str = current_time.strftime("%H:%M")
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check medicine reminders
        cursor.execute("SELECT id, user_id, medicine_name, dosage, reminder_time, frequency FROM medicine_reminders WHERE is_active = TRUE")
        reminders = cursor.fetchall()
        
        for rem in reminders:
            rem_id, user_id, medicine, dosage, rem_time, frequency = rem
            if rem_time == current_time_str:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"💊 **የመድኃኒት ማስታወሻ!**\n\n"
                             f"⏰ ሰዓቱ {rem_time} ነው!\n"
                             f"💊 መድኃኒት: {medicine}\n"
                             f"📊 መጠን: {dosage if dosage else 'ያልተጠቀሰ'}\n\n"
                             f"✅ መድኃኒትዎን መውሰድ አይርሱ!",
                        parse_mode="Markdown"
                    )
                    cursor.execute(f"UPDATE medicine_reminders SET last_reminded = CURRENT_TIMESTAMP WHERE id = {rem_id}")
                    if DATABASE_URL:
                        conn.commit()
                except Exception as e:
                    logging.error(f"Error sending reminder: {e}")
        
        # Check appointment reminders
        cursor.execute("SELECT id, user_id, doctor_name, appointment_date, reminder_days FROM appointment_reminders WHERE is_active = TRUE")
        appointments = cursor.fetchall()
        
        current_date = current_time.date()
        for app in appointments:
            app_id, user_id, doctor, app_date_str, reminder_days = app
            try:
                app_date = datetime.strptime(app_date_str, "%Y-%m-%d").date()
                days_until = (app_date - current_date).days
                if days_until == reminder_days:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"📅 **የሐኪም ቀጠሮ ማስታወሻ!**\n\n"
                             f"👨‍⚕️ ሐኪም: {doctor}\n"
                             f"📅 ቀን: {app_date_str}\n\n"
                             f"⚠️ ከ{reminder_days} ቀን ቀደም ብለን እናሳስብዎታለን!",
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logging.error(f"Error processing appointment: {e}")
    except Exception as e:
        logging.error(f"Check reminders error: {e}")
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 10. STATES & KEYBOARDS
# ==============================================================================
WAITING_FOR_SEARCH = 1
WAITING_FOR_PHARMACY_PRICE = 2
WAITING_FOR_ORDER_PRICE = 3
WAITING_FOR_LOCATION_SET = 4
WAITING_FOR_MED_INFO = 5
WAITING_FOR_MEDICINE_REMINDER = 6
WAITING_FOR_APPOINTMENT = 7

REG_NAME = 10
REG_LOCATION = 11
REG_PHONE = 12
REG_HOURS = 14
REG_LICENSE = 13

MAIN_KEYBOARD = [
    ["🔍 መድኃኒት ፈልግ", "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ"],
    ["📍 አካባቢ ምረጥ", "📋 የፋርማሲዎች ዝርዝር"],
    ["🏥 ፋርማሲ መዝግብ", "📋 የታዘዙ መድኃኒቶች"],
    ["💊 መድኃኒት ማስታወሻ", "📅 የሐኪም ቀጠሮ"],
    ["📋 ማስታወሻዎች", "📞 እገዛ / ድጋፍ"],
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
# 11. TRANSLATION FUNCTION
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
# 12. AI HANDLER
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
# 13. HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "ወዳጄ"
    user_id = update.effective_user.id
    unread_count = get_unread_notification_count(user_id)
    
    welcome_text = (
        f"👋 ሰላም {user_name}! ወደ አል-ኑር መድኃኒት አፋላጊ በደህና መጡ።\n\n"
        f"━━━ ⚖️ ሕጋዊ ማስታወቂያ ━━━\n"
        f"• 🏥 ከሕጋዊና ፈቃድ ካላቸው ፋርማሲዎች ጋር ብቻ ያገናኛል።\n"
        f"• 📄 መድኃኒት ሲገዙ የሐኪም ማዘዣ (Prescription) ይያዙ።\n"
        f"• ℹ️ ይህ ቦት የመረጃ ማገናኛ እንጂ Pharmacy አይደለም።\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 የሚፈልጉትን አገልግሎት ከታች ይምረጡ፦"
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

# ==============================================================================
# REMINDER HANDLERS
# ==============================================================================

async def add_medicine_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💊 Add new medicine reminder"""
    await update.message.reply_text(
        "💊 **አዲስ የመድኃኒት ማስታወሻ**\n\n"
        "እባክዎ የሚከተሉትን መረጃዎች በዚህ ቅርጸት ያስገቡ፦\n\n"
        "`መድኃኒት: ፓራሲታሞል`\n"
        "`መጠን: 500mg`\n"
        "`ሰዓት: 08:00`\n"
        "`ድግግሞሽ: በየቀኑ`\n\n"
        "ለምሳሌ፦\n"
        "`መድኃኒት: ፓራሲታሞል, መጠን: 500mg, ሰዓት: 08:00, ድግግሞሽ: በየቀኑ`",
        parse_mode="Markdown"
    )
    return WAITING_FOR_MEDICINE_REMINDER

async def save_medicine_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💾 Save medicine reminder"""
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        parts = text.split(',')
        medicine = ""
        dosage = ""
        reminder_time = ""
        frequency = "በየቀኑ"
        
        for part in parts:
            part = part.strip()
            if part.startswith("መድኃኒት:") or part.startswith("መድሃኒት:"):
                medicine = part.split(":", 1)[1].strip()
            elif part.startswith("መጠን:"):
                dosage = part.split(":", 1)[1].strip()
            elif part.startswith("ሰዓት:"):
                reminder_time = part.split(":", 1)[1].strip()
            elif part.startswith("ድግግሞሽ:"):
                frequency = part.split(":", 1)[1].strip()
        
        if not medicine or not reminder_time:
            await update.message.reply_text(
                "❌ እባክዎ ቢያንስ 'መድኃኒት' እና 'ሰዓት' ያስገቡ።\n\n"
                "ምሳሌ: `መድኃኒት: ፓራሲታሞል, ሰዓት: 08:00`",
                parse_mode="Markdown"
            )
            return WAITING_FOR_MEDICINE_REMINDER
        
        if save_medicine_reminder_db(user_id, medicine, dosage, reminder_time, frequency):
            await update.message.reply_text(
                f"✅ **ማስታወሻ ተመዝግቧል!**\n\n"
                f"💊 መድኃኒት: {medicine}\n"
                f"📊 መጠን: {dosage if dosage else 'አልተጠቀሰም'}\n"
                f"⏰ ሰዓት: {reminder_time}\n"
                f"🔄 ድግግሞሽ: {frequency}\n\n"
                f"📌 በየቀኑ በ {reminder_time} ላይ ማሳሰቢያ ይደርስዎታል!",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        else:
            await update.message.reply_text("❌ ማስታወሻውን ማስቀመጥ አልተቻለም። እባክዎ እንደገና ይሞክሩ።")
        
    except Exception as e:
        logging.error(f"Error saving reminder: {e}")
        await update.message.reply_text(f"❌ ስህተት ተከስቷል። እባክዎ እንደገና ይሞክሩ።")
    
    return ConversationHandler.END

async def add_appointment_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📅 Add new appointment reminder"""
    await update.message.reply_text(
        "📅 **አዲስ የሐኪም ቀጠሮ ማስታወሻ**\n\n"
        "እባክዎ የሚከተሉትን መረጃዎች ያስገቡ፦\n\n"
        "`ሐኪም: ዶ/ር አበበ`\n"
        "`ሆስፒታል: ቅዱስ ጳውሎስ`\n"
        "`ቀን: 2026-08-15`\n"
        "`ሰዓት: 14:30`\n"
        "`ማሳሰቢያ: 2` (ከቀን በፊት)\n\n"
        "ለምሳሌ፦\n"
        "`ሐኪም: ዶ/ር አበበ, ቀን: 2026-08-15, ሰዓት: 14:30`",
        parse_mode="Markdown"
    )
    return WAITING_FOR_APPOINTMENT

async def save_appointment_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💾 Save appointment reminder"""
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        parts = text.split(',')
        doctor = ""
        hospital = ""
        app_date = ""
        app_time = ""
        reminder_days = 1
        
        for part in parts:
            part = part.strip()
            if part.startswith("ሐኪም:") or part.startswith("ሀኪም:"):
                doctor = part.split(":", 1)[1].strip()
            elif part.startswith("ሆስፒታል:"):
                hospital = part.split(":", 1)[1].strip()
            elif part.startswith("ቀን:"):
                app_date = part.split(":", 1)[1].strip()
            elif part.startswith("ሰዓት:"):
                app_time = part.split(":", 1)[1].strip()
            elif part.startswith("ማሳሰቢያ:"):
                reminder_days = int(part.split(":", 1)[1].strip())
        
        if not doctor or not app_date or not app_time:
            await update.message.reply_text(
                "❌ እባክዎ 'ሐኪም', 'ቀን' እና 'ሰዓት' ያስገቡ።"
            )
            return WAITING_FOR_APPOINTMENT
        
        if save_appointment_reminder_db(user_id, doctor, hospital, app_date, app_time, reminder_days):
            await update.message.reply_text(
                f"✅ **ቀጠሮ ተመዝግቧል!**\n\n"
                f"👨‍⚕️ ሐኪም: {doctor}\n"
                f"🏥 ሆስፒታል: {hospital if hospital else 'አልተጠቀሰም'}\n"
                f"📅 ቀን: {app_date}\n"
                f"⏰ ሰዓት: {app_time}\n"
                f"🔔 ማሳሰቢያ: ከ{reminder_days} ቀን በፊት\n\n"
                f"📌 ቀጠሮዎ ከ{reminder_days} ቀን በፊት ይታሰብዎታል!",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        else:
            await update.message.reply_text("❌ ቀጠሮውን ማስቀመጥ አልተቻለም። እባክዎ እንደገና ይሞክሩ።")
        
    except Exception as e:
        logging.error(f"Error saving appointment: {e}")
        await update.message.reply_text(f"❌ ስህተት ተከስቷል። እባክዎ እንደገና ይሞክሩ።")
    
    return ConversationHandler.END

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📋 List all reminders"""
    user_id = update.effective_user.id
    
    medicine_reminders = get_user_medicine_reminders(user_id)
    appointment_reminders = get_user_appointment_reminders(user_id)
    
    if not medicine_reminders and not appointment_reminders:
        await update.message.reply_text(
            "📋 ምንም ንቁ ማስታወሻዎች የሉም።\n\n"
            "💊 አዲስ የመድኃኒት ማስታወሻ ለመፍጠር '💊 መድኃኒት ማስታወሻ' ይጫኑ።\n"
            "📅 አዲስ የሐኪም ቀጠሮ ለመፍጠር '📅 የሐኪም ቀጠሮ' ይጫኑ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    
    text = "📋 **የእርስዎ ማስታወሻዎች**\n\n"
    
    if medicine_reminders:
        text += "💊 **የመድኃኒት ማስታወሻዎች**\n"
        for rem in medicine_reminders:
            rem_id, name, dosage, time, freq, active = rem
            text += f"   • {name} - ⏰ {time} - {freq}\n"
            if dosage:
                text += f"     📊 {dosage}\n"
            text += f"     🆔 ለመሰረዝ: `ሰርዝ መድሃኒት {rem_id}`\n"
        text += "\n"
    
    if appointment_reminders:
        text += "📅 **የሐኪም ቀጠሮዎች**\n"
        for app in appointment_reminders:
            app_id, doctor, hospital, date, time, days, active = app
            text += f"   • {doctor} - 📅 {date} ⏰ {time}\n"
            if hospital:
                text += f"     🏥 {hospital}\n"
            text += f"     🆔 ለመሰረዝ: `ሰርዝ ቀጠሮ {app_id}`\n"
    
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🗑️ Delete a reminder"""
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        if text.startswith("ሰርዝ መድሃኒት"):
            rem_id = int(text.split()[2])
            if delete_medicine_reminder_db(rem_id, user_id):
                await update.message.reply_text(f"✅ የመድኃኒት ማስታወሻ #{rem_id} ተሰርዟል!")
            else:
                await update.message.reply_text("❌ ማስታወሻውን መሰረዝ አልተቻለም።")
        
        elif text.startswith("ሰርዝ ቀጠሮ"):
            rem_id = int(text.split()[2])
            if delete_appointment_reminder_db(rem_id, user_id):
                await update.message.reply_text(f"✅ የሐኪም ቀጠሮ #{rem_id} ተሰርዟል!")
            else:
                await update.message.reply_text("❌ ቀጠሮውን መሰረዝ አልተቻለም።")
        else:
            await update.message.reply_text(
                "❌ ትክክለኛ ትዕዛዝ ያስገቡ።\n\n"
                "ምሳሌ: `ሰርዝ መድሃኒት 5`\n"
                "ምሳሌ: `ሰርዝ ቀጠሮ 3`"
            )
    except Exception as e:
        logging.error(f"Error deleting reminder: {e}")
        await update.message.reply_text("❌ ስህተት ተከስቷል። እባክዎ እንደገና ይሞክሩ።")

# ==============================================================================
# EXISTING HANDLERS (truncated for space - keep your existing handlers)
# ==============================================================================

# The following handlers from your original code remain unchanged:
# - show_orders
# - respond_order_callback
# - prompt_med_info
# - translate_callback
# - show_english_callback
# - analyze_med_info
# - stats_command
# - admin_manage_pharmacies
# - admin_delete_pharmacy_callback
# - admin_confirm_delete_callback
# - admin_cancel_delete_callback
# - list_pharmacies
# - select_location_prompt
# - save_user_location
# - prompt_search
# - handle_customer_request
# - handle_pharmacy_response
# - receive_price_details
# - handle_admin_approval
# - start_pharmacy_reg
# - reg_get_name
# - reg_get_location
# - reg_get_phone
# - reg_get_hours
# - reg_get_license
# - show_help
# - error_handler_func

# ==============================================================================
# 14. MAIN FUNCTION
# ==============================================================================

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # ============================================================
    # Conversation Handlers
    # ============================================================
    
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
        states={WAITING_FOR_PHARMACY_PRICE: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)]},
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

    order_response_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(respond_order_callback, pattern="^respond_order_")],
        states={WAITING_FOR_ORDER_PRICE: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
        per_message=False,
    )

    # ✅ NEW: Reminder conversation handlers
    medicine_reminder_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💊 መድኃኒት ማስታወሻ$"), add_medicine_reminder)],
        states={WAITING_FOR_MEDICINE_REMINDER: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_medicine_reminder)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    )

    appointment_reminder_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📅 የሐኪም ቀጠሮ$"), add_appointment_reminder)],
        states={WAITING_FOR_APPOINTMENT: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_appointment_reminder)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    )

    # ============================================================
    # Add all handlers
    # ============================================================
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("manage", admin_manage_pharmacies))
    
    app.add_handler(MessageHandler(filters.Regex("^📞 እገዛ / ድጋፍ$"), show_help))
    app.add_handler(MessageHandler(filters.Regex("^📋 የፋርማሲዎች ዝርዝር$"), list_pharmacies))
    app.add_handler(MessageHandler(filters.Regex("^📋 የታዘዙ መድኃኒቶች$"), show_orders))
    app.add_handler(MessageHandler(filters.Regex("^📋 ማስታወሻዎች$"), list_reminders))
    
    # Reminder delete handler
    app.add_handler(MessageHandler(filters.Regex("^ሰርዝ"), delete_reminder))
    
    app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^verify_"))
    app.add_handler(CallbackQueryHandler(translate_callback, pattern="^(translate_amharic|go_home)$"))
    app.add_handler(CallbackQueryHandler(show_english_callback, pattern="^show_english$"))
    
    app.add_handler(CallbackQueryHandler(admin_delete_pharmacy_callback, pattern="^delete_pharm_"))
    app.add_handler(CallbackQueryHandler(admin_confirm_delete_callback, pattern="^confirm_delete_"))
    app.add_handler(CallbackQueryHandler(admin_cancel_delete_callback, pattern="^cancel_delete$"))
    
    app.add_handler(order_response_conv)
    app.add_handler(loc_conv)
    app.add_handler(search_conv)
    app.add_handler(med_info_conv)
    app.add_handler(pharmacy_reply_conv)
    app.add_handler(pharmacy_conv)
    app.add_handler(medicine_reminder_conv)
    app.add_handler(appointment_reminder_conv)

    app.add_handler(MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start))

    app.add_error_handler(error_handler_func)

    # ============================================================
    # Start scheduler for reminders
    # ============================================================
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_reminders,
        'interval',
        minutes=1,
        args=[app]
    )
    scheduler.start()
    logging.info("✅ Reminder scheduler started")

    print("🤖 አል-ኑር መድኃኒት አፋላጊ ቦት በ PostgreSQL ዳታቤዝ ስራ ጀምሯል...")
    app.run_polling()

if __name__ == "__main__":
    main()