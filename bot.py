import logging
import os
import threading
import psycopg2
import requests
import json
import base64
import math
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
        
        # Pharmacy responses table
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
        
        # Medicine Reminders table
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
        
        # Pharmacy Stock table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pharmacy_stock (
                    id SERIAL PRIMARY KEY,
                    pharmacy_id BIGINT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    quantity INTEGER DEFAULT 0,
                    price REAL,
                    expiry_date DATE,
                    batch_number TEXT,
                    reorder_level INTEGER DEFAULT 5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pharmacy_stock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pharmacy_id INTEGER NOT NULL,
                    medicine_name TEXT NOT NULL,
                    quantity INTEGER DEFAULT 0,
                    price REAL,
                    expiry_date TEXT,
                    batch_number TEXT,
                    reorder_level INTEGER DEFAULT 5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
                )
            """)
        
        # Stock History table
        if DATABASE_URL:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_history (
                    id SERIAL PRIMARY KEY,
                    stock_id BIGINT NOT NULL,
                    pharmacy_id BIGINT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    old_quantity INTEGER,
                    new_quantity INTEGER,
                    change_amount INTEGER,
                    change_type TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL,
                    pharmacy_id INTEGER NOT NULL,
                    medicine_name TEXT NOT NULL,
                    old_quantity INTEGER,
                    new_quantity INTEGER,
                    change_amount INTEGER,
                    change_type TEXT,
                    note TEXT,
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
# 4. STOCK MANAGEMENT FUNCTIONS
# ==============================================================================

def add_medicine_to_stock(pharmacy_id, medicine_name, quantity, price=None, expiry_date=None, batch_number=None, reorder_level=5):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        
        cursor.execute(f"""
            SELECT id, quantity FROM pharmacy_stock 
            WHERE pharmacy_id = {placeholder} AND LOWER(medicine_name) = LOWER({placeholder})
        """, (pharmacy_id, medicine_name))
        existing = cursor.fetchone()
        
        if existing:
            stock_id, current_qty = existing
            new_qty = current_qty + quantity
            cursor.execute(f"""
                UPDATE pharmacy_stock 
                SET quantity = {placeholder}, updated_at = CURRENT_TIMESTAMP
                WHERE id = {placeholder}
            """, (new_qty, stock_id))
            
            cursor.execute(f"""
                INSERT INTO stock_history (stock_id, pharmacy_id, medicine_name, old_quantity, new_quantity, change_amount, change_type)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, 'added')
            """, (stock_id, pharmacy_id, medicine_name, current_qty, new_qty, quantity))
        else:
            if DATABASE_URL:
                cursor.execute(f"""
                    INSERT INTO pharmacy_stock 
                    (pharmacy_id, medicine_name, quantity, price, expiry_date, batch_number, reorder_level)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                    RETURNING id
                """, (pharmacy_id, medicine_name, quantity, price, expiry_date, batch_number, reorder_level))
                stock_id = cursor.fetchone()[0]
            else:
                cursor.execute(f"""
                    INSERT INTO pharmacy_stock 
                    (pharmacy_id, medicine_name, quantity, price, expiry_date, batch_number, reorder_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (pharmacy_id, medicine_name, quantity, price, expiry_date, batch_number, reorder_level))
                stock_id = cursor.lastrowid
            
            cursor.execute(f"""
                INSERT INTO stock_history (stock_id, pharmacy_id, medicine_name, old_quantity, new_quantity, change_amount, change_type)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, 'added')
            """, (stock_id, pharmacy_id, medicine_name, 0, quantity, quantity))
        
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Add medicine to stock error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_pharmacy_stock(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, medicine_name, quantity, price, expiry_date, batch_number, reorder_level
            FROM pharmacy_stock 
            WHERE pharmacy_id = {placeholder}
            ORDER BY medicine_name
        """, (pharmacy_id,))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get pharmacy stock error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def update_stock_quantity(stock_id, pharmacy_id, new_quantity, change_type='adjusted', note=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        
        cursor.execute(f"""
            SELECT quantity, medicine_name FROM pharmacy_stock 
            WHERE id = {placeholder} AND pharmacy_id = {placeholder}
        """, (stock_id, pharmacy_id))
        row = cursor.fetchone()
        if not row:
            return False
        
        old_quantity, medicine_name = row
        change_amount = new_quantity - old_quantity
        
        cursor.execute(f"""
            UPDATE pharmacy_stock 
            SET quantity = {placeholder}, updated_at = CURRENT_TIMESTAMP
            WHERE id = {placeholder} AND pharmacy_id = {placeholder}
        """, (new_quantity, stock_id, pharmacy_id))
        
        cursor.execute(f"""
            INSERT INTO stock_history 
            (stock_id, pharmacy_id, medicine_name, old_quantity, new_quantity, change_amount, change_type, note)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
        """, (stock_id, pharmacy_id, medicine_name, old_quantity, new_quantity, change_amount, change_type, note))
        
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Update stock quantity error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_low_stock_items(pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, medicine_name, quantity, reorder_level
            FROM pharmacy_stock 
            WHERE pharmacy_id = {placeholder} AND quantity <= reorder_level
            ORDER BY quantity ASC
        """, (pharmacy_id,))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get low stock items error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_stock_history(stock_id, limit=20):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT change_type, old_quantity, new_quantity, change_amount, note, created_at
            FROM stock_history 
            WHERE stock_id = {placeholder}
            ORDER BY created_at DESC
            LIMIT {placeholder}
        """, (stock_id, limit))
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        logging.error(f"Get stock history error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def delete_stock_item(stock_id, pharmacy_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"DELETE FROM pharmacy_stock WHERE id = {placeholder} AND pharmacy_id = {placeholder}", (stock_id, pharmacy_id))
        if DATABASE_URL:
            conn.commit()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Delete stock item error: {e}")
        return False
    finally:
        if conn:
            conn.close()

# ==============================================================================
# 5. REMINDER FUNCTIONS
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

# ==============================================================================
# 6. SEARCH HISTORY FUNCTIONS
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
# 7. AI LOGS FUNCTIONS
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
# 8. DISTANCE CALCULATION AND TOP PHARMACIES
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
# 9. NOTIFICATION SYSTEM
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
# 10. SAVE PHARMACY REQUEST
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
# 11. STATES & KEYBOARDS - PROFESSIONAL UI
# ==============================================================================
WAITING_FOR_SEARCH = 1
WAITING_FOR_PHARMACY_PRICE = 2
WAITING_FOR_ORDER_PRICE = 3
WAITING_FOR_LOCATION_SET = 4
WAITING_FOR_MED_INFO = 5
WAITING_FOR_MEDICINE_REMINDER = 6
STOCK_ADD = 20
STOCK_UPDATE = 21
STOCK_VIEW = 22

REG_NAME = 10
REG_LOCATION = 11
REG_PHONE = 12
REG_HOURS = 14
REG_LICENSE = 13

MAIN_KEYBOARD = [
    ["🔍 መድኃኒት ፈልግ", "📖 የመድኃኒት መረጃ"],
    ["📍 አካባቢ ምረጥ", "📋 ፋርማሲዎች"],
    ["🏥 ፋርማሲ መዝግብ", "📋 ትዕዛዞች"],
    ["📦 ክምችት", "💊 ማሳሰቢያ"],
    ["📞 ድጋፍ", "🏠 ዋና ገጽ"]
]

LOCATION_KEYBOARD = [
    ["ቦሌ", "አራዳ", "አዲስ ከተማ"],
    ["የካ", "ቂርቆስ", "ልደታ"],
    ["ኮልፌ ቀራኒዮ", "ንፋስ ስልክ", "አቃቂ ቃሊቲ"],
    ["🏠 ዋና ገጽ"]
]

HOURS_KEYBOARD = [
    ["🕒 24 ሰዓት ክፍት"],
    ["☀️ በቀን ብቻ (ከጠዋቱ 2:00 - ማታ 2:00)"],
    ["🏠 ዋና ገጽ"]
]

STOCK_KEYBOARD = [
    ["📦 መድኃኒት ጨምር", "📊 ክምችት ማየት"],
    ["🔁 ክምችት አስተካክል", "⚠️ ዝቅተኛ ክምችት"],
    ["📋 የመድኃኒት ታሪክ", "🗑️ መድኃኒት ሰርዝ"],
    ["🏠 ዋና ገጽ"]
]

# ==============================================================================
# 12. TRANSLATION FUNCTION
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
# 13. AI HANDLER
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
# 14. HANDLERS
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
# STOCK MANAGEMENT HANDLERS
# ==============================================================================

async def stock_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📦 የክምችት አስተዳደር ሜኑ"""
    try:
        logging.info("📦 stock_menu function called!")  # ✅ ይህን ያክሉ
        
        pharmacy_chat_id = update.effective_user.id
        logging.info(f"👤 User ID: {pharmacy_chat_id}")
        
        # Check if registered as pharmacy
        pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
        logging.info(f"🏥 Pharmacy info: {pharm_info}")
        
        if not pharm_info:
            await update.message.reply_text(
                "⚠️ ይህ አገልግሎት ለተመዘገቡ ፋርማሲዎች ብቻ ነው!\n\n"
                "📝 እባክዎ መጀመሪያ ፋርማሲዎን ይመዝገቡ (🏥 ፋርማሲ መዝግብ)",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return
        
        pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
        logging.info(f"🆔 Pharmacy internal ID: {pharmacy_internal_id}")
        
        if not pharmacy_internal_id:
            await update.message.reply_text(
                "⚠️ የፋርማሲ መለያ አልተገኘም። እባክዎ እንደገና ይመዝገቡ።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return
        
        # Check if verified
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            placeholder = "%s" if DATABASE_URL else "?"
            cursor.execute(f"SELECT is_verified FROM pharmacies WHERE id = {placeholder}", (pharmacy_internal_id,))
            row = cursor.fetchone()
            is_verified = row[0] if row else 0
            logging.info(f"✅ Is verified: {is_verified}")
            
            if is_verified != 1:
                await update.message.reply_text(
                    "⏳ ፋርማሲዎ ገና አልተረጋገጠም!\n\n"
                    "📝 እባክዎ አስተዳዳሪው ፋርማሲዎን እስኪያረጋግጥ ይጠብቁ።",
                    reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                )
                return
        except Exception as e:
            logging.error(f"Error checking verification: {e}")
        finally:
            if conn:
                conn.close()
        
        # Show stock menu
        stock_keyboard = [
            ["📦 መድኃኒት ጨምር", "📊 ክምችት ማየት"],
            ["🔁 ክምችት አስተካክል", "⚠️ ዝቅተኛ ክምችት"],
            ["📋 የመድኃኒት ታሪክ", "🗑️ መድኃኒት ሰርዝ"],
            ["🏠 ዋና ገጽ"]
        ]
        
        await update.message.reply_text(
            f"📦 **የክምችት አስተዳደር**\n\n"
            f"🏥 ፋርማሲ: {pharm_info[0]}\n"
            f"📍 አካባቢ: {pharm_info[1]}\n\n"
            f"👇 የሚፈልጉትን አገልግሎት ይምረጡ:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(stock_keyboard, resize_keyboard=True)
        )
        
        logging.info("✅ Stock menu sent successfully!")  # ✅ ይህን ያክሉ
        
    except Exception as e:
        logging.error(f"❌ Error in stock_menu: {e}")
        await update.message.reply_text(
            f"❌ ስህተት ተከስቷል። እባክዎ እንደገና ይሞክሩ።\n\n`{str(e)[:200]}`",
            parse_mode="Markdown"
        )
        reply_markup=ReplyKeyboardMarkup(STOCK_KEYBOARD, resize_keyboard=True)
    )

async def add_stock_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📦 አዲስ መድኃኒት ወደ ክምችት መጨመር"""
    await update.message.reply_text(
        "📦 **አዲስ መድኃኒት ወደ ክምችት መጨመር**\n\n"
        "እባክዎ የሚከተሉትን መረጃዎች በዚህ ቅርጸት ያስገቡ፦\n\n"
        "`ስም: ፓራሲታሞል`\n"
        "`ብዛት: 100`\n"
        "`ዋጋ: 50` (አማራጭ)\n"
        "`የሚያበቃበት ቀን: 2026-12-31` (አማራጭ)\n\n"
        "ለምሳሌ፦\n"
        "`ስም: ፓራሲታሞል, ብዛት: 100, ዋጋ: 50`",
        parse_mode="Markdown"
    )
    return STOCK_ADD

async def save_stock_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💾 መድኃኒት ወደ ክምችት ማስቀመጥ"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return ConversationHandler.END
    
    text = update.message.text
    
    try:
        parts = text.split(',')
        medicine_name = ""
        quantity = 0
        price = None
        expiry_date = None
        
        for part in parts:
            part = part.strip()
            if part.startswith("ስም:"):
                medicine_name = part.split(":", 1)[1].strip()
            elif part.startswith("ብዛት:"):
                quantity = int(part.split(":", 1)[1].strip())
            elif part.startswith("ዋጋ:"):
                price = float(part.split(":", 1)[1].strip())
            elif part.startswith("የሚያበቃበት ቀን:"):
                expiry_date = part.split(":", 1)[1].strip()
        
        if not medicine_name or quantity <= 0:
            await update.message.reply_text(
                "❌ እባክዎ 'ስም' እና 'ብዛት' ያስገቡ።\n\n"
                "ምሳሌ: `ስም: ፓራሲታሞል, ብዛት: 100`",
                parse_mode="Markdown"
            )
            return STOCK_ADD
        
        if add_medicine_to_stock(pharmacy_internal_id, medicine_name, quantity, price, expiry_date):
            await update.message.reply_text(
                f"✅ **መድኃኒት በስኬት ተጨምሯል!**\n\n"
                f"💊 መድኃኒት: {medicine_name}\n"
                f"📊 ብዛት: {quantity}\n"
                f"💰 ዋጋ: {price if price else 'አልተጠቀሰም'}\n"
                f"📅 የሚያበቃበት: {expiry_date if expiry_date else 'አልተጠቀሰም'}\n\n"
                f"📌 ክምችትዎ በተሳካ ሁኔታ ተዘምኗል!",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        else:
            await update.message.reply_text("❌ መድኃኒቱን ማስቀመጥ አልተቻለም።")
        
    except Exception as e:
        logging.error(f"Error saving stock: {e}")
        await update.message.reply_text(f"❌ ስህተት: {str(e)[:100]}")
    
    return ConversationHandler.END

async def view_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📊 ሁሉንም ክምችት ማሳየት"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return
    
    stock_items = get_pharmacy_stock(pharmacy_internal_id)
    
    if not stock_items:
        await update.message.reply_text(
            "📦 ምንም የክምችት እቃዎች የሉም።\n\n"
            "💡 አዲስ መድኃኒት ለመጨመር '📦 መድኃኒት ጨምር' ይጫኑ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    
    text = "📊 **የክምችት ዝርዝር**\n\n"
    total_items = 0
    
    for item in stock_items:
        stock_id, name, qty, price, expiry, batch, reorder = item
        status = "✅" if qty > reorder else "⚠️"
        text += f"{status} **{name}**\n"
        text += f"   📊 ብዛት: {qty}\n"
        text += f"   💰 ዋጋ: {price if price else 'አልተጠቀሰም'} ብር\n"
        if expiry:
            text += f"   📅 የሚያበቃበት: {expiry}\n"
        text += f"   🔄 ዝቅተኛ ገደብ: {reorder}\n"
        text += f"   🆔 ID: {stock_id}\n"
        text += "────────────────────\n"
        total_items += qty
    
    text += f"\n📦 ጠቅላላ እቃዎች: {len(stock_items)}\n"
    text += f"📊 ጠቅላላ ብዛት: {total_items}"
    
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def view_low_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⚠️ ዝቅተኛ ክምችት ያላቸውን መድኃኒቶች ማሳየት"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return
    
    low_stock = get_low_stock_items(pharmacy_internal_id)
    
    if not low_stock:
        await update.message.reply_text(
            "✅ ሁሉም መድኃኒቶች ከዝቅተኛ ገደብ በላይ ናቸው!\n\n"
            "📦 ክምችትዎ በጥሩ ሁኔታ ላይ ነው.",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    
    text = "⚠️ **ዝቅተኛ ክምችት ያላቸው መድኃኒቶች**\n\n"
    text += "እባክዎ እነዚህን መድኃኒቶች በፍጥነት ያስገቡ!\n\n"
    
    for item in low_stock:
        stock_id, name, qty, reorder = item
        text += f"🔴 **{name}**\n"
        text += f"   📊 ቀሪ: {qty}\n"
        text += f"   🔄 ዝቅተኛ ገደብ: {reorder}\n"
        text += f"   🆔 ID: {stock_id}\n"
        text += "────────────────────\n"
    
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def update_stock_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🔁 የክምችት መጠን ማስተካከል"""
    await update.message.reply_text(
        "🔁 **ክምችት ማስተካከል**\n\n"
        "እባክዎ የሚከተሉትን መረጃዎች ያስገቡ፦\n\n"
        "`ID: 5` (ከላይ ያለው ቁጥር)\n"
        "`አዲስ ብዛት: 50`\n\n"
        "ለምሳሌ፦\n"
        "`ID: 5, አዲስ ብዛት: 50`",
        parse_mode="Markdown"
    )
    return STOCK_UPDATE

async def save_stock_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💾 የክምችት ለውጥ ማስቀመጥ"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return ConversationHandler.END
    
    text = update.message.text
    
    try:
        parts = text.split(',')
        stock_id = None
        new_quantity = None
        
        for part in parts:
            part = part.strip()
            if part.startswith("ID:"):
                stock_id = int(part.split(":", 1)[1].strip())
            elif part.startswith("አዲስ ብዛት:"):
                new_quantity = int(part.split(":", 1)[1].strip())
        
        if not stock_id or new_quantity is None:
            await update.message.reply_text(
                "❌ እባክዎ 'ID' እና 'አዲስ ብዛት' ያስገቡ።\n\n"
                "ምሳሌ: `ID: 5, አዲስ ብዛት: 50`",
                parse_mode="Markdown"
            )
            return STOCK_UPDATE
        
        if update_stock_quantity(stock_id, pharmacy_internal_id, new_quantity, 'adjusted'):
            await update.message.reply_text(
                f"✅ **ክምችት በስኬት ተስተካክሏል!**\n\n"
                f"🆔 ID: {stock_id}\n"
                f"📊 አዲስ ብዛት: {new_quantity}\n\n"
                f"📌 ለውጡ ተመዝግቧል!",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        else:
            await update.message.reply_text("❌ ክምችቱን ማስተካከል አልተቻለም።")
        
    except Exception as e:
        logging.error(f"Error updating stock: {e}")
        await update.message.reply_text(f"❌ ስህተት: {str(e)[:100]}")
    
    return ConversationHandler.END

async def view_stock_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📋 የመድኃኒት ታሪክ ማሳየት"""
    await update.message.reply_text(
        "📋 **የመድኃኒት ታሪክ**\n\n"
        "እባክዎ ታሪኩን ለማየት የሚፈልጉትን መድኃኒት ID ያስገቡ፦\n\n"
        "ምሳሌ: `5`\n\n"
        "💡 መድኃኒት ID ከ'📊 ክምችት ማየት' ያግኙት።",
        parse_mode="Markdown"
    )
    return STOCK_VIEW

async def show_stock_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📋 የመድኃኒት ታሪክ ማሳየት"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return ConversationHandler.END
    
    try:
        stock_id = int(update.message.text.strip())
        history = get_stock_history(stock_id)
        
        if not history:
            await update.message.reply_text(
                f"📋 ለመድኃኒት ID {stock_id} ምንም ታሪክ የለም።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return ConversationHandler.END
        
        text = f"📋 **የመድኃኒት ታሪክ (ID: {stock_id})**\n\n"
        for entry in history:
            change_type, old_qty, new_qty, change, note, created = entry
            type_emoji = {
                'added': '➕',
                'removed': '➖',
                'sold': '💰',
                'adjusted': '🔁'
            }.get(change_type, '📌')
            
            text += f"{type_emoji} {change_type.upper()}\n"
            text += f"   {old_qty} → {new_qty} ({change:+d})\n"
            text += f"   📝 {note if note else 'ምንም ማስታወሻ የለም'}\n"
            text += f"   🕐 {created}\n"
            text += "────────────────────\n"
        
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        
    except Exception as e:
        logging.error(f"Error showing history: {e}")
        await update.message.reply_text(f"❌ ስህተት: {str(e)[:100]}")
    
    return ConversationHandler.END

async def delete_stock_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🗑️ መድኃኒት ከክምችት መሰረዝ"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return
    
    await update.message.reply_text(
        "🗑️ **መድኃኒት ከክምችት መሰረዝ**\n\n"
        "እባክዎ መሰረዝ የሚፈልጉትን መድኃኒት ID ያስገቡ፦\n\n"
        "ምሳሌ: `5`\n\n"
        "💡 መድኃኒት ID ከ'📊 ክምችት ማየት' ያግኙት።",
        parse_mode="Markdown"
    )
    return STOCK_VIEW

async def confirm_delete_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✅ መድኃኒት መሰረዝ ማረጋገጥ"""
    pharmacy_chat_id = update.effective_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await update.message.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return ConversationHandler.END
    
    try:
        stock_id = int(update.message.text.strip())
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ አዎ ሰርዝ", callback_data=f"confirm_stock_delete_{stock_id}"),
                InlineKeyboardButton("❌ አይተው", callback_data="cancel_stock_delete")
            ]
        ])
        
        await update.message.reply_text(
            f"⚠️ **እርግጠኛ ነዎት?**\n\n"
            f"መድኃኒት ID {stock_id} ን መሰረዝ ይፈልጋሉ?\n\n"
            f"ይህ ድርጊት ሊቀለበስ አይችልም!",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logging.error(f"Error deleting stock: {e}")
        await update.message.reply_text(f"❌ ስህተት: {str(e)[:100]}")
    
    return ConversationHandler.END

async def confirm_stock_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✅ የስቶክ መሰረዝ ማረጋገጫ"""
    query = update.callback_query
    await query.answer()
    
    pharmacy_chat_id = query.from_user.id
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    
    if not pharmacy_internal_id:
        await query.edit_message_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return
    
    stock_id = int(query.data.split("_")[3])
    
    if delete_stock_item(stock_id, pharmacy_internal_id):
        await query.edit_message_text(
            f"✅ መድኃኒት ID {stock_id} በስኬት ተሰርዟል!",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ መሰረዝ አልተቻለም።")

async def cancel_stock_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """❌ የስቶክ መሰረዝ ስረዛ"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ መሰረዝ ተሰርዟል።")

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
            await update.message.reply_text("❌ ማስታወሻውን ማስቀመጥ አልተቻለም።")
        
    except Exception as e:
        logging.error(f"Error saving reminder: {e}")
        await update.message.reply_text(f"❌ ስህተት: {str(e)[:100]}")
    
    return ConversationHandler.END

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📋 List all reminders"""
    user_id = update.effective_user.id
    
    medicine_reminders = get_user_medicine_reminders(user_id)
    
    if not medicine_reminders:
        await update.message.reply_text(
            "📋 ምንም ንቁ ማስታወሻዎች የሉም።\n\n"
            "💊 አዲስ የመድኃኒት ማስታወሻ ለመፍጠር '💊 ማሳሰቢያ' ይጫኑ።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    
    text = "📋 **የእርስዎ ማስታወሻዎች**\n\n"
    for rem in medicine_reminders:
        rem_id, name, dosage, time, freq, active = rem
        text += f"💊 **{name}**\n"
        text += f"   ⏰ {time} | 📊 {dosage if dosage else 'ያልተጠቀሰ'}\n"
        text += f"   🔄 {freq}\n"
        text += f"   🆔 ለመሰረዝ: `ሰርዝ {rem_id}`\n"
        text += "────────────────────\n"
    
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
        if text.startswith("ሰርዝ"):
            rem_id = int(text.split()[1])
            if delete_medicine_reminder_db(rem_id, user_id):
                await update.message.reply_text(f"✅ ማስታወሻ #{rem_id} ተሰርዟል!")
            else:
                await update.message.reply_text("❌ ማስታወሻውን መሰረዝ አልተቻለም።")
        else:
            await update.message.reply_text(
                "❌ ትክክለኛ ትዕዛዝ ያስገቡ።\n\n"
                "ምሳሌ: `ሰርዝ 5`"
            )
    except Exception as e:
        logging.error(f"Error deleting reminder: {e}")
        await update.message.reply_text("❌ ስህተት ተከስቷል።")

# ==============================================================================
# EXISTING HANDLERS
# ==============================================================================

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pharmacy_chat_id = update.effective_user.id
    user_id = update.effective_user.id
    
    pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
    if not pharm_info:
        await update.message.reply_text(
            "⚠️ ይህ ትዕዛዝ ለፋርማሲዎች ብቻ ነው።",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        return
    
    is_admin = (user_id == ADMIN_CHAT_ID)
    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    if not pharmacy_internal_id:
        await update.message.reply_text("⚠️ የፋርማሲ መለያ አልተገኘም።")
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            SELECT id, customer_id, medicine_name, price, response_time, status
            FROM pharmacy_responses 
            WHERE pharmacy_id = {placeholder} 
            ORDER BY response_time DESC
        """, (pharmacy_internal_id,))
        all_orders = cursor.fetchall()
        
        if not all_orders:
            await update.message.reply_text(
                "📋 ምንም የታዘዙ መድኃኒቶች አልተገኙም።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return
        
        pending_count = len([o for o in all_orders if o[5] == 'pending'])
        responded_count = len([o for o in all_orders if o[5] == 'responded'])
        completed_count = len([o for o in all_orders if o[5] == 'completed'])
        
        await update.message.reply_text(
            f"📋 **የታዘዙ መድኃኒቶች** (ከአዲስ ወደ አሮጌ)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 ጠቅላላ: {len(all_orders)} ጥያቄዎች\n"
            f"⏳ ምላሽ የሚጠብቁ: {pending_count}\n"
            f"✅ መልስ የተሰጠ: {responded_count}\n"
            f"📦 የተጠናቀቀ: {completed_count}\n",
            parse_mode="Markdown"
        )
        
        for idx, order in enumerate(all_orders, 1):
            order_id, customer_id, medicine_name, price, response_time, status = order
            time_str = response_time.strftime('%Y-%m-%d %H:%M') if hasattr(response_time, 'strftime') else str(response_time)
            
            status_emoji = "⏳" if status == 'pending' else "✅" if status == 'responded' else "📦"
            status_text = "ምላሽ የሚጠብቅ" if status == 'pending' else "መልስ ተሰጥቷል" if status == 'responded' else "ተጠናቅቋል"
            
            text = f"{status_emoji} **{idx}. {medicine_name}**\n"
            text += f"   📅 {time_str}\n"
            text += f"   📊 {status_text}\n"
            
            if is_admin:
                text += f"   👤 ደንበኛ: {customer_id}\n"
            
            if status == 'pending':
                inline_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💊 መልስ ስጥ", callback_data=f"respond_order_{order_id}")]
                ])
                await update.message.reply_text(text, parse_mode="Markdown", reply_markup=inline_keyboard)
            else:
                await update.message.reply_text(text, parse_mode="Markdown")
                
    except Exception as e:
        logging.error(f"Error getting orders: {e}")
        await update.message.reply_text(f"❌ ስህተት: {str(e)[:100]}")
    finally:
        if conn:
            conn.close()

