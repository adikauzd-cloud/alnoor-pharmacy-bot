# ነባሩን ፋይል አስቀምጥ
mv main.py main_backup.py

# አዲስ ፋይል ፍጠር
cat > main.py << 'ENDOFFILE'
import os
import json
import sqlite3
import logging
import base64
import urllib.request
import re
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

# ============================================
# ⚙️ Configuration
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "7030641737"))
DB_NAME = "pharmacy_bot.db"

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set!")

# ============================================
# 📊 Logging
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# 📋 Constants
# ============================================
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

MENU_BUTTONS = [
    "🔍 መድኃኒት ፈልግ",
    "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ",
    "📍 አካባቢ ምረጥ",
    "📋 የፋርማሲዎች ዝርዝር",
    "🏥 ፋርማሲ መዝግብ",
    "📞 እገዛ / ድጋፍ",
    "🏠 ወደ ዋና ገጽ"
]
MENU_BUTTONS_REGEX = re.compile(f"^({'|'.join(map(re.escape, MENU_BUTTONS))})$")

# ============================================
# 🤖 AI Handler
# ============================================
def analyze_medicine(text=None, image_bytes=None):
    if not MY_GEMINI_KEY:
        return "⚠️ የAI አገልግሎት ቁልፍ አልተገኘም።"
    
    prompt = """
    አንተ የመድሃኒት ባለሙያ ነህ። የተሰጠህን መድሃኒት በሚከተለው ቅርጸት በአማርኛ አብራራ፦
    1. የመድሃኒቱ ስም
    2. ዋነኛ ጥቅም
    3. አወሳሰድ እና ጥንቃቄዎች
    4. ሊከሰቱ የሚችሉ የጎንዮሽ ጉዳቶች
    
    ማስታወሻ፦ ይህ መረጃ ለግንዛቤ ብቻ ነው።
    """
    
    try:
        if image_bytes:
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={MY_GEMINI_KEY}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                    ]
                }]
            }
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={MY_GEMINI_KEY}"
            payload = {
                "contents": [{
                    "parts": [{"text": f"{prompt}\n\nመድሃኒት፦ {text}"}]
                }]
            }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
            if 'candidates' in data and data['candidates']:
                return data['candidates'][0]['content']['parts'][0]['text']
            return "❌ AI መልስ ማግኘት አልቻለም።"
                
    except Exception as e:
        logger.error(f"AI error: {e}")
        return f"❌ መረጃውን መተንተን አልተቻለም።"

# ============================================
# 💾 Database
# ============================================
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
        conn.commit()
        logger.info("✅ Database initialized")

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

