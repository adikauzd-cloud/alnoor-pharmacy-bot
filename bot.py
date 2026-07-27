import os
import json
import sqlite3
import logging
import base64
import urllib.request
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Configuration Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8697195057:AAEWFLHH8EvXNNc4kCyQMke62CvDz-oYgNc")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "7030641737"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://alnoor-pharmacy-bot-3.onrender.com")
MY_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

DB_NAME = "pharmacy_bot.db"
LOGO_FILE_ID = "AgACAgQAAxkBAAEszTBqZGhpfKNE12Y948HvU4JhQHfZrQAC0g1rG4xKIFPy4FmrrNxjRAEAAwIAA3gAAz0E"

# State Constants
WAITING_FOR_SEARCH = 1
WAITING_FOR_PRICE = 2
WAITING_FOR_LOCATION_SET = 3
WAITING_FOR_MED_INFO = 4

REG_NAME = 10
REG_LOCATION = 11
REG_PHONE = 12
REG_HOURS = 14
REG_LICENSE = 13

# Keyboards
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

# Database Helpers
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
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
        ''')
        try:
            cursor.execute("ALTER TABLE pharmacies ADD COLUMN operating_hours TEXT DEFAULT 'ያልተጠቀሰ'")
        except sqlite3.OperationalError:
            pass
        conn.commit()

def register_pharmacy_db(chat_id, name, location, phone, operating_hours, license_photo):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO pharmacies (chat_id, name, location, phone, operating_hours, license_photo, is_verified)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        ''', (chat_id, name, location, phone, operating_hours, license_photo))
        pharmacy_id = cursor.lastrowid
        conn.commit()
        return pharmacy_id