async def respond_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        order_id = int(query.data.split("_")[2])
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            placeholder = "%s" if DATABASE_URL else "?"
            cursor.execute(f"""
                SELECT customer_id, medicine_name, status FROM pharmacy_responses 
                WHERE id = {placeholder}
            """, (order_id,))
            order = cursor.fetchone()
        except Exception as e:
            logging.error(f"Error getting order: {e}")
            await query.edit_message_text("❌ የትዕዛዝ መረጃ ማግኘት አልተቻለም።", reply_markup=None)
            return
        finally:
            if conn:
                conn.close()
        
        if not order:
            await query.edit_message_text("❌ ይህ ትዕዛዝ አልተገኘም።", reply_markup=None)
            return
        
        customer_id, medicine_name, status = order
        
        if status != 'pending':
            await query.edit_message_text(
                f"⏳ ይህ ትዕዛዝ ቀድሞውኑ መልስ አግኝቷል (ሁኔታ: {status})።",
                reply_markup=None
            )
            return
        
        context.user_data["responding_order_id"] = order_id
        context.user_data["responding_customer_id"] = customer_id
        
        price_keyboard = [
            ["✅ አለኝ", "❌ የለኝም"],
            ["🏠 ዋና ገጽ"]
        ]
        
        await query.edit_message_text(
            f"💊 **ለትዕዛዝ መልስ መስጠት**\n\n"
            f"📋 መድኃኒት: **{medicine_name}**\n"
            f"👤 ደንበኛ: {customer_id}\n\n"
            f"✏️ ዋጋ እና መረጃ ያስገቡ።",
            parse_mode="Markdown",
            reply_markup=None
        )
        
        await query.message.reply_text(
            "👇 ከታች ያሉትን ይጠቀሙ፦",
            reply_markup=ReplyKeyboardMarkup(price_keyboard, resize_keyboard=True)
        )
        
        return WAITING_FOR_ORDER_PRICE
        
    except Exception as e:
        logging.error(f"Error in respond_order_callback: {e}")
        await query.edit_message_text(f"❌ ስህተት: {str(e)[:100]}", reply_markup=None)
        return ConversationHandler.END

