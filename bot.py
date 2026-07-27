try:
    result = analyze_medicine(text=text, image_bytes=image_bytes)
except Exception as e:
    # ስህተቱን አስተናግድ
    error_msg = str(e)
    logging.error(f"AI Analysis Error: {error_msg}")
    user_msg = "❌ መረጃውን መተንተን አልተቻለም!"
    await wait_msg.edit_text(user_msg)
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
    # analyze_med_info ውስጥ ያለውን የስህተት መልእክት አሻሽል
except Exception as e:
    error_msg = str(e)
    logging.error(f"AI Analysis Error: {error_msg}")
    
    # ለተጠቃሚ ግልጽ መልእክት
    if "API key" in error_msg:
        user_msg = "⚠️ የAI አገልግሎት ቁልፍ ችግር አለ። እባክዎ አስተዳዳሪውን ያግኙ።"
    elif "quota" in error_msg.lower():
        user_msg = "⏳ የዕለት ጥቅም ገደብ አልፏል። እባክዎ በኋላ ይሞክሩ።"
    elif "image" in error_msg.lower() or "photo" in error_msg.lower():
        user_msg = "📷 የምስሉ ጥራት ጥሩ አይደለም። እባክዎ ግልጽ የሆነ ፎቶ ይላኩ ወይም የመድኃኒቱን ስም በጽሑፍ ይጻፉ።"
    else:
        user_msg = "❌ መረጃውን መተንተን አልተቻለም!"
    
    await wait_msg.edit_text(
        f"{user_msg}\n\n📝 *ዝርዝር መረጃ፦*\n`{error_msg[:150]}`",
        parse_mode="Markdown",
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

async def prompt_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_loc = context.user_data.get("user_location")
    loc_text = f"📍 የተመረጠው አካባቢ፦ **{user_loc}**\n\n" if user_loc else "📍 *(አካባቢ አልመረጡም - ጥያቄው ለሁሉም ፋርማሲዎች ይላካል)*\n\n"
    await update.message.reply_text(
        f"{loc_text}"
        f"እባክዎ የሚፈልጉትን መድኃኒት፦\n"
        f"1. **በጽሑፍ** የመድኃኒቱን ስም ይጻፉልን፡ ወይም\n"
        f"2. **የሐኪም ማዘዣውን (Prescription)** ፎቶ አንስተው ይላኩልን።\n\n"
        f"*(አካባቢ ለመቀየር '📍 አካባቢ ምረጥ' የሚለውን መጫን ይችላሉ)*",
        reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True),
    )
    return WAITING_FOR_SEARCH

# ============================================
# 📝 የተስተካከለው handle_customer_request
# ============================================
async def handle_customer_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return ConversationHandler.END

    # 🛑 በጣም አስፈላጊ: የሜኑ አዝራሮችን ማጣራት
    if msg.text:
        # ሁሉንም የሜኑ አዝራሮች ዝርዝር
        menu_buttons = [
            "🔍 መድኃኒት ፈልግ",
            "📖 ስለ ታዘዘልዎት መድኃኒት ለማወቅ",
            "📍 አካባቢ ምረጥ",
            "📋 የፋርማሲዎች ዝርዝር",
            "🏥 ፋርማሲ መዝግብ",
            "📞 እገዛ / ድጋፍ",
            "🏠 ወደ ዋና ገጽ"
        ]
        
        # ተጠቃሚው የላከው የሜኑ አዝራር ከሆነ
        if msg.text in menu_buttons:
            # "መድኃኒት ፈልግ" ከሆነ ወደ ፍለጋ እንሂድ
            if msg.text == "🔍 መድኃኒት ፈልግ":
                return await prompt_search(update, context)
            else:
                # ሌሎች አዝራሮች ከሆነ እንደ መድሃኒት አትቁጠር
                await start(update, context)
                return ConversationHandler.END

    # 🟢 ከዚህ በታች ያለው ኮድ የሚሰራው ተጠቃሚው ሜኑ አዝራር ሳይሆን
    # ትክክለኛ የመድኃኒት ስም ወይም ፎቶ ሲልክ ብቻ ነው!
    
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