def verify_pharmacy_db(pharmacy_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE pharmacies SET is_verified = 1 WHERE id = ?', (pharmacy_id,))
        conn.commit()

def get_pharmacy_info_by_id(pharmacy_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, location, phone, chat_id, operating_hours FROM pharmacies WHERE id = ?', (pharmacy_id,))
        return cursor.fetchone()

def get_pharmacy_info_by_chat_id(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, location, phone, operating_hours FROM pharmacies WHERE chat_id = ? ORDER BY id DESC LIMIT 1', (chat_id,))
        return cursor.fetchone()

def get_verified_pharmacies_by_location(location=None):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        if location:
            cursor.execute('SELECT chat_id FROM pharmacies WHERE is_verified = 1 AND LOWER(location) LIKE LOWER(?)', (f'%{location}%',))
        else:
            cursor.execute('SELECT chat_id FROM pharmacies WHERE is_verified = 1')
        rows = cursor.fetchall()
        return list(set([r[0] for r in rows]))

def get_all_verified_pharmacies():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, location, phone, operating_hours FROM pharmacies WHERE is_verified = 1')
        return cursor.fetchall()

def get_bot_statistics():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM pharmacies')
        total = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM pharmacies WHERE is_verified = 1')
        verified = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM pharmacies WHERE is_verified = 0')
        pending = cursor.fetchone()[0]
        return total, verified, pending

# Telegram Bot Application Setup
telegram_app = Application.builder().token(BOT_TOKEN).build()

# Handlers
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
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
        except Exception as e:
            logging.error(f"Logo send error: {e}")
            await update.message.reply_text(
                welcome_text,
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
    return ConversationHandler.END

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

    status_msg = await msg.reply_text("⏳ **መረጃው በ AI በመተንተን ላይ ነው... እባክዎ ትንሽ ይጠብቁ...**", parse_mode="Markdown")

    prompt = (
        "እባክህ የዚህን መድኃኒት ወይም የሐኪም ማዘዣ ፎቶ መዝገብ ተንትነህ በሚከተለው መልኩ በአማርኛ አብራራ፦\n"
        "1. የመድኃኒቱ ስም (Medication Name)\n"
        "2. ዋነኛ ጥቅም (Primary Usage)\n"
        "3. አወሳሰድ እና ጥንቃቄዎች (Dosage & Precautions)\n"
        "4. ሊከሰቱ የሚችሉ የጎንዮሽ ጉዳቶች (Side Effects)\n\n"
        "ማስታወሻ፦ መረጃው ለግንዛቤ ብቻ እንደሆነ እና የሐኪም ምክርን እንደማይተካ በጥሩ ስነ-ምግባር ግለጽ።"
    )

    api_key = os.environ.get("GEMINI_API_KEY", MY_GEMINI_KEY)

    if not api_key:
        await status_msg.edit_text("❌ **የ Gemini API Key አልተገኘም!**\nእባክዎ Render ዳሽቦርድ ላይ `GEMINI_API_KEY` ማስገባትዎን ያረጋግጡ።")
        return ConversationHandler.END

    # gemini-1.5-flash የተስተካከለ Endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

    try:
        contents_parts = []
        if msg.photo:
            photo_file = await msg.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            base64_image = base64.b64encode(photo_bytes).decode('utf-8')
            contents_parts = [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        elif msg.text:
            contents_parts = [{"text": f"{prompt}\n\nየመድኃኒቱ ስም፦ {msg.text}"}]

        payload = {"contents": [{"parts": contents_parts}]}
        headers = {'Content-Type': 'application/json'}
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')

        with urllib.request.urlopen(req, timeout=30) as response:
            res_body = json.loads(response.read().decode('utf-8'))
            
            if 'candidates' in res_body and len(res_body['candidates']) > 0:
                ai_text = res_body['candidates'][0]['content']['parts'][0]['text']
                await status_msg.edit_text(
                    f"💡 **የመድኃኒት መረጃ ማብራሪያ፦**\n\n{ai_text}\n\n"
                    f"⚠️ *ማስታወሻ፦ ይህ መረጃ በ AI የተዘጋጀ ለግንዛቤ ብቻ የሚያገለግል ነው። ሁልጊዜ የሐኪምዎን ወይም የፋርማሲስቱን መመሪያ ይከተሉ።*",
                    parse_mode="Markdown"
                )
            else:
                await status_msg.edit_text("❌ AI መረጃውን መተንተን አልቻለም። እባክዎ እንደገና ይሞክሩ።")

    except urllib.error.HTTPError as e:
        error_details = e.read().decode('utf-8')
        logging.error(f"Gemini API Error: {error_details}")
        await status_msg.edit_text(f"❌ **የ AI API ስህተት ተፈጽሟል (HTTP {e.code})**\n`{error_details[:100]}`")
    except Exception as e:
        logging.error(f"AI Processing Error: {e}")
        await status_msg.edit_text(f"❌ **መረጃውን መተንተን አልተቻለም!**\n\n`{str(e)[:150]}`", parse_mode="Markdown")

    return ConversationHandler.END

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ ይቅርታ! ይህንን ትዕዛዝ መጠቀም የሚችለው አድሚኑ ብቻ ነው።")
        return
    total, verified, pending = get_bot_statistics()
    stats_text = (
        f"📊 **የቦቱ ስታቲስቲክስ እና መረጃ**\n\n"
        f"🏥 **ጠቅላላ የተመዘገቡ ፋርማሲዎች፦** {total}\n"
        f"✅ **የተረጋገጡ (ሕጋዊ) ፋርማሲዎች፦** {verified}\n"
        f"⏳ **ማረጋገጫ የሚጠብቁ (Pending)፦** {pending}\n\n"
        f"━━━━━━━━━━━━━━━\n🤖 *አል-ኑር መድኃኒት አፋላጊ ሲስተም*"
    )
    await update.message.reply_text(stats_text, parse_mode="Markdown")

async def list_pharmacies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pharmacies = get_all_verified_pharmacies()
    if not pharmacies:
        await update.message.reply_text("ℹ️ በአሁኑ ሰዓት የተረጋገጡ ሕጋዊ ፋርማሲዎች አልተገኙም።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return
    text = "🏥 **የተመዘገቡ ሕጋዊ ፋርማሲዎች ዝርዝር፦**\n\n"
    for idx, (name, loc, phone, hours) in enumerate(pharmacies, 1):
        text += f"{idx}. **{name}**\n   📍 አካባቢ፦ {loc}\n   📞 ስልክ፦ {phone}\n   🕒 የስራ ሰዓት፦ {hours}\n────────────────────\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

async def select_location_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_loc = context.user_data.get('user_location', 'አልተመረጠም')
    await update.message.reply_text(
        f"📍 **የአካባቢ መምረጫ**\n\nአሁን የተመረጠው አካባቢ፦ **{current_loc}**\n\nእባክዎ የሚገኙበትን ወይም የሚቀርብዎትን ክፍለ ከተማ ከታች ካሉት አዝራሮች ይምረጡ ወይም ይጻፉልን፦",
        reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True)
    )
    return WAITING_FOR_LOCATION_SET

async def save_user_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    selected_loc = msg.text
    context.user_data['user_location'] = selected_loc
    await msg.reply_text(
        f"✅ አካባቢዎ በስኬት ወደ **'{selected_loc}'** ተቀይሯል!\n\nአሁን መድኃኒት ሲፈልጉ ጥያቄዎ በቅድሚያ ለ**{selected_loc}** አካባቢ ፋርማሲዎች ይላካል።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END

async def prompt_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_loc = context.user_data.get('user_location')
    loc_text = f"📍 የተመረጠው አካባቢ፦ **{user_loc}**\n\n" if user_loc else "📍 *(አካባቢ አልመረጡም - ጥያቄው ለሁሉም ፋርማሲዎች ይላካል)*\n\n"
    await update.message.reply_text(
        f"{loc_text}እባክዎ የሚፈልጉትን መድኃኒት፦\n1. **በጽሑፍ** የመድኃኒቱን ስም ይጻፉልን፡ ወይም\n2. **የሐኪም ማዘዣውን (Prescription)** ፎቶ አንስተው ይላኩልን።",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return WAITING_FOR_SEARCH

async def handle_customer_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or (msg.text and msg.text == "🏠 ወደ ዋና ገጽ"):
        return await start(update, context)

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
        await msg.reply_text(f"✅ የሐኪም ማዘዣ ፎቶዎ ተቀብለናል! ለ{len(target_chats)} ሕጋዊ ፋርማሲዎች ጥያቄው ተልኳል።{loc_tag}", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
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
        await msg.reply_text(f"✅ የመድኃኒት ስም '{med_name}' ተቀብለናል! ለ{len(target_chats)} ሕጋዊ ፋርማሲዎች እየተላከ ነው...{loc_tag}", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        for chat_id in target_chats:
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"🔔 **አዲስ የመድኃኒት ፍለጋ ጥያቄ!**\nየተፈለገው መድኃኒት፡ **{med_name}**\n📍 አካባቢ፡ {user_loc if user_loc else 'ያልተመረጠ'}\n\nመድኃኒቱ አለዎት?", reply_markup=reply_markup)
            except Exception as e:
                logging.error(f"Pharmacy notify error: {e}")

    return ConversationHandler.END

# --- [ የተስተካከለው የ ፋርማሲ ምላሽ መስጫ ] ---
async def handle_pharmacy_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, customer_id = query.data.rsplit('_', 1)
    
    # target_customer_id በ user_data ማከማቸት
    context.user_data['target_customer_id'] = customer_id

    if action == "available":
        msg_text = (
            "✅ **'መድኃኒቱ አለኝ' የሚለው ምላሽዎ ተመዝግቧል!**\n\n"
            "እባክዎ የመድኃኒቱን ዋጋ እና ተጨማሪ መረጃ ከታች በጽሑፍ ያስገቡ፦\n\n"
            "**ምሳሌ አጻጻፍ፦**\n"
            "• አሞክሳሲሊን - 150 ብር\n"
            "• ፓራሲታሞል - 50 ብር"
        )
        try:
            await query.edit_message_caption(caption=msg_text, parse_mode="Markdown")
        except Exception:
            await query.edit_message_text(text=msg_text, parse_mode="Markdown")
            
        return WAITING_FOR_PRICE

    elif action == "not_available":
        try:
            await query.edit_message_caption(caption="❌ 'የለኝም' የሚለው ምላሽዎ ተመዝግቧል።")
        except Exception:
            await query.edit_message_text(text="❌ 'የለኝም' የሚለው ምላሽዎ ተመዝግቧል።")
            
        return ConversationHandler.END

# --- [ የተስተካከለው የ ዋጋ አስተላላፊ ] ---
async def receive_price_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return ConversationHandler.END
        
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)

    price_details = msg.text
    customer_id = context.user_data.get('target_customer_id')
    
    pharm_info = get_pharmacy_info_by_chat_id(msg.chat_id)

    pharm_name = pharm_info[0] if pharm_info else "ፋርማሲ"
    pharm_loc = pharm_info[1] if pharm_info else "ያልተጠቀሰ"
    pharm_phone = pharm_info[2] if pharm_info else "ያልተጠቀሰ"
    pharm_hours = pharm_info[3] if pharm_info and pharm_info[3] else "ያልተጠቀሰ"

    if customer_id:
        try:
            await context.bot.send_message(
                chat_id=int(customer_id),
                text=(
                    f"🎉 **የመድኃኒት መረጃ ከ{pharm_name} ተገኘ!**\n\n"
                    f"🏥 **ፋርማሲ፦** {pharm_name}\n"
                    f"📍 **አካባቢ፦** {pharm_loc}\n"
                    f"📞 **ስልክ፦** {pharm_phone}\n"
                    f"🕒 **የስራ ሰዓት፦** {pharm_hours}\n\n"
                    f"💰 **የዋጋ እና የዝርዝር መረጃ፦**\n{price_details}\n\n"
                    f"📄 *እባክዎ በአካል ሲሄዱ የሐኪም ማዘዣ (Prescription) መያዝዎን አይረሱ!*"
                ),
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            await msg.reply_text("✅ ዋጋው እና መረጃው ለደንበኛው በስኬት ተልኳል!", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        except Exception as e:
            logging.error(f"Customer response error: {e}")
            await msg.reply_text("❌ መልእክቱን ለደንበኛው መላክ አልተቻለም።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    else:
        await msg.reply_text("❌ የደንበኛው መረጃ አልተገኘም። እባክዎ እንደገና ይሞክሩ።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

    return ConversationHandler.END

async def handle_admin_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("verify_"):
        pharmacy_id = int(query.data.split("_")[1])
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
                logging.error(f"Approval notice error: {e}")

async def start_pharmacy_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏥 **የፋርማሲ መመዝገቢያ ክፍል**\n\nእባክዎ የፋርማሲዎን **ሙሉ ስም** ያስገቡ፦", reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    return REG_NAME

async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data['pharm_name'] = msg.text
    await msg.reply_text("📍 ፋርማሲዎ የሚገኝበትን **ክፍለ ከተማ / አካባቢ** ከታች ይምረጡ ወይም ይጻፉ፦", reply_markup=ReplyKeyboardMarkup(LOCATION_KEYBOARD, resize_keyboard=True))
    return REG_LOCATION

async def reg_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data['pharm_location'] = msg.text
    await msg.reply_text("📞 ደንበኞች የሚያገኙበትን **የፋርማሲ የስልክ ቁጥር** ያስገቡ፦", reply_markup=ReplyKeyboardMarkup([["🏠 ወደ ዋና ገጽ"]], resize_keyboard=True))
    return REG_PHONE

async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data['pharm_phone'] = msg.text
    await msg.reply_text("🕒 **የፋርማሲዎ የስራ ሰዓት መቼ ነው?**", reply_markup=ReplyKeyboardMarkup(HOURS_KEYBOARD, resize_keyboard=True))
    return REG_HOURS

async def reg_get_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    context.user_data['pharm_hours'] = msg.text
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
    name = context.user_data.get('pharm_name', 'ያልተጠቀሰ')
    location = context.user_data.get('pharm_location', 'ያልተጠቀሰ')
    phone = context.user_data.get('pharm_phone', 'ያልተጠቀሰ')
    hours = context.user_data.get('pharm_hours', 'ያልተጠቀሰ')

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
        logging.error(f"Admin notify error: {e}")

    return ConversationHandler.END

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📞 **አል-ኑር መድኃኒት አፋላጊ - የደንበኞች ድጋፍ**\n\n• **ስልክ፦** +251 911 00 00 00\n• **ቴሌግራም፦** @AlNoorSupport", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

# Conversation Handlers Registration
loc_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^📍 አካባቢ ምረጥ$"), select_location_prompt)],
    states={WAITING_FOR_LOCATION_SET: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_location)]},
    fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)]
)

search_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), prompt_search)],
    states={WAITING_FOR_SEARCH: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.ALL, handle_customer_request)]},
    fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)]
)