async def prompt_med_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 ስለ ታዘዘልዎት መድኃኒት መረጃ ማወቂያ\n\n"
        "1. የመድኃኒቱን ስም ይጻፉ\n"
        "2. የሐኪም ማዘዣ ፎቶ ይላኩ\n\n"
        "🤖 AI መረጃ ይሰጥዎታል",
        reply_markup=ReplyKeyboardMarkup([["🏠 ዋና ገጽ"]], resize_keyboard=True)
    )
    return WAITING_FOR_MED_INFO

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

    await query.edit_message_text("⏳ ወደ አማርኛ እየተረጎመ ነው...")

    try:
        amharic_text = await translate_to_amharic(english_text)
        if amharic_text:
            amharic_text = clean_translation(amharic_text)
            inline_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Show English", callback_data="show_english")],
                [InlineKeyboardButton("🏠 ዋና ገጽ", callback_data="go_home")]
            ])
            await query.edit_message_text(
                f"💡 የመድኃኒት መረጃ (አማርኛ)\n\n{amharic_text}\n\n"
                f"⚠️ ይህ መረጃ ለግንዛቤ ብቻ ነው።",
                reply_markup=inline_keyboard
            )
            context.user_data["last_amharic_response"] = amharic_text
        else:
            await query.edit_message_text("❌ ትርጉሙ አልተሳካም።")
    except Exception as e:
        logging.error(f"Translation error: {e}")
        await query.edit_message_text(f"❌ ስህተት: {str(e)[:200]}")