def get_pharmacy_info_by_chat_id(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, location, phone, operating_hours FROM pharmacies WHERE chat_id = ? ORDER BY id DESC LIMIT 1', (chat_id,))
        return cursor.fetchone()

# ============================================
# 🤖 Bot Handlers
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "ወዳጄ"
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
        await update.message.reply_text(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
    return ConversationHandler.END

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return ConversationHandler.END
    
    text = msg.text
    
    if text == "🔍 መድኃኒት ፈልግ":
        return await prompt_search(update, context)
    elif text == "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ":
        return await prompt_med_info(update, context)
    elif text == "📍 አካባቢ ምረጥ":
        return await select_location_prompt(update, context)
    elif text == "📋 የፋርማሲዎች ዝርዝር":
        return await list_pharmacies(update, context)
    elif text == "🏠 ወደ ዋና ገጽ":
        return await start(update, context)
    else:
        return await start(update, context)

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

    wait_msg = await msg.reply_text("⏳ **መረጃው በ AI በመተንተን ላይ ነው... እባክዎ ትንሽ ይጠብቁ...**")

    try:
        image_bytes = None
        text = None
        
        if msg.photo:
            photo_file = await msg.photo[-1].get_file()
            image_bytes = await photo_file.download_as_bytearray()
        elif msg.document:
            doc_file = await msg.document.get_file()
            image_bytes = await doc_file.download_as_bytearray()
        elif msg.text:
            text = msg.text
        else:
            await wait_msg.edit_text(
                "❌ እባክዎ የመድሃኒት ስም ወይም ፎቶ ይላኩ።",
                reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
            )
            return WAITING_FOR_MED_INFO
        
        result = analyze_medicine(text=text, image_bytes=image_bytes)
        
        await wait_msg.edit_text(
            f"💡 **የመድኃኒት መረጃ ማብራሪያ፦**\n\n{result}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *ማስታወሻ፦ ይህ መረጃ ለግንዛቤ ብቻ ነው። ሁልጊዜ የሐኪምዎን መመሪያ ይከተሉ።*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"AI Analysis Error: {error_msg}")
        
        if "API key" in error_msg:
            user_msg = "⚠️ የAI አገልግሎት ቁልፍ ችግር አለ። እባክዎ አስተዳዳሪውን ያግኙ።"
        elif "quota" in error_msg.lower():
            user_msg = "⏳ የዕለት ጥቅም ገደብ አልፏል። እባክዎ በኋላ ይሞክሩ።"
        elif "image" in error_msg.lower() or "photo" in error_msg.lower():
            user_msg = "📷 የምስሉ ጥራት ጥሩ አይደለም። እባክዎ ግልጽ የሆነ ፎቶ ይላኩ ወይም የመድኃኒቱን ስም በጽሑፍ ይጻፉ።"
        else:
            user_msg = "❌ መረጃውን መተንተን አልተቻለም!"
        
        await wait_msg.edit_text(
            f"{user_msg}\n\n`{error_msg[:150]}`",
            parse_mode="Markdown",
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
    if not msg:
        return ConversationHandler.END

    if msg.text and msg.text in MENU_BUTTONS:
        if msg.text == "🔍 መድኃኒት ፈልግ":
            return await prompt_search(update, context)
        else:
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
                logger.error(f"Pharmacy notify error: {e}")
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
                logger.error(f"Pharmacy notify error: {e}")

    return ConversationHandler.END

async def select_location_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_loc = context.user_data.get('user_location', 'አልተመረጠም')
    await update.message.reply_text(
        f"📍 **የአካባቢ መምረጫ**\n\nአሁን የተመረጠው አካባቢ፦ **{current_loc}**\n\nእባክዎ የሚገኙበትን ክፍለ ከተማ ከታች ካሉት አዝራሮች ይምረጡ ወይም ይጻፉልን፦",
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
        f"✅ አካባቢዎ በስኬት ወደ **'{selected_loc}'** ተቀይሯል!",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END

async def list_pharmacies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pharmacies = get_all_verified_pharmacies()
    if not pharmacies:
        await update.message.reply_text("ℹ️ በአሁኑ ሰዓት የተረጋገጡ ሕጋዊ ፋርማሲዎች አልተገኙም።", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return
    text = "🏥 **የተመዘገቡ ሕጋዊ ፋርማሲዎች ዝርዝር፦**\n\n"
    for idx, (name, loc, phone, hours) in enumerate(pharmacies, 1):
        text += f"{idx}. **{name}**\n   📍 አካባቢ፦ {loc}\n   📞 ስልክ፦ {phone}\n   🕒 የስራ ሰዓት፦ {hours}\n────────────────────\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 **አል-ኑር መድኃኒት አፋላጊ - የደንበኞች ድጋፍ**\n\n"
        "• **ስልክ፦** +251 911 00 00 00\n"
        "• **ቴሌግራም፦** @AlNoorSupport",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    )

# ============================================
# 🚀 Main
# ============================================
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(MENU_BUTTONS_REGEX, handle_menu_buttons))
    
    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 መድኃኒት ፈልግ$"), prompt_search)],
        states={
            WAITING_FOR_SEARCH: [
                MessageHandler(MENU_BUTTONS_REGEX, handle_menu_buttons),
                MessageHandler(filters.ALL & ~filters.COMMAND, handle_customer_request)
            ]
        },
        fallbacks=[CommandHandler("start", start), MessageHandler(MENU_BUTTONS_REGEX, handle_menu_buttons)]
    )
    app.add_handler(search_conv)
    
    med_info_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ$"), prompt_med_info)],
        states={WAITING_FOR_MED_INFO: [MessageHandler(filters.ALL, analyze_med_info)]},
        fallbacks=[CommandHandler("start", start)]
    )
    app.add_handler(med_info_conv)
    
    loc_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 አካባቢ ምረጥ$"), select_location_prompt)],
        states={WAITING_FOR_LOCATION_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_location)]},
        fallbacks=[CommandHandler("start", start)]
    )
    app.add_handler(loc_conv)
    
    app.add_handler(MessageHandler(filters.Regex("^📋 የፋርማሲዎች ዝርዝር$"), list_pharmacies))
    app.add_handler(MessageHandler(filters.Regex("^📞 እገዛ / ድጋፍ$"), show_help))
    
    logger.info("🚀 Bot started with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
ENDOFFILE