med_info_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ$"), prompt_med_info)],
    states={WAITING_FOR_MED_INFO: [MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start), MessageHandler(filters.ALL, analyze_med_info)]},
    fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)]
)

pharmacy_reply_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_pharmacy_response, pattern="^(available_|not_available_)")],
    states={
        WAITING_FOR_PRICE: [
            MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_details)
        ]
    },
    fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)],
    per_user=True,
    per_chat=True,
    per_message=False
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
    fallbacks=[CommandHandler("start", start), MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start)]
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("stats", admin_stats))
telegram_app.add_handler(MessageHandler(filters.Regex("^🏠 ወደ ዋና ገጽ$"), start))
telegram_app.add_handler(MessageHandler(filters.Regex("^📞 እገዛ / ድጋፍ$"), show_help))
telegram_app.add_handler(MessageHandler(filters.Regex("^📋 የፋርማሲዎች ዝርዝር$"), list_pharmacies))
telegram_app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^verify_"))

telegram_app.add_handler(loc_conv)
telegram_app.add_handler(search_conv)
telegram_app.add_handler(med_info_conv)
telegram_app.add_handler(pharmacy_reply_conv)
telegram_app.add_handler(pharmacy_conv)

# FastAPI App Lifespan (Webhook Registration)
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Set Webhook URL in Telegram
    webhook_target = f"{WEBHOOK_URL.rstrip('/')}/webhook"
    await telegram_app.bot.set_webhook(url=webhook_target)
    logging.info(f"✅ Webhook successfully set to: {webhook_target}")
    
    yield
    
    # Cleanup on shutdown
    await telegram_app.stop()
    await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "ok", "message": "✅ አል-ኑር መድኃኒት አፋላጊ ቦት (FastAPI + Webhook) በስኬት እየሰራ ይገኛል!"}

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logging.error(f"Error processing webhook: {e}")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