async def show_english_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    english_text = context.user_data.get("last_english_response")
    if not english_text:
        await query.edit_message_text("⚠️ No English text found.")
        return
    inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Translate to Amharic", callback_data="translate_amharic")],
        [InlineKeyboardButton("🏠 ዋና ገጽ", callback_data="go_home")]
    ])
    await query.edit_message_text(
        f"💡 Medical Information (English)\n\n{english_text}\n\n"
        f"⚠️ For informational purposes only.",
        reply_markup=inline_keyboard
    )

async def analyze_med_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return WAITING_FOR_MED_INFO

    if msg.text == "🏠 ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END

    if not OPENROUTER_API_KEY:
        await msg.reply_text("⚠️ AI service is not configured.")
        return ConversationHandler.END

    wait_msg = await msg.reply_text("⏳ Fetching medical information...")

    image_bytes = None
    text = None
    user_id = update.effective_user.id if update.effective_user else None
    
    if msg.photo:
        photo_file = await msg.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        doc_file = await msg.document.get_file()
        image_bytes = await doc_file.download_as_bytearray()
    elif msg.text:
        text = msg.text
    else:
        await msg.reply_text("❌ Please send a medicine name or photo.")
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
            [InlineKeyboardButton("🏠 ዋና ገጽ", callback_data="go_home")]
        ])

        await wait_msg.delete()
        await msg.reply_text(
            f"💡 Medical Information (English)\n\n{english_response}\n\n"
            f"⚠️ This is for informational purposes only.",
            reply_markup=inline_keyboard
        )
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Error: {error_msg}")
        await wait_msg.edit_text(f"❌ Failed to get information.\n\n`{error_msg[:200]}`")

    return ConversationHandler.END

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_CHAT_ID)
    
    ai_stats = get_ai_stats()
    total_pharms = get_bot_statistics()
    top_meds = get_top_medicines(5)
    top_pharms = get_top_pharmacies(5)
    search_history = get_user_search_history(user_id, 5)
    unread_count = get_unread_notification_count(user_id)
    
    text = f"📊 **ስታቲስቲክስ**\n\n"
    text += f"🔔 ያልተነበቡ: {unread_count}\n\n"
    text += f"🤖 **AI**\n"
    text += f"• ጠቅላላ: {ai_stats['total']}\n"
    text += f"• ስኬታማ: {ai_stats['successful']}\n"
    if is_admin:
        text += f"• ስህተቶች: {ai_stats['errors']}\n"
    text += f"• አማካይ ጊዜ: {ai_stats['avg_time']}ሰ\n\n"
    text += f"🏥 **ፋርማሲዎች**\n"
    text += f"• ጠቅላላ: {total_pharms[0]}\n"
    text += f"• የተረጋገጡ: {total_pharms[1]}\n"
    if is_admin:
        text += f"• የሚጠብቁ: {total_pharms[2]}\n"
    text += "\n"
    
    if top_meds:
        text += f"🏆 **ከፍተኛ መድኃኒቶች**\n"
        for idx, (med, count) in enumerate(top_meds, 1):
            text += f"• {idx}. {med} ({count})\n"
        text += "\n"
    
    if top_pharms:
        text += f"🏆 **ከፍተኛ ፋርማሲዎች**\n"
        for idx, (pid, name, loc, phone, count) in enumerate(top_pharms, 1):
            text += f"• {idx}. {name} ({count})\n"
        text += "\n"
    
    if search_history:
        text += f"📝 **የቅርብ ጊዜ ፍለጋዎች**\n"
        for med, date in search_history:
            date_str = date.strftime('%Y-%m-%d %H:%M') if isinstance(date, datetime) else str(date)
            text += f"• {med} - {date_str}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

async def admin_manage_pharmacies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ ይህ ለአድሚን ብቻ ነው።")
        return
    
    pharmacies = get_all_pharmacies_with_status()
    if not pharmacies:
        await update.message.reply_text("📋 ምንም ፋርማሲዎች የሉም።")
        return
    
    text = "🏥 **የፋርማሲዎች አስተዳደር**\n\n"
    
    for pharmacy in pharmacies:
        pid, name, location, phone, is_verified, chat_id = pharmacy
        status_emoji = "✅" if is_verified == 1 else "⏳"
        status_text = "የተረጋገጠ" if is_verified == 1 else "ያልተረጋገጠ"
        
        text += f"{status_emoji} **{name}** (ID: {pid})\n"
        text += f"   📍 {location}\n"
        text += f"   📞 {phone}\n"
        text += f"   📊 {status_text}\n"
        
        if is_verified == 1:
            keyboard = [[InlineKeyboardButton(f"🗑️ ሰርዝ {name}", callback_data=f"delete_pharm_{pid}")]]
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        
        text = ""
    
    await update.message.reply_text(
        "💡 ፋርማሲን ለመሰረዝ '🗑️ ሰርዝ' ይጫኑ።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

async def admin_delete_pharmacy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await query.edit_message_text("⛔ ለአድሚን ብቻ")
        return
    
    pharmacy_id = int(query.data.split("_")[2])
    pharm_info = get_pharmacy_info_by_id(pharmacy_id)
    pharm_name = pharm_info[0] if pharm_info else f"ID {pharmacy_id}"
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ አዎ ሰርዝ", callback_data=f"confirm_delete_{pharmacy_id}"),
            InlineKeyboardButton("❌ አይ", callback_data="cancel_delete")
        ]
    ])
    
    await query.edit_message_text(
        f"⚠️ **እርግጠኛ ነዎት?**\n\n"
        f"ፋርማሲውን '{pharm_name}' (ID: {pharmacy_id}) መሰረዝ?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def admin_confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await query.edit_message_text("⛔ ለአድሚን ብቻ")
        return
    
    pharmacy_id = int(query.data.split("_")[2])
    pharm_info = get_pharmacy_info_by_id(pharmacy_id)
    pharm_name = pharm_info[0] if pharm_info else f"ID {pharmacy_id}"
    pharm_chat_id = pharm_info[3] if pharm_info and len(pharm_info) > 3 else None
    
    if delete_pharmacy_db(pharmacy_id):
        await query.edit_message_text(f"✅ '{pharm_name}' ተሰርዟል!")
        if pharm_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=pharm_chat_id,
                    text=f"⚠️ የፋርማሲዎት ('{pharm_name}') ምዝገባ ተሰርዟል።"
                )
            except:
                pass
    else:
        await query.edit_message_text(f"❌ መሰረዝ አልተቻለም።")

async def admin_cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ ተሰርዟል።")

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
        logging.error(f"Error: {e}")
        await msg.reply_text("❌ የፋርማሲ ዝርዝር ማግኘት አልተቻለም።")
        return

    if not pharmacies:
        await msg.reply_text("ℹ️ ምንም የተረጋገጡ ፋርማሲዎች የሉም።")
        return

    try:
        top_pharms = get_top_pharmacies(100)
        pharm_rank = {pid: idx+1 for idx, (pid, _, _, _, _) in enumerate(top_pharms)}
    except:
        pharm_rank = {}
    
    text = "🏥 **ፋርማሲዎች**\n\n"
    
    for pharmacy in pharmacies:
        try:
            if len(pharmacy) >= 5:
                pid, name, loc, phone, hours = pharmacy[:5]
            else:
                continue
            
            rank = pharm_rank.get(pid, '—')
            rank_emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"#{rank}" if rank != '—' else ""
            
            text += f"{rank_emoji} **{name}**\n"
            text += f"   📍 {loc}\n"
            text += f"   📞 {phone}\n"
            text += f"   🕒 {hours}\n"
            text += "────────────────────\n"
            
            if len(text) > 3500:
                await msg.reply_text(text, parse_mode="Markdown")
                text = ""
        except:
            continue

    if text:
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

async def select_location_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_loc = context.user_data.get("user_location", "አልተመረጠም")
    await update.message.reply_text(
        f"📍 **አካባቢ ምረጥ**\n\nአሁን: {current_loc}\n\nከታች ይምረጡ:",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True),
    )
    return WAITING_FOR_LOCATION_SET

async def save_user_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ዋና ገጽ":
        return await start(update, context)
    context.user_data["user_location"] = msg.text
    await msg.reply_text(
        f"✅ ወደ '{msg.text}' ተቀይሯል!",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )
    return ConversationHandler.END

async def prompt_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_loc = context.user_data.get("user_location")
    loc_text = f"📍 {user_loc}\n\n" if user_loc else ""
    await update.message.reply_text(
        f"{loc_text}እባክዎ መድኃኒት ይላኩ (ስም ወይም ፎቶ):",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )
    return WAITING_FOR_SEARCH

async def handle_customer_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return ConversationHandler.END

    if msg.text:
        logging.info(f"📝 User message: {msg.text}")
        
        menu_buttons = [
            "🔍 መድኃኒት ፈልግ", "📖 የመድኃኒት መረጃ",
            "📍 አካባቢ ምረጥ", "📋 ፋርማሲዎች",
            "🏥 ፋርማሲ መዝግብ", "📋 ትዕዛዞች",
            "📦 ክምችት", "💊 ማሳሰቢያ",
            "📞 ድጋፍ", "🏠 ዋና ገጽ"
        ]
        
        if msg.text in menu_buttons:
            logging.info(f"✅ Menu button detected: {msg.text}")
            
            if msg.text == "🔍 መድኃኒት ፈልግ":
                return await prompt_search(update, context)
            elif msg.text == "📋 ትዕዛዞች":
                return await show_orders(update, context)
            elif msg.text == "💊 ማሳሰቢያ":
                return await add_medicine_reminder(update, context)
            elif msg.text == "📦 ክምችት":
                logging.info("📦 Stock button pressed! Calling stock_menu...")
                return await stock_menu(update, context)
            elif msg.text == "📞 ድጋፍ":
                await show_help(update, context)
                return ConversationHandler.END
            else:
                await start(update, context)
                return ConversationHandler.END

    # ... ቀሪው ኮድ (የደንበኛ ጥያቄ አያያዝ) ...
    keyboard = [[
        InlineKeyboardButton("✅ አለኝ", callback_data=f"available_{user.id}"),
        InlineKeyboardButton("❌ የለኝም", callback_data=f"not_available_{user.id}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    target_chats = verified_pharmacies

    photo_file_id = msg.photo[-1].file_id if msg.photo else (msg.document.file_id if msg.document else None)
    is_doc = True if msg.document else False
    loc_tag = f" (አካባቢ: {user_loc})" if user_loc else ""
    medicine_name = msg.text if msg.text else "Prescription Photo"
    order_time = datetime.now()

    if photo_file_id:
        await msg.reply_text(
            f"✅ ፎቶ ተቀብለናል! ለ{len(target_chats)} ፋርማሲዎች ተልኳል{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        for chat_id in target_chats:
            try:
                pharmacy_chat_id = int(chat_id)
                save_pharmacy_request(pharmacy_chat_id, user.id, medicine_name)
                await send_order_notification(context, chat_id, user.id, "Prescription Photo", order_time)
                caption = f"🔔 **አዲስ ጥያቄ!**\n👤 {user.first_name}\n📍 {user_loc if user_loc else 'ያልተመረጠ'}\n📅 {order_time.strftime('%Y-%m-%d')}\n🕐 {order_time.strftime('%H:%M')}"
                if is_doc:
                    await context.bot.send_document(chat_id=chat_id, document=photo_file_id, caption=caption, reply_markup=reply_markup)
                else:
                    await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=caption, reply_markup=reply_markup)
            except Exception as e:
                logging.error(f"Error: {e}")
    elif msg.text:
        med_name = msg.text
        await msg.reply_text(
            f"✅ '{med_name}' ለ{len(target_chats)} ፋርማሲዎች ተልኳል{loc_tag}",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        for chat_id in target_chats:
            try:
                pharmacy_chat_id = int(chat_id)
                save_pharmacy_request(pharmacy_chat_id, user.id, med_name)
                await send_order_notification(context, chat_id, user.id, med_name, order_time)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 **አዲስ ጥያቄ!**\n💊 {med_name}\n👤 {user.first_name}\n📍 {user_loc if user_loc else 'ያልተመረጠ'}\n📅 {order_time.strftime('%Y-%m-%d')}\n🕐 {order_time.strftime('%H:%M')}",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.error(f"Error: {e}")

    return ConversationHandler.END

async def handle_pharmacy_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    action, customer_id = data.rsplit("_", 1)
    context.chat_data["target_customer_id"] = customer_id

    if action == "available":
        msg_text = "✅ አለኝ ተመዝግቧል!\n\nዋጋ እና መረጃ ያስገቡ።"
        try:
            await query.edit_message_caption(caption=msg_text)
        except:
            await query.edit_message_text(text=msg_text)
        return WAITING_FOR_PHARMACY_PRICE
    else:
        try:
            await query.edit_message_caption(caption="❌ የለኝም ተመዝግቧል።")
        except:
            await query.edit_message_text(text="❌ የለኝም ተመዝግቧል።")
        return ConversationHandler.END

async def receive_price_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return ConversationHandler.END
        
    if msg.text == "🏠 ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END

    price_details = msg.text
    
    order_id = context.user_data.get("responding_order_id")
    customer_id = context.user_data.get("responding_customer_id")
    
    if order_id and customer_id:
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            placeholder = "%s" if DATABASE_URL else "?"
            
            pharmacy_chat_id = update.effective_user.id
            pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
            pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
            pharm_loc = pharm_info[1] if pharm_info else "ያልተጠቀሰ"
            pharm_phone = pharm_info[2] if pharm_info else "ያልተጠቀሰ"
            pharm_hours = pharm_info[3] if pharm_info and pharm_info[3] else "ያልተጠቀሰ"
            
            cursor.execute(f"SELECT medicine_name FROM pharmacy_responses WHERE id = {placeholder}", (order_id,))
            med_row = cursor.fetchone()
            medicine_name = med_row[0] if med_row else "ያልተገለጸ"
            
            cursor.execute(f"SELECT status FROM pharmacy_responses WHERE id = {placeholder}", (order_id,))
            status_row = cursor.fetchone()
            if status_row and status_row[0] != 'pending':
                await msg.reply_text("⏳ ይህ ትዕዛዝ ቀድሞ መልስ አግኝቷል።")
                context.user_data.pop("responding_order_id", None)
                context.user_data.pop("responding_customer_id", None)
                return ConversationHandler.END
            
            cursor.execute(f"""
                UPDATE pharmacy_responses 
                SET price = {placeholder}, status = 'responded'
                WHERE id = {placeholder}
            """, (price_details, order_id))
            if DATABASE_URL:
                conn.commit()
            
            customer_message = (
                f"🎉 **መድኃኒት ተገኘ!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💊 **መድኃኒት**\n└─ {medicine_name}\n\n"
                f"🏥 **ፋርማሲ**\n└─ {pharm_name}\n\n"
                f"📍 **አካባቢ**\n└─ {pharm_loc}\n\n"
                f"📞 **ስልክ**\n└─ {pharm_phone}\n\n"
                f"🕒 **የስራ ሰዓት**\n└─ {pharm_hours}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 **ዋጋ**\n└─ {price_details} ብር\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 ለመግዛት ያግኙን\n"
                f"💊 ማዘዣ አይርሱ!"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=int(customer_id),
                    text=customer_message,
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
                )
            except Exception as e:
                logging.error(f"Error: {e}")
                await msg.reply_text("⚠️ መልስዎ ተቀምጧል ግን ለደንበኛ አልተላከም።")
            
            await msg.reply_text("✅ መልስዎ ተልኳል!", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
            
            context.user_data.pop("responding_order_id", None)
            context.user_data.pop("responding_customer_id", None)
            
        except Exception as e:
            if conn:
                conn.rollback()
            logging.error(f"Error: {e}")
            await msg.reply_text(f"❌ ስህተት: {str(e)[:100]}")
        finally:
            if conn:
                conn.close()
        
        return ConversationHandler.END
    
    # Regular flow from "✅ አለኝ"
    customer_id = context.chat_data.get("target_customer_id")
    pharmacy_chat_id = msg.chat_id

    if not customer_id:
        await msg.reply_text("❌ የደንበኛ መለያ አልተገኘም።")
        return ConversationHandler.END

    pharm_info = get_pharmacy_info_by_chat_id(pharmacy_chat_id)
    pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
    pharm_loc = pharm_info[1] if pharm_info else "ያልተጠቀሰ"
    pharm_phone = pharm_info[2] if pharm_info else "ያልተጠቀሰ"
    pharm_hours = pharm_info[3] if pharm_info and pharm_info[3] else "ያልተጠቀሰ"

    pharmacy_internal_id = get_pharmacy_id_by_chat_id(pharmacy_chat_id)
    if not pharmacy_internal_id:
        await msg.reply_text("❌ የፋርማሲ መለያ አልተገኘም።")
        return ConversationHandler.END

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if DATABASE_URL else "?"
        cursor.execute(f"""
            INSERT INTO pharmacy_responses (pharmacy_id, customer_id, medicine_name, price, status)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, 'responded')
        """, (pharmacy_internal_id, customer_id, price_details[:50], price_details))
        if DATABASE_URL:
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error: {e}")
        await msg.reply_text(f"❌ ስህተት: {str(e)[:100]}")
        return ConversationHandler.END
    finally:
        if conn:
            conn.close()

    customer_message = (
        f"🎉 **መድኃኒት ተገኘ!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏥 **ፋርማሲ**\n└─ {pharm_name}\n\n"
        f"📍 **አካባቢ**\n└─ {pharm_loc}\n\n"
        f"📞 **ስልክ**\n└─ {pharm_phone}\n\n"
        f"🕒 **የስራ ሰዓት**\n└─ {pharm_hours}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 **ዋጋ**\n└─ {price_details} ብር\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 ለመግዛት ያግኙን\n"
        f"💊 ማዘዣ አይርሱ!"
    )

    await msg.reply_text("✅ ዋጋው ተልኳል!", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

    if customer_id:
        try:
            await context.bot.send_message(
                chat_id=int(customer_id),
                text=customer_message,
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
            )
        except Exception as e:
            logging.error(f"Error: {e}")

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
            await query.edit_message_caption(caption=f"✅ '{pharm_name}' ተረጋግጧል!")
        except:
            await query.edit_message_text(text=f"✅ '{pharm_name}' ተረጋግጧል!")

        if pharm_chat_id:
            try:
                await context.bot.send_message(chat_id=pharm_chat_id, text=f"🎉 '{pharm_name}' ተረጋግጧል!")
            except:
                pass

async def start_pharmacy_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏥 **ፋርማሲ መዝግብ**\n\nስም ያስገቡ:",
        reply_markup=ReplyKeyboardMarkup([["🏠 ዋና ገጽ"]], resize_keyboard=True)
    )
    return REG_NAME

async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_name"] = update.message.text
    await update.message.reply_text("📍 አካባቢ ይምረጡ:", reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True))
    return REG_LOCATION

async def reg_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_location"] = update.message.text
    await update.message.reply_text("📞 ስልክ ያስገቡ:", reply_markup=ReplyKeyboardMarkup([["🏠 ዋና ገጽ"]], resize_keyboard=True))
    return REG_PHONE

async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_phone"] = update.message.text
    await update.message.reply_text("🕒 የስራ ሰዓት:", reply_markup=ReplyKeyboardMarkup(HOURS_KEYBOARD, resize_keyboard=True))
    return REG_HOURS

async def reg_get_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 ዋና ገጽ":
        return await start(update, context)
    context.user_data["pharm_hours"] = update.message.text
    await update.message.reply_text("📄 ፈቃድ ፎቶ ይላኩ:", reply_markup=ReplyKeyboardMarkup([["🏠 ዋና ገጽ"]], resize_keyboard=True))
    return REG_LICENSE

async def reg_get_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return REG_LICENSE
    
    if msg.text and msg.text == "🏠 ዋና ገጽ":
        await start(update, context)
        return ConversationHandler.END

    photo_file_id = None
    is_doc = False
    
    if msg.photo:
        photo_file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        photo_file_id = msg.document.file_id
        is_doc = True
    else:
        await msg.reply_text("❌ እባክዎ ፎቶ ይላኩ።")
        return REG_LICENSE

    chat_id = msg.chat_id
    name = context.user_data.get("pharm_name", "ያልተጠቀሰ")
    location = context.user_data.get("pharm_location", "ያልተጠቀሰ")
    phone = context.user_data.get("pharm_phone", "ያልተጠቀሰ")
    hours = context.user_data.get("pharm_hours", "ያልተጠቀሰ")

    try:
        pharm_id = register_pharmacy_db(chat_id, name, location, phone, hours, photo_file_id)
        
        await msg.reply_text(
            f"📝 **ተመዝግቧል!**\n\n"
            f"🏥 {name}\n📍 {location}\n📞 {phone}\n🕒 {hours}\n\n"
            f"⏳ እየተረጋገጠ ነው...",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )

        admin_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ፈቅድ", callback_data=f"verify_{pharm_id}")]
        ])
        
        caption_text = f"🔔 **አዲስ ፋርማሲ!**\n\n🏥 {name}\n📍 {location}\n📞 {phone}\n🕒 {hours}\n🆔 {pharm_id}"

        if is_doc:
            await context.bot.send_document(chat_id=ADMIN_CHAT_ID, document=photo_file_id, caption=caption_text, reply_markup=admin_keyboard)
        else:
            await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=photo_file_id, caption=caption_text, reply_markup=admin_keyboard)

    except Exception as e:
        logging.error(f"Error: {e}")
        await msg.reply_text(f"❌ ስህተት: {str(e)[:200]}")

    return ConversationHandler.END

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 **ድጋፍ**\n\n"
        "ማንኛውም ጥያቄ:\n"
        "• 📞 +251 911 00 00 00\n"
        "• 📱 @AlNoorSupport",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )

async def error_handler_func(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(msg="Exception:", exc_info=context.error)

# ==============================================================================
# 15. MAIN FUNCTION
# ==============================================================================

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    loc_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 አካባቢ ምረጥ$"), select_location_prompt)],
        states={WAITING_FOR_LOCATION_SET: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_location)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), prompt_search)],
        states={WAITING_FOR_SEARCH: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.ALL, handle_customer_request)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    med_info_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📖 የመድኃኒት መረጃ$"), prompt_med_info)],
        states={WAITING_FOR_MED_INFO: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.ALL, analyze_med_info)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    pharmacy_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_pharmacy_response, pattern="^(available_|not_available_)")],
        states={WAITING_FOR_PHARMACY_PRICE: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
        per_message=False,
    )

    pharmacy_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏥 ፋርማሲ መዝግብ$"), start_pharmacy_reg)],
        states={
            REG_NAME: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_name)],
            REG_LOCATION: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_location)],
            REG_PHONE: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_phone)],
            REG_HOURS: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_hours)],
            REG_LICENSE: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.ALL, reg_get_license)],
        },
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    order_response_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(respond_order_callback, pattern="^respond_order_")],
        states={WAITING_FOR_ORDER_PRICE: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
        per_message=False,
    )

    medicine_reminder_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💊 ማሳሰቢያ$"), add_medicine_reminder)],
        states={WAITING_FOR_MEDICINE_REMINDER: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_medicine_reminder)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    stock_add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 መድኃኒት ጨምር$"), add_stock_item)],
        states={STOCK_ADD: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_stock_item)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    stock_update_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔁 ክምችት አስተካክል$"), update_stock_item)],
        states={STOCK_UPDATE: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_stock_update)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    stock_history_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📋 የመድኃኒት ታሪክ$"), view_stock_history)],
        states={STOCK_VIEW: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, show_stock_history)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    stock_delete_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ መድኃኒት ሰርዝ$"), delete_stock_item_handler)],
        states={STOCK_VIEW: [MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_delete_stock)]},
        fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("manage", admin_manage_pharmacies))
    
    app.add_handler(MessageHandler(filters.Regex("^📞 ድጋፍ$"), show_help))
    app.add_handler(MessageHandler(filters.Regex("^📋 ፋርማሲዎች$"), list_pharmacies))
    app.add_handler(MessageHandler(filters.Regex("^📋 ትዕዛዞች$"), show_orders))
    app.add_handler(MessageHandler(filters.Regex("^📋 ማሳሰቢያዎች$"), list_reminders))
    app.add_handler(MessageHandler(filters.Regex("^ሰርዝ"), delete_reminder))
    
    app.add_handler(MessageHandler(filters.Regex("^📊 ክምችት ማየት$"), view_stock))
    app.add_handler(MessageHandler(filters.Regex("^⚠️ ዝቅተኛ ክምችት$"), view_low_stock))
    
    app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^verify_"))
    app.add_handler(CallbackQueryHandler(translate_callback, pattern="^(translate_amharic|go_home)$"))
    app.add_handler(CallbackQueryHandler(show_english_callback, pattern="^show_english$"))
    
    app.add_handler(CallbackQueryHandler(admin_delete_pharmacy_callback, pattern="^delete_pharm_"))
    app.add_handler(CallbackQueryHandler(admin_confirm_delete_callback, pattern="^confirm_delete_"))
    app.add_handler(CallbackQueryHandler(admin_cancel_delete_callback, pattern="^cancel_delete$"))
    
    app.add_handler(CallbackQueryHandler(confirm_stock_delete_callback, pattern="^confirm_stock_delete_"))
    app.add_handler(CallbackQueryHandler(cancel_stock_delete_callback, pattern="^cancel_stock_delete$"))
    
    app.add_handler(order_response_conv)
    app.add_handler(loc_conv)
    app.add_handler(search_conv)
    app.add_handler(med_info_conv)
    app.add_handler(pharmacy_reply_conv)
    app.add_handler(pharmacy_conv)
    app.add_handler(medicine_reminder_conv)
    app.add_handler(stock_add_conv)
    app.add_handler(stock_update_conv)
    app.add_handler(stock_history_conv)
    app.add_handler(stock_delete_conv)

    app.add_handler(MessageHandler(filters.Regex("^🏠 ዋና ገጽ$"), start))

    app.add_error_handler(error_handler_func)

    print("🤖 አል-ኑር ቦት ተጀምሯል...")
    app.run_polling()

if __name__ == "__main__":
    main()