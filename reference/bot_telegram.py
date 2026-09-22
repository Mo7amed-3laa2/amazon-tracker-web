import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from database import (
    init_db, add_product, add_user_product, remove_user_product, get_all_products,
    get_user_products, remove_product, get_product_by_id, get_product_by_url,
    is_user_authorized, is_admin, add_authorized_user, remove_authorized_user,
    get_authorized_users, get_user_language, set_user_language, get_price_history,
    update_user_product_preferences, clear_user_product_alert,
    get_check_interval, set_check_interval
)
from formatting import esc, link
from scraper import canonical_product_url, fetch_product
from scheduler import (
    start_scheduler, reschedule_check,
    MIN_CHECK_INTERVAL_MINUTES, MAX_CHECK_INTERVAL_MINUTES,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(CHAT_ID) if CHAT_ID else None
# Starting point only: once an admin picks an interval from the panel, the
# stored setting wins (see database.get_check_interval).
DEFAULT_INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))

TRANSLATIONS = {
    "en": {
        "help_title": "🤖 *Amazon Price Tracker*",
        "help_desc": "Track product prices and get automatic updates!",
        "help_commands": "_Commands available:_",
        "track_cmd": "• 📦 `/track <url>` - Add a product",
        "list_cmd": "• 🧾 `/list` - Show tracked products",
        "check_cmd": "• 🔎 `/check` - Check prices now",
        "untrack_cmd": "• 🗑 `/untrack <id>` - Remove a product",
        "help_lang": "_Supported link formats:_",
        "lang_full": "• Full: `https://www.amazon.com.eg/...`",
        "lang_short": "• Shortened: `https://amzn.eu/d/...`",
        "language": "Language",
        "english": "English",
        "arabic": "العربية",
        "added_success": "✅ *Successfully Added!*",
        "current_price": "Current Price",
        "price_updates": "You'll receive updates when the price changes.",
        "check_list": "Check your list anytime with 🧾 or `/list`",
        "tracked_products": "Tracked Products",
        "no_products": "No products tracked yet.",
        "add_first": "Use 📦 to add your first product!",
        "send_link": "Send me the product link to track it.",
        "full_link": "Full: `https://www.amazon.com.eg/...`",
        "short_link": "Short: `https://amzn.eu/d/00rKyOJw`",
        "paste_link": "Just paste the link and I'll add it to your list!",
        "remove_product": "Remove a product from tracking",
        "product_id": "Send me the product ID",
        "see_list_first": "(use 🧾 to see your list first)",
        "example": "Example",
        "remove_from": "No longer monitoring",
        "invalid_url": "Invalid URL",
        "provide_link": "Please provide a valid Amazon product link.",
        "not_amazon": "Not an Amazon link",
        "not_amazon_desc": "This doesn't look like an Amazon URL.\nPlease make sure you're sharing a product from Amazon.",
        "fetching": "Fetching product details...",
        "please_wait": "Please wait...",
        "could_not_fetch": "Could not fetch the product",
        "link_invalid": "The link might be invalid, or Amazon is temporarily blocking requests.",
        "try_tips": "Try:\n• Copy the link from your browser address bar\n• Wait a moment and try again\n• Check the product still exists",
        "lang_updated": "Language Updated",
        "select_language": "Select Language",
        "choose_language": "Choose your preferred language for the bot:",
    },
    "ar": {
        "help_title": "🤖 *تتبع أسعار أمازون*",
        "help_desc": "تتبع أسعار المنتجات واحصل على تحديثات تلقائية!",
        "help_commands": "_الأوامر المتاحة:_",
        "track_cmd": "• 📦 `/track <رابط>` - إضافة منتج",
        "list_cmd": "• 🧾 `/list` - عرض المنتجات المتتبعة",
        "check_cmd": "• 🔎 `/check` - فحص الأسعار الآن",
        "untrack_cmd": "• 🗑 `/untrack <رقم>` - إزالة منتج",
        "help_lang": "_تنسيقات الروابط المدعومة:_",
        "lang_full": "• كامل: `https://www.amazon.com.eg/...`",
        "lang_short": "• مختصر: `https://amzn.eu/d/...`",
        "language": "اللغة",
        "english": "English",
        "arabic": "العربية",
        "added_success": "✅ *تمت الإضافة بنجاح!*",
        "current_price": "السعر الحالي",
        "price_updates": "ستتلقى تحديثات عند تغيير السعر.",
        "check_list": "تحقق من قائمتك في أي وقت باستخدام 🧾 أو `/list`",
        "tracked_products": "المنتجات المتتبعة",
        "no_products": "لا توجد منتجات مراقبة حاليًا.",
        "add_first": "استخدم 📦 لإضافة أول منتج!",
        "send_link": "أرسل لي رابط المنتج لتتبعه.",
        "full_link": "كامل: `https://www.amazon.com.eg/...`",
        "short_link": "مختصر: `https://amzn.eu/d/00rKyOJw`",
        "paste_link": "ما عليك سوى لصق الرابط وسأضيفه إلى قائمتك!",
        "remove_product": "إزالة منتج من المراقبة",
        "product_id": "أرسل لي معرّف المنتج",
        "see_list_first": "(استخدم 🧾 لرؤية قائمتك أولاً)",
        "example": "مثال",
        "remove_from": "لم يعد قيد المراقبة",
        "invalid_url": "رابط غير صالح",
        "provide_link": "يرجى توفير رابط منتج أمازون صالح.",
        "not_amazon": "ليس رابط أمازون",
        "not_amazon_desc": "هذا لا يبدو أنه رابط أمازون.\nتأكد من مشاركتك منتجًا من أمازون.",
        "fetching": "جاري جلب تفاصيل المنتج...",
        "please_wait": "يرجى الانتظار...",
        "could_not_fetch": "تعذر جلب المنتج",
        "link_invalid": "قد يكون الرابط غير صالح، أو أمازون تحجب الطلب مؤقتًا.",
        "try_tips": "جرب:\n• انسخ الرابط من شريط عنوان متصفحك\n• انتظر قليلاً ثم حاول مرة أخرى\n• تحقق من أن المنتج لا يزال موجودًا",
        "lang_updated": "تم تحديث اللغة",
        "select_language": "اختر اللغة",
        "choose_language": "اختر لغتك المفضلة للروبوت:",
    }
}


def get_text(lang: str, key: str) -> str:
    """Get translated text for a key."""
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, "")


def validate_config() -> None:
    missing = []
    if not TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")

    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Avoid leaking bot token in HTTP request URLs at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def build_help_message(lang: str = "en") -> str:
    return (
        f"{get_text(lang, 'help_title')}\n\n"
        f"{get_text(lang, 'help_desc')}\n\n"
        f"{get_text(lang, 'help_commands')}\n"
        f"{get_text(lang, 'track_cmd')}\n"
        f"{get_text(lang, 'list_cmd')}\n"
        f"{get_text(lang, 'check_cmd')}\n"
        f"{get_text(lang, 'untrack_cmd')}\n\n"
        f"{get_text(lang, 'help_lang')}\n"
        f"{get_text(lang, 'lang_full')}\n"
        f"{get_text(lang, 'lang_short')}"
    )


def build_menu_markup(lang: str = "en", user_id: int | None = None) -> InlineKeyboardMarkup:
    track_btn = "📦 أضف منتج" if lang == "ar" else "📦 Add Product"
    list_btn = "🧾 منتجاتي" if lang == "ar" else "🧾 My Products"
    check_btn = "🔎 افحص الآن" if lang == "ar" else "🔎 Check Now"
    remove_btn = "❌ إزالة" if lang == "ar" else "❌ Remove"
    help_btn = "❓ المساعدة" if lang == "ar" else "❓ Help & Info"
    lang_btn = "🌐 اللغة" if lang == "ar" else "🌐 Language"

    keyboard = [
        [
            InlineKeyboardButton(track_btn, callback_data="track"),
            InlineKeyboardButton(list_btn, callback_data="list"),
        ],
        [
            InlineKeyboardButton(check_btn, callback_data="check"),
            InlineKeyboardButton(remove_btn, callback_data="untrack"),
        ],
        [
            InlineKeyboardButton(help_btn, callback_data="help"),
            InlineKeyboardButton(lang_btn, callback_data="language"),
        ],
    ]

    user_is_admin = user_id == ADMIN_ID or (user_id and is_admin(user_id))
    if user_is_admin:
        admin_btn = "⚙️ إدارة" if lang == "ar" else "⚙️ Admin"
        keyboard.append([InlineKeyboardButton(admin_btn, callback_data="admin_menu")])

    return InlineKeyboardMarkup(keyboard)





def build_tracking_success_message(product_name: str, price: float, lang: str = "en") -> str:
    if lang == "ar":
        return (
            f"✅ *تمت الإضافة بنجاح!*\n\n"
            f"📦 *{product_name}*\n"
            f"💰 السعر الحالي: `EGP {price:,.2f}`\n\n"
            f"_ستتلقى تحديثات عند تغيير السعر._\n"
            f"تحقق من قائمتك في أي وقت باستخدام 🧾 أو `/list`"
        )
    return (
        f"✅ *Successfully Added!*\n\n"
        f"📦 *{product_name}*\n"
        f"💰 Current Price: `EGP {price:,.2f}`\n\n"
        f"_You'll receive updates when the price changes._\n"
        f"Check your list anytime with 🧾 or `/list`"
    )


def build_products_list_message(products, lang: str = "en", bot_username: str | None = None) -> str:
    """Build the products list message with per-item 'View on Amazon' and 'Edit' text links.

    The edit link is a Telegram deep link (t.me/<bot>?start=edit_<id>) that
    re-opens /start with a hidden argument — clicking it does the same thing
    as the edit_product_<id> callback button, without needing a button row
    per item.

    Rendered as HTML, and every product-derived value goes through esc().
    Telegram's Markdown parser also scans link targets for entity markers, so
    with parse_mode="Markdown" an Amazon URL containing `_` lost those
    characters and the link either pointed somewhere else or was dropped and
    left as grey text — which took the edit deep link, bot username and all,
    down with it. See formatting.py.
    """
    if not products:
        if lang == "ar":
            return "📦 <b>المنتجات المتتبعة:</b>\n\nلا توجد منتجات مراقبة حاليًا."
        return "📦 <b>Tracked Products:</b>\n\nNo products tracked yet."

    if lang == "ar":
        lines = [f"📦 <b>المنتجات المتتبعة</b> ({len(products)})\n"]
        alert_text = "تنبيه"
        view_text = "أمازون"
        edit_text = "تعديل"
        unavailable_text = "غير متوفر"
        other_seller_text = "بائع آخر"
    else:
        lines = [f"📦 <b>Tracked Products</b> ({len(products)})\n"]
        alert_text = "Alert"
        view_text = "Amazon"
        edit_text = "Edit"
        unavailable_text = "Unavailable"
        other_seller_text = "Other seller"

    for (pid, url, name, last_price, prev_price, image_url, added_at, merchant_name,
         is_amazon, alert_threshold, price_min, price_max, require_amazon_merchant,
         available) in products:
        current_str = f"EGP {last_price:,.2f}" if last_price is not None else "N/A"

        change_info = ""
        if prev_price is not None and prev_price != last_price and last_price is not None:
            diff = last_price - prev_price
            pct = (diff / prev_price) * 100 if prev_price > 0 else 0
            emoji = "📉" if diff < 0 else "📈"
            change_info = f" ({emoji}{abs(pct):.0f}%)"

        # Only call out the seller when it's NOT Amazon — Amazon is the assumed default.
        merchant_info = "" if is_amazon else f" · 🛒 {esc(merchant_name or other_seller_text)}"

        # The stored price is the last one seen while the item was buyable, so
        # say so rather than presenting it as a live price.
        stock_info = "" if available is None or available else f" · ⚠️ {unavailable_text}"

        alert_info = ""
        if price_max is not None:
            alert_info = f" · 🚨 {alert_text} <code>≤EGP {price_max:,.0f}</code>"

        short_name = name[:45] + ("…" if len(name) > 45 else "")

        links = "🔗 " + link(view_text, canonical_product_url(url))
        if bot_username:
            links += " · " + link(f"✏️ {edit_text}", f"https://t.me/{bot_username}?start=edit_{pid}")

        lines.append(
            f"<b>{pid}.</b> {esc(short_name)}\n"
            f"💰 <code>{current_str}</code>{change_info}{merchant_info}{stock_info}{alert_info}\n"
            f"{links}\n"
        )

    return "\n".join(lines)


def is_valid_amazon_url(url: str) -> bool:
    """Check if URL is a valid Amazon link in any supported format."""
    amazon_domains = [
        "amazon.com",
        "amazon.co",
        "amazon.de",
        "amazon.fr",
        "amazon.it",
        "amazon.es",
        "amazon.nl",
        "amazon.ca",
        "amazon.com.au",
        "amazon.com.br",
        "amazon.in",
        "amazon.jp",
        "amazon.sg",
        "amazon.ae",
        "amazon.sa",
        "amazon.eg",
        "amzn.",
    ]
    return any(domain in url for domain in amazon_domains)


def is_authorized(update: Update) -> bool:
    """Check if user is authorized (admin or authorized user)."""
    user_id = update.effective_user.id
    return user_id == ADMIN_ID or is_user_authorized(user_id)


def get_lang(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """Resolve the user's language, falling back to the database.

    context.user_data lives in memory only, so it is empty after a restart —
    without the DB fallback every user silently reverts to English.
    """
    lang = context.user_data.get("language")
    if not lang:
        lang = get_user_language(user_id)
        context.user_data["language"] = lang
    return lang


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name

    if not is_authorized(update):
        keyboard = [
            [InlineKeyboardButton("📋 Copy User ID", callback_data="copy_user_id")],
            [InlineKeyboardButton("❓ Get Instructions", callback_data="get_instructions")],
        ]

        msg = (
            f"🚫 *Access Denied*\n\n"
            f"You are not authorized to use this bot yet.\n\n"
            f"📍 *Your User ID:*\n`{user_id}`\n\n"
            f"👤 *Your Username:*\n`@{username if username else 'Not set'}`\n\n"
            f"📬 *To get access:*\n"
            f"1. Copy your User ID above\n"
            f"2. Contact the bot admin\n"
            f"3. Send them your ID and desired display name\n"
            f"4. They will authorize you!\n\n"
            f"⏳ Once authorized, you'll be able to use all features."
        )
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"[bot] Unauthorized access attempt from {user_id} ({first_name})")
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    if "language" not in context.user_data:
        context.user_data["language"] = get_user_language(user_id)

    context.user_data["awaiting_track_url"] = False
    context.user_data["awaiting_untrack_id"] = False
    lang = context.user_data["language"]

    # Deep link from a "✏️ Edit" text link in the product list: t.me/<bot>?start=edit_<id>
    if context.args and context.args[0].startswith("edit_"):
        try:
            product_id = int(context.args[0].split("_", 1)[1])
        except ValueError:
            product_id = None
        if product_id is not None:
            await send_product_edit_screen(update, context, user_id, product_id, lang)
            return

    await update.message.reply_text(
        build_help_message(lang),
        parse_mode="Markdown",
        reply_markup=build_menu_markup(lang, user_id)
    )


async def handle_unauthorized_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    action = query.data

    if action == "copy_user_id":
        await query.answer("✅ User ID copied to clipboard!", show_alert=False)
        msg = (
            f"📋 *Your User ID*\n\n"
            f"`{user_id}`\n\n"
            f"👉 Click the code above to copy it, then send it to the admin."
        )
        await query.edit_message_text(msg, parse_mode="Markdown")

    elif action == "get_instructions":
        await query.answer()
        msg = (
            f"📖 *How to Get Access*\n\n"
            f"*Step 1:* Copy your User ID\n"
            f"`{user_id}`\n\n"
            f"*Step 2:* Find the admin\n"
            f"Ask in the group chat or contact them directly\n\n"
            f"*Step 3:* Send them:\n"
            f"• Your User ID (above)\n"
            f"• Your desired display name (e.g., 'Ahmed')\n\n"
            f"*Step 4:* They will authorize you using `/admin`\n\n"
            f"*Step 5:* Send `/start` again - you should now have access! ✅"
        )
        await query.edit_message_text(msg, parse_mode="Markdown")





async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    action = query.data

    if action == "track":
        context.user_data["awaiting_track_url"] = True
        context.user_data["awaiting_untrack_id"] = False
        if lang == "ar":
            msg = ("📤 *أرسل لي رابط المنتج لتتبعه.*\n\n"
                   "_تنسيقات مدعومة:_\n"
                   "🔗 كامل: `https://www.amazon.com.eg/...`\n"
                   "⚡ مختصر: `https://amzn.eu/d/00rKyOJw`\n\n"
                   "_فقط ألصق الرابط وسأضيفه إلى قائمتك!_")
        else:
            msg = ("📤 *Send me the product link to track it.*\n\n"
                   "_Supported formats:_\n"
                   "🔗 Full: `https://www.amazon.com.eg/...`\n"
                   "⚡ Short: `https://amzn.eu/d/00rKyOJw`\n\n"
                   "_Just paste the link and I'll add it to your list!_")
        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "list":
        products = get_user_products(user_id)
        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        if not products:
            if lang == "ar":
                msg = "📦 *منتجاتك المتتبعة*\n\n_لم تضف أي منتجات بعد._\n\nاستخدم 📦 لإضافة أول منتج!"
            else:
                msg = "📦 *Your tracked products*\n\n_You haven't added any products yet._\n\nUse 📦 to add your first product!"
            keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
            await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            msg = build_products_list_message(products, lang, bot_username=context.bot.username)
            keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
            await query.edit_message_text(msg, parse_mode="HTML", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "check":
        user_is_admin = user_id == ADMIN_ID or (user_id and is_admin(user_id))
        if not user_is_admin:
            await query.answer(adm(lang)["admin_only"], show_alert=True)
            return

        from scheduler import check_prices

        if lang == "ar":
            checking = "🔍 *جاري فحص الأسعار...*\n\n_قد يستغرق هذا بعض الوقت._"
            done = "✅ *تم فحص الأسعار*\n\n_ستصلك رسالة عند تغيّر أي سعر._"
        else:
            checking = "🔍 *Checking prices...*\n\n_This can take a while._"
            done = "✅ *Price check complete*\n\n_You'll get a message for any price that changed._"

        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
        await query.edit_message_text(checking, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        await check_prices(context.bot, CHAT_ID)
        await query.edit_message_text(done, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "untrack":
        context.user_data["awaiting_untrack_id"] = True
        context.user_data["awaiting_track_url"] = False
        if lang == "ar":
            msg = ("❌ *إزالة منتج من المراقبة*\n\n"
                   "_أرسل لي معرّف المنتج_ (استخدم 🧾 لرؤية قائمتك أولاً)\n"
                   "مثال: `1` أو `3`")
        else:
            msg = ("❌ *Remove a product from tracking*\n\n"
                   "_Send me the product ID_ (use 🧾 to see your list first)\n"
                   "Example: `1` or `3`")
        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "help":
        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
        await query.edit_message_text(build_help_message(lang), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "language":
        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        lang_keyboard = [
            [
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
            ],
            [InlineKeyboardButton(back_btn, callback_data="back_to_menu")],
        ]
        if lang == "ar":
            msg = "🌐 *اختر اللغة*\n\n_اختر لغتك المفضلة للروبوت:_"
        else:
            msg = "🌐 *Select Language*\n\n_Choose your preferred language for the bot:_"
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(lang_keyboard))

    elif action.startswith("lang_"):
        new_lang = action.split("_")[1]
        context.user_data["language"] = new_lang
        set_user_language(user_id, new_lang)
        logger.info(f"[bot] User {user_id} changed language to {new_lang}")

        if new_lang == "ar":
            msg = "✅ *تم تغيير اللغة إلى العربية*"
            back_btn = "◀ العودة"
        else:
            msg = "✅ *Language changed to English*"
            back_btn = "◀ Back"
        keyboard = [[InlineKeyboardButton(back_btn, callback_data="back_to_menu")]]
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "back_to_menu":
        await query.edit_message_text(build_help_message(lang), parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))

    elif action == "admin_menu":
        if user_id != ADMIN_ID and not is_admin(user_id):
            await query.answer(adm(lang)["admin_only"], show_alert=True)
            return
        await show_admin_panel(query, lang)

    else:
        unknown_msg = "إجراء غير معروف" if lang == "ar" else "Unknown action"
        await query.edit_message_text(unknown_msg, reply_markup=build_menu_markup(lang, user_id))


async def process_track_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)

    if not url:
        if lang == "ar":
            msg = "❌ *رابط غير صالح*\n\nيرجى توفير رابط منتج أمازون صالح."
        else:
            msg = "❌ *Invalid URL*\n\nPlease provide a valid Amazon product link."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if not is_valid_amazon_url(url):
        if lang == "ar":
            msg = ("❌ *ليس رابط أمازون*\n\n"
                   "_هذا لا يبدو أنه رابط أمازون._\n"
                   "تأكد من مشاركتك منتجًا من أمازون.")
        else:
            msg = ("❌ *Not an Amazon link*\n\n"
                   "_This doesn't look like an Amazon URL._\n"
                   "Please make sure you're sharing a product from Amazon.")
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if lang == "ar":
        waiting = "🔍 *جاري جلب بيانات المنتج...*\n\n_قد يستغرق هذا حتى دقيقة._"
    else:
        waiting = "🔍 *Getting the product info...*\n\n_This can take up to a minute._"
    status = await update.message.reply_text(waiting, parse_mode="Markdown")

    # fetch_product does blocking network I/O with retries and sleeps; running it
    # on the event loop would freeze the bot for every user until it finishes.
    result = await asyncio.to_thread(fetch_product, url, lang)

    if result is None:
        if lang == "ar":
            msg = ("⚠️ *تعذر جلب المنتج*\n\n"
                   "_قد يكون الرابط غير صالح، أو أمازون تحجب الطلب مؤقتًا._\n\n"
                   "💡 جرب:\n"
                   "• انسخ الرابط من شريط عنوان متصفحك\n"
                   "• انتظر قليلاً ثم حاول مرة أخرى\n"
                   "• تحقق من أن المنتج لا يزال موجودًا")
        else:
            msg = ("⚠️ *Could not fetch the product*\n\n"
                   "_The link might be invalid, or Amazon is temporarily blocking requests._\n\n"
                   "💡 Try:\n"
                   "• Copy the link from your browser address bar\n"
                   "• Wait a moment and try again\n"
                   "• Check the product still exists")
        await status.edit_text(msg, parse_mode="Markdown")
        return

    # Nothing useful can be tracked from a sold-out listing: it has no price of
    # its own, only a stale one or another seller's offer.
    if not result.get("available", True):
        if lang == "ar":
            msg = ("⚠️ *المنتج غير متوفر حاليًا*\n\n"
                   "_أمازون لا تعرض سعرًا لهذا المنتج الآن._\n\n"
                   "جرّب إضافته مرة أخرى عندما يعود للتوفر.")
        else:
            msg = ("⚠️ *This product is currently unavailable*\n\n"
                   "_Amazon isn't showing a price for it right now._\n\n"
                   "Try adding it again once it's back in stock.")
        await status.edit_text(msg, parse_mode="Markdown")
        return

    product_id = add_product(
        url,
        result["name"],
        result["price"],
        result.get("image"),
        result.get("merchant_name"),
        result.get("is_amazon", True)
    )
    add_user_product(user_id, product_id)

    merchant_badge = "✅ Amazon" if result.get("is_amazon", True) else f"🛒 {esc(result.get('merchant_name') or 'Other seller')}"
    product_title = esc(result['name'][:60])
    if lang == "ar":
        success_msg = (
            f"✅ <b>تمت الإضافة بنجاح!</b>\n\n"
            f"📦 <b>{product_title}</b>\n"
            f"💰 السعر: <code>EGP {result['price']:,.2f}</code>\n"
            f"🏪 البائع: {merchant_badge}\n\n"
            f"<i>يمكنك تعيين سعر تنبيه ليصلك إشعار عند انخفاض السعر إليه.</i>"
        )
        prefs_btn = "🚨 تعيين سعر التنبيه"
        skip_btn = "⏭️ لاحقًا"
    else:
        success_msg = (
            f"✅ <b>Successfully Added!</b>\n\n"
            f"📦 <b>{product_title}</b>\n"
            f"💰 Price: <code>EGP {result['price']:,.2f}</code>\n"
            f"🏪 Seller: {merchant_badge}\n\n"
            f"<i>You can set an alert price to get notified when it drops to that price.</i>"
        )
        prefs_btn = "🚨 Set alert price"
        skip_btn = "⏭️ Later"

    keyboard = [
        [
            InlineKeyboardButton(prefs_btn, callback_data=f"edit_alert_{product_id}"),
            InlineKeyboardButton(skip_btn, callback_data="back_to_menu"),
        ]
    ]

    await status.edit_text(
        success_msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["awaiting_track_url"] = False


async def process_alert_price(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, price_text: str):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)

    try:
        alert_price = float(price_text.replace(",", "").replace("EGP", "").strip())
    except ValueError:
        msg = ("❌ *رقم غير صالح*\n\n_أرسل رقمًا فقط، مثل_ `500`"
               if lang == "ar" else
               "❌ *Invalid number*\n\n_Send just a number, e.g._ `500`")
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if alert_price <= 0:
        msg = "❌ يجب أن يكون السعر أكبر من صفر" if lang == "ar" else "❌ Price must be greater than zero"
        await update.message.reply_text(msg)
        return

    update_user_product_preferences(user_id, product_id, price_max=alert_price)
    context.user_data["awaiting_alert_price_for"] = None

    product = get_product_by_id(product_id)
    current_price = product[3] if product else None

    if lang == "ar":
        msg = f"✅ *تم تعيين سعر التنبيه*\n\n🚨 سأنبهك عندما يصل السعر إلى `EGP {alert_price:,.2f}` أو أقل."
        if current_price is not None and current_price <= alert_price:
            msg += f"\n\n🎉 _السعر الحالي `EGP {current_price:,.2f}` بالفعل عند هذا الحد أو أقل!_"
    else:
        msg = f"✅ *Alert price set*\n\n🚨 I'll notify you when the price reaches `EGP {alert_price:,.2f}` or below."
        if current_price is not None and current_price <= alert_price:
            msg += f"\n\n🎉 _The current price `EGP {current_price:,.2f}` is already at or below that!_"

    back_btn = "◀ العودة للمنتج" if lang == "ar" else "◀ Back to product"
    keyboard = [[InlineKeyboardButton(back_btn, callback_data=f"edit_product_{product_id}")]]
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def process_untrack_id(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id_text: str):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)

    if not product_id_text.isdigit():
        if lang == "ar":
            msg = "❌ *معرّف غير صالح*\n\n_يرجى إرسال رقم المنتج فقط (مثل `1` أو `2`)_"
        else:
            msg = "❌ *Invalid ID*\n\n_Please send just the product number (e.g., `1` or `2`)_"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    product_id = int(product_id_text)
    product = get_product_by_id(product_id)
    if product is None:
        if lang == "ar":
            msg = f"❌ *المنتج غير موجود*\n\n_لا يوجد منتج برقم {product_id}._\n\nاستخدم 🧾 لفحص قائمتك."
        else:
            msg = f"❌ *Product not found*\n\n_No product with ID {product_id}._\n\nUse 🧾 to check your list."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    remove_user_product(user_id, product_id)
    product_name = product[2]
    if lang == "ar":
        msg = f"✅ تم إزالة {product_name}"
    else:
        msg = f"✅ Removed: {product_name}"
    await update.message.reply_text(msg)


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    context.user_data["awaiting_track_url"] = True
    context.user_data["awaiting_untrack_id"] = False
    if lang == "ar":
        msg = ("📤 *أرسل لي رابط المنتج لتتبعه.*\n\n"
               "_تنسيقات مدعومة:_\n"
               "🔗 كامل: `https://www.amazon.com.eg/...`\n"
               "⚡ مختصر: `https://amzn.eu/d/00rKyOJw`\n\n"
               "_فقط ألصق الرابط وسأضيفه إلى قائمتك!_")
    else:
        msg = ("📤 *Send me the product link to track it.*\n\n"
               "_Supported formats:_\n"
               "🔗 Full: `https://www.amazon.com.eg/...`\n"
               "⚡ Short: `https://amzn.eu/d/00rKyOJw`\n\n"
               "_Just paste the link and I'll add it to your list!_")
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))


async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    products = get_user_products(user_id)
    if not products:
        if lang == "ar":
            msg = ("📦 *لا توجد منتجات مراقبة حتى الآن*\n\n"
                   "_ابدأ بتتبع المنتجات لرؤيتها هنا._\n"
                   "استخدم 📦 أو `/track <رابط>` لإضافة واحد!")
        else:
            msg = ("📦 *No products tracked yet*\n\n"
                   "_Start tracking products to see them here._\n"
                   "Use 📦 or `/track <url>` to add one!")
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))
        return

    msg = build_products_list_message(products, lang, bot_username=context.bot.username)
    keyboard = [[InlineKeyboardButton("◀ Back" if lang == "en" else "◀ العودة", callback_data="back_to_menu")]]
    await update.message.reply_text(
        msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    context.user_data["awaiting_untrack_id"] = True
    context.user_data["awaiting_track_url"] = False
    if lang == "ar":
        msg = ("❌ *إزالة منتج من المراقبة*\n\n"
               "_أرسل لي معرّف المنتج_ (استخدم 🧾 لرؤية قائمتك أولاً)\n"
               "مثال: `1` أو `3`")
    else:
        msg = ("❌ *Remove a product from tracking*\n\n"
               "_Send me the product ID_ (use 🧾 to see your list first)\n"
               "Example: `1` or `3`")
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)

    t = adm(lang)
    back = [[InlineKeyboardButton(t["back_admin"], callback_data="admin_menu")]]

    if context.user_data.get("awaiting_scan_interval"):
        await process_scan_interval(update, context, update.message.text or "")
        return

    if context.user_data.get("awaiting_authorize_name"):
        username = update.message.text.strip()
        context.user_data["authorize_username"] = username
        context.user_data["awaiting_authorize_name"] = False
        context.user_data["awaiting_authorize_id"] = True

        await update.message.reply_text(
            t["step2"].format(name=username),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(back),
        )
        return

    if context.user_data.get("awaiting_authorize_id"):
        if update.message.text.strip().isdigit():
            target_id = int(update.message.text.strip())
            username = context.user_data.pop("authorize_username", "User")
            context.user_data["awaiting_authorize_id"] = False

            add_authorized_user(target_id, username)
            await update.message.reply_text(
                t["authorized"].format(name=username, uid=target_id),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(back),
            )
        else:
            await update.message.reply_text(t["invalid_id"], reply_markup=InlineKeyboardMarkup(back))
        return

    if context.user_data.get("awaiting_revoke_id"):
        context.user_data["awaiting_revoke_id"] = False
        if update.message.text.strip().isdigit():
            target_id = int(update.message.text.strip())
            remove_authorized_user(target_id)
            await update.message.reply_text(
                f"{t['revoked']}: {target_id}", reply_markup=InlineKeyboardMarkup(back)
            )
        else:
            await update.message.reply_text(t["invalid_id"], reply_markup=InlineKeyboardMarkup(back))
        return

    if not is_authorized(update):
        user_id = update.effective_user.id
        keyboard = [
            [InlineKeyboardButton("📋 Copy User ID", callback_data="copy_user_id")],
            [InlineKeyboardButton("❓ Get Instructions", callback_data="get_instructions")],
        ]

        msg = (
            f"🚫 *Not Authorized*\n\n"
            f"Your User ID: `{user_id}`\n\n"
            f"Please contact the admin to request access.\n"
            f"Send them your User ID and desired display name."
        )
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if context.user_data.get("awaiting_track_url"):
        context.user_data["awaiting_track_url"] = False
        await process_track_url(update, context, update.message.text.strip())
        return

    if context.user_data.get("awaiting_untrack_id"):
        context.user_data["awaiting_untrack_id"] = False
        await process_untrack_id(update, context, update.message.text.strip())
        return

    alert_product_id = context.user_data.get("awaiting_alert_price_for")
    if alert_product_id:
        await process_alert_price(update, context, alert_product_id, update.message.text.strip())
        return

    text_input = update.message.text.strip() if update.message.text else ""
    text_lower = text_input.lower()

    keyboard_button_map = {
        "en": {
            "add product": "track",
            "add": "track",
            "📦 add product": "track",
            "my products": "list",
            "my list": "list",
            "🧾 my products": "list",
            "check now": "check",
            "check": "check",
            "🔎 check now": "check",
            "remove": "untrack",
            "delete": "untrack",
            "❌ remove": "untrack",
            "help & info": "help",
            "help": "help",
            "❓ help & info": "help",
            "language": "language",
            "🌐 language": "language",
            "admin": "admin_menu",
            "⚙️ admin": "admin_menu",
        },
        "ar": {
            "أضف منتج": "track",
            "أضف": "track",
            "📦 أضف منتج": "track",
            "📦 أضف": "track",
            "منتجاتي": "list",
            "قائمتي": "list",
            "🧾 منتجاتي": "list",
            "🧾 قائمتي": "list",
            "افحص الآن": "check",
            "فحص": "check",
            "🔎 افحص الآن": "check",
            "🔎 فحص": "check",
            "إزالة": "untrack",
            "حذف": "untrack",
            "❌ إزالة": "untrack",
            "❌ حذف": "untrack",
            "المساعدة": "help",
            "مساعدة": "help",
            "❓ المساعدة": "help",
            "❓ مساعدة": "help",
            "اللغة": "language",
            "لغة": "language",
            "🌐 اللغة": "language",
            "🌐 لغة": "language",
            "إدارة": "admin_menu",
            "ادارة": "admin_menu",
            "⚙️ إدارة": "admin_menu",
            "⚙️ ادارة": "admin_menu",
        }
    }

    button_action = keyboard_button_map.get(lang, {}).get(text_lower) or keyboard_button_map.get(lang, {}).get(text_input)

    if button_action == "track":
        context.user_data["awaiting_track_url"] = True
        context.user_data["awaiting_untrack_id"] = False
        if lang == "ar":
            msg = ("📤 *أرسل لي رابط المنتج لتتبعه.*\n\n"
                   "_تنسيقات مدعومة:_\n"
                   "🔗 كامل: `https://www.amazon.com.eg/...`\n"
                   "⚡ مختصر: `https://amzn.eu/d/00rKyOJw`\n\n"
                   "_فقط ألصق الرابط وسأضيفه إلى قائمتك!_")
        else:
            msg = ("📤 *Send me the product link to track it.*\n\n"
                   "_Supported formats:_\n"
                   "🔗 Full: `https://www.amazon.com.eg/...`\n"
                   "⚡ Short: `https://amzn.eu/d/00rKyOJw`\n\n"
                   "_Just paste the link and I'll add it to your list!_")
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))
        return

    elif button_action == "list":
        products = get_user_products(user_id)
        if not products:
            if lang == "ar":
                msg = "📦 *منتجاتك المتتبعة*\n\n_لم تضف أي منتجات بعد._\n\nاستخدم 📦 لإضافة أول منتج!"
            else:
                msg = "📦 *Your tracked products*\n\n_You haven't added any products yet._\n\nUse 📦 to add your first product!"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))
            return
        msg = build_products_list_message(products, lang, bot_username=context.bot.username)
        keyboard = [[InlineKeyboardButton("◀ Back" if lang == "en" else "◀ العودة", callback_data="back_to_menu")]]
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif button_action == "check":
        from scheduler import check_prices
        if lang == "ar":
            checking = "🔍 *جاري فحص الأسعار...*\n\n_قد يستغرق هذا بعض الوقت._"
            done = "✅ *تم فحص الأسعار*\n\n_ستصلك رسالة عند تغيّر أي سعر._"
        else:
            checking = "🔍 *Checking prices...*\n\n_This can take a while._"
            done = "✅ *Price check complete*\n\n_You'll get a message for any price that changed._"
        checking_msg = await update.message.reply_text(checking, parse_mode="Markdown")
        await check_prices(context.bot, CHAT_ID)
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=checking_msg.message_id,
            text=done,
            parse_mode="Markdown"
        )
        return

    elif button_action == "untrack":
        context.user_data["awaiting_untrack_id"] = True
        context.user_data["awaiting_track_url"] = False
        if lang == "ar":
            msg = ("❌ *إزالة منتج من المراقبة*\n\n"
                   "_أرسل لي معرّف المنتج_ (استخدم 🧾 لرؤية قائمتك أولاً)\n"
                   "مثال: `1` أو `3`")
        else:
            msg = ("❌ *Remove a product from tracking*\n\n"
                   "_Send me the product ID_ (use 🧾 to see your list first)\n"
                   "Example: `1` or `3`")
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))
        return

    elif button_action == "help":
        await update.message.reply_text(
            build_help_message(lang),
            parse_mode="Markdown",
            reply_markup=build_menu_markup(lang, user_id)
        )
        return

    elif button_action == "language":
        back_btn = "◀ العودة" if lang == "ar" else "◀ Back"
        lang_keyboard = [
            [
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
            ],
            [InlineKeyboardButton(back_btn, callback_data="back_to_menu")],
        ]
        if lang == "ar":
            msg = "🌐 *اختر اللغة*\n\n_اختر لغتك المفضلة للروبوت:_"
        else:
            msg = "🌐 *Select Language*\n\n_Choose your preferred language for the bot:_"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(lang_keyboard))
        return

    elif button_action == "admin_menu":
        if user_id != ADMIN_ID and not is_admin(user_id):
            if lang == "ar":
                await update.message.reply_text("🚫 للمشرفين فقط", reply_markup=build_menu_markup(lang, user_id))
            else:
                await update.message.reply_text("🚫 Admin only", reply_markup=build_menu_markup(lang, user_id))
            return
        await show_admin_panel(update, lang)
        return

    if text_lower in {"menu", "main menu", "show menu", "start"}:
        await update.message.reply_text(build_help_message(lang), parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))
        return

    if lang == "ar":
        msg = "👋 *استخدم القائمة أدناه للبدء!*\n\n_الأوامر:_ `/track`, `/list`, `/check`, `/untrack`"
    else:
        msg = "👋 *Use the menu below to get started!*\n\n_Commands:_ `/track`, `/list`, `/check`, `/untrack`"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_menu_markup(lang, user_id))


ADMIN_STRINGS = {
    "en": {
        "panel": "⚙️ *Admin Panel*",
        "btn_users": "👥 Users",
        "btn_stats": "📊 Stats",
        "btn_view_products": "📦 View User Products",
        "btn_interval": "⏱️ Scan Interval",
        "btn_authorize": "➕ Authorize User",
        "btn_revoke": "➖ Revoke User",
        "back": "◀ Back",
        "back_admin": "◀ Back to Admin",
        "admin_only": "🚫 Admin only",
        "users_title": "👥 *Authorized Users*",
        "no_users": "No users authorized yet.",
        "select_user_products": "Select a user to view their products:",
        "user_products_title": "📦 <b>Products for {name}</b>",
        "user_no_products": "This user has no tracked products yet.",
        "badge_admin": "🔐 Admin",
        "badge_user": "✅ User",
        "name": "Name",
        "stats_title": "📊 *Bot Statistics*",
        "stats_users": "👥 Authorized Users",
        "stats_products": "📦 Total Products",
        "stats_interval": "⏱️ Check Interval",
        "minutes": "minutes",
        "stats_db": "🗄️ Database",
        "stats_db_ok": "Active",
        "stats_sched": "🔄 Scheduler",
        "stats_sched_ok": "Running",
        "no_revoke": "❌ No users to revoke",
        "select_revoke": "Select user to revoke:",
        "revoked": "✅ Revoked",
        "authorize_prompt": "📝 *Authorize New User*\n\nEnter display name:",
        "step2": "📝 *Step 2*\n\nEnter {name}'s Telegram ID:",
        "authorized": "✅ *{name}* authorized! ({uid})",
        "invalid_id": "❌ Invalid ID. Please enter numbers only.",
        "interval_title": "⏱️ *Scan Interval*",
        "interval_current": "Scanning every {value} — and alerting on what changed.",
        "interval_note": "_Takes effect immediately, no restart needed._",
        "interval_custom_btn": "✏️ Custom…",
        "interval_prompt": "✏️ *Custom scan interval*\n\nSend the number of minutes ({min}–{max}).",
        "interval_saved": "✅ Now scanning every {value}.",
        "interval_saved_restart": "✅ Saved: every {value}.\n\n_It will apply when the bot next starts._",
        "interval_invalid": "❌ Send a whole number of minutes, e.g. `45`.",
        "interval_out_of_range": "❌ Pick between {min} and {max} minutes.",
    },
    "ar": {
        "panel": "⚙️ *لوحة الإدارة*",
        "btn_users": "👥 المستخدمون",
        "btn_stats": "📊 الإحصائيات",
        "btn_view_products": "📦 عرض منتجات المستخدم",
        "btn_interval": "⏱️ فترة الفحص",
        "btn_authorize": "➕ تفويض مستخدم",
        "btn_revoke": "➖ إزالة مستخدم",
        "back": "◀ رجوع",
        "back_admin": "◀ رجوع للإدارة",
        "admin_only": "🚫 للمشرفين فقط",
        "users_title": "👥 *المستخدمون المصرح لهم*",
        "no_users": "لا يوجد مستخدمون حتى الآن.",
        "select_user_products": "اختر مستخدمًا لعرض منتجاته:",
        "user_products_title": "📦 <b>منتجات {name}</b>",
        "user_no_products": "هذا المستخدم لم يضف أي منتجات حتى الآن.",
        "badge_admin": "🔐 مشرف",
        "badge_user": "✅ مستخدم",
        "name": "الاسم",
        "stats_title": "📊 *إحصائيات البوت*",
        "stats_users": "👥 المستخدمون المصرح لهم",
        "stats_products": "📦 إجمالي المنتجات",
        "stats_interval": "⏱️ فترة الفحص",
        "minutes": "دقيقة",
        "stats_db": "🗄️ قاعدة البيانات",
        "stats_db_ok": "نشطة",
        "stats_sched": "🔄 المجدول",
        "stats_sched_ok": "يعمل",
        "no_revoke": "❌ لا يوجد مستخدمون لإزالتهم",
        "select_revoke": "اختر المستخدم للإزالة:",
        "revoked": "✅ تم إزالة",
        "authorize_prompt": "📝 *تفويض مستخدم جديد*\n\nأدخل اسم المستخدم:",
        "step2": "📝 *خطوة 2*\n\nأدخل معرف {name}:",
        "authorized": "✅ تم تفويض *{name}*! ({uid})",
        "invalid_id": "❌ معرف غير صالح. أرقام فقط.",
        "interval_title": "⏱️ *فترة الفحص*",
        "interval_current": "يتم الفحص كل {value}، والتنبيه عند أي تغيير.",
        "interval_note": "_يُطبَّق فورًا بدون إعادة تشغيل._",
        "interval_custom_btn": "✏️ قيمة مخصصة…",
        "interval_prompt": "✏️ *فترة فحص مخصصة*\n\nأرسل عدد الدقائق ({min}–{max}).",
        "interval_saved": "✅ سيتم الفحص كل {value}.",
        "interval_saved_restart": "✅ تم الحفظ: كل {value}.\n\n_سيُطبَّق عند إعادة تشغيل البوت._",
        "interval_invalid": "❌ أرسل عدد الدقائق كرقم صحيح، مثل `45`.",
        "interval_out_of_range": "❌ اختر بين {min} و {max} دقيقة.",
    },
}


def adm(lang: str) -> dict:
    """Admin panel strings for the given language, falling back to English."""
    return ADMIN_STRINGS.get(lang, ADMIN_STRINGS["en"])


def build_users_message(users, lang: str) -> str:
    """Render the authorized-user list in the admin's language."""
    t = adm(lang)
    if not users:
        return f"{t['users_title']}\n\n{t['no_users']}"

    lines = [f"{t['users_title']} ({len(users)})\n"]
    for uid, username, is_admin_flag, _auth_date in users:
        badge = t["badge_admin"] if is_admin_flag else t["badge_user"]
        lines.append(f"\n{badge}\n`{uid}`\n{t['name']}: {username}")
    return "\n".join(lines)


def build_stats_message(users, products, lang: str, interval_minutes: int) -> str:
    """Render bot statistics in the admin's language."""
    t = adm(lang)
    return (
        f"{t['stats_title']}\n\n"
        f"{t['stats_users']}: {len(users)}\n"
        f"{t['stats_products']}: {len(products)}\n"
        f"{t['stats_interval']}: {format_interval(interval_minutes, lang)}\n"
        f"{t['stats_db']}: {t['stats_db_ok']}\n"
        f"{t['stats_sched']}: {t['stats_sched_ok']}"
    )


INTERVAL_PRESETS = (15, 30, 60, 180, 360, 720)


def format_interval(minutes: int, lang: str) -> str:
    """Render a minute count the way the admin screens talk about it."""
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        if lang == "ar":
            return f"{hours} ساعة"
        return f"{hours} hour" + ("" if hours == 1 else "s")
    if lang == "ar":
        return f"{minutes} دقيقة"
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def build_interval_screen(lang: str, current: int):
    """The scan-interval screen: presets, a custom option, and what's active."""
    t = adm(lang)
    msg = (
        f"{t['interval_title']}\n\n"
        f"{t['interval_current'].format(value=format_interval(current, lang))}\n\n"
        f"{t['interval_note']}"
    )

    rows, row = [], []
    for preset in INTERVAL_PRESETS:
        label = format_interval(preset, lang)
        if preset == current:
            label = "✅ " + label
        row.append(InlineKeyboardButton(label, callback_data=f"admin_interval_set_{preset}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(t["interval_custom_btn"], callback_data="admin_interval_custom")])
    rows.append([InlineKeyboardButton(t["back_admin"], callback_data="admin_menu")])
    return msg, InlineKeyboardMarkup(rows)


def apply_check_interval(context: ContextTypes.DEFAULT_TYPE, minutes: int) -> bool:
    """Store the new interval and retime the running job.

    Returns False if the live job could not be retimed, in which case the
    setting is still saved and takes effect on the next start.
    """
    set_check_interval(minutes)
    return reschedule_check(context.bot_data.get("scheduler"), minutes)


async def process_scan_interval(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle the number of minutes typed after '✏️ Custom…'."""
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    t = adm(lang)
    back = [[InlineKeyboardButton(t["back_admin"], callback_data="admin_menu")]]

    if user_id != ADMIN_ID and not is_admin(user_id):
        context.user_data["awaiting_scan_interval"] = False
        await update.message.reply_text(t["admin_only"])
        return

    cleaned = text.strip()
    if not cleaned.isdigit():
        await update.message.reply_text(
            t["interval_invalid"], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(back)
        )
        return

    minutes = int(cleaned)
    if not MIN_CHECK_INTERVAL_MINUTES <= minutes <= MAX_CHECK_INTERVAL_MINUTES:
        await update.message.reply_text(
            t["interval_out_of_range"].format(
                min=MIN_CHECK_INTERVAL_MINUTES, max=MAX_CHECK_INTERVAL_MINUTES
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(back),
        )
        return

    context.user_data["awaiting_scan_interval"] = False
    applied_live = apply_check_interval(context, minutes)
    note = t["interval_saved"] if applied_live else t["interval_saved_restart"]
    msg, keyboard = build_interval_screen(lang, minutes)
    await update.message.reply_text(
        f"{note.format(value=format_interval(minutes, lang))}\n\n{msg}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    t = adm(lang)
    if user_id != ADMIN_ID and not is_admin(user_id):
        await update.message.reply_text(t["admin_only"])
        return

    keyboard = [[InlineKeyboardButton(t["back_admin"], callback_data="admin_menu")]]
    await update.message.reply_text(
        build_users_message(get_authorized_users(), lang),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    t = adm(lang)
    if user_id != ADMIN_ID and not is_admin(user_id):
        await update.message.reply_text(t["admin_only"])
        return

    keyboard = [[InlineKeyboardButton(t["back_admin"], callback_data="admin_menu")]]
    await update.message.reply_text(
        build_stats_message(
            get_authorized_users(), get_all_products(), lang,
            get_check_interval(DEFAULT_INTERVAL),
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    from scheduler import check_prices

    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    if lang == "ar":
        checking = "🔍 *جاري فحص الأسعار...*\n\n_قد يستغرق هذا بعض الوقت._"
        done = "✅ *تم فحص الأسعار*\n\n_ستصلك رسالة عند تغيّر أي سعر._"
    else:
        checking = "🔍 *Checking prices...*\n\n_This can take a while._"
        done = "✅ *Price check complete*\n\n_You'll get a message for any price that changed._"

    status = await update.message.reply_text(checking, parse_mode="Markdown")
    await check_prices(context.bot, CHAT_ID)
    await status.edit_text(done, parse_mode="Markdown")


async def show_admin_panel(update_or_query, lang: str = "en"):
    t = adm(lang)
    keyboard = [
        [InlineKeyboardButton(t["btn_users"], callback_data="admin_users"),
         InlineKeyboardButton(t["btn_stats"], callback_data="admin_stats")],
        [InlineKeyboardButton(t["btn_view_products"], callback_data="admin_view_products")],
        [InlineKeyboardButton(t["btn_interval"], callback_data="admin_interval")],
        [InlineKeyboardButton(t["btn_authorize"], callback_data="admin_authorize"),
         InlineKeyboardButton(t["btn_revoke"], callback_data="admin_revoke")],
        [InlineKeyboardButton(t["back"], callback_data="back_to_menu")],
    ]

    msg = t["panel"]

    if hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(
            msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update_or_query.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    if user_id != ADMIN_ID and not is_admin(user_id):
        await update.message.reply_text(adm(lang)["admin_only"])
        return

    await show_admin_panel(update, lang)


async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    t = adm(lang)

    if user_id != ADMIN_ID and not is_admin(user_id):
        await update.callback_query.answer(t["admin_only"], show_alert=True)
        return

    query = update.callback_query
    await query.answer()

    action = query.data
    back = [[InlineKeyboardButton(t["back_admin"], callback_data="admin_menu")]]

    if action == "admin_users":
        await query.edit_message_text(
            build_users_message(get_authorized_users(), lang),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(back),
        )

    elif action == "admin_stats":
        await query.edit_message_text(
            build_stats_message(
                get_authorized_users(), get_all_products(), lang,
                get_check_interval(DEFAULT_INTERVAL),
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(back),
        )

    elif action == "admin_interval":
        msg, keyboard = build_interval_screen(lang, get_check_interval(DEFAULT_INTERVAL))
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)

    elif action.startswith("admin_interval_set_"):
        minutes = int(action.rsplit("_", 1)[1])
        context.user_data["awaiting_scan_interval"] = False
        applied_live = apply_check_interval(context, minutes)
        note = t["interval_saved"] if applied_live else t["interval_saved_restart"]
        msg, keyboard = build_interval_screen(lang, minutes)
        await query.edit_message_text(
            f"{note.format(value=format_interval(minutes, lang))}\n\n{msg}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif action == "admin_interval_custom":
        context.user_data["awaiting_scan_interval"] = True
        await query.edit_message_text(
            t["interval_prompt"].format(
                min=MIN_CHECK_INTERVAL_MINUTES, max=MAX_CHECK_INTERVAL_MINUTES
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(back),
        )

    elif action == "admin_authorize":
        context.user_data["awaiting_authorize_name"] = True
        await query.edit_message_text(
            t["authorize_prompt"], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(back)
        )

    elif action == "admin_view_products":
        users = get_authorized_users()
        if not users:
            await query.edit_message_text(t["no_users"], reply_markup=InlineKeyboardMarkup(back))
            return

        keyboard = [
            [InlineKeyboardButton(f"📦 {name}", callback_data=f"view_products_{uid}")]
            for uid, name, _, _ in users
        ]
        keyboard.append([InlineKeyboardButton(t["back"], callback_data="admin_menu")])
        await query.edit_message_text(t["select_user_products"], reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "admin_revoke":
        revocable = [(uid, name) for uid, name, is_admin_flag, _ in get_authorized_users() if not is_admin_flag]
        if not revocable:
            await query.edit_message_text(t["no_revoke"], reply_markup=InlineKeyboardMarkup(back))
            return

        keyboard = [
            [InlineKeyboardButton(f"❌ {name}", callback_data=f"revoke_{uid}")]
            for uid, name in revocable
        ]
        keyboard.append([InlineKeyboardButton(t["back"], callback_data="admin_menu")])
        await query.edit_message_text(t["select_revoke"], reply_markup=InlineKeyboardMarkup(keyboard))

    elif action.startswith("view_products_"):
        target_id = int(action.split("_")[2])
        user_name = next(
            (name for uid, name, _, _ in get_authorized_users() if uid == target_id), "User"
        )
        products = get_user_products(target_id)
        
        if not products:
            msg = t["user_no_products"]
            await query.edit_message_text(
                f"{t['user_products_title'].format(name=esc(user_name))}\n\n{esc(msg)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(back)
            )
        else:
            full_msg = build_products_list_message(products, lang)
            # Extract just the products part (skip the title from build_products_list_message)
            products_content = full_msg.split("\n", 1)[1] if "\n" in full_msg else full_msg
            display_msg = f"{t['user_products_title'].format(name=esc(user_name))}\n\n{products_content}"
            await query.edit_message_text(
                display_msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(back)
            )

    elif action.startswith("revoke_"):
        target_id = int(action.split("_")[1])
        user_to_revoke = next(
            (name for uid, name, _, _ in get_authorized_users() if uid == target_id), None
        )

        if user_to_revoke:
            remove_authorized_user(target_id)
            await query.edit_message_text(
                f"{t['revoked']}: {user_to_revoke}", reply_markup=InlineKeyboardMarkup(back)
            )
        else:
            await show_admin_panel(query, lang)


def build_product_edit_screen(product, user_product, lang: str):
    """Build the edit screen message and keyboard for one tracked product.

    HTML for the same reason as the product list: the name is Amazon's text
    and regularly contains Markdown control characters.
    """
    product_id = product[0]
    name = product[2]
    current_price = product[3]
    merchant_name = product[7] if len(product) > 7 else None
    is_amazon = product[8] if len(product) > 8 else True
    available = product[9] if len(product) > 9 else 1

    alert_price = user_product[11]
    amazon_only = bool(user_product[12])

    price_str = f"EGP {current_price:,.2f}" if current_price is not None else "N/A"
    merchant_badge = "✅ Amazon" if is_amazon else f"🛒 {esc(merchant_name or 'Other seller')}"

    if lang == "ar":
        msg = (
            f"📦 <b>{esc(name)}</b>\n\n"
            f"💰 السعر الحالي: <code>{price_str}</code>\n"
            f"🏪 البائع: {merchant_badge}\n"
            + ("" if available is None or available else "⚠️ غير متوفر حاليًا على أمازون\n")
            + f"🚨 نبّهني إذا وصل إلى: "
            + (f"<code>EGP {alert_price:,.2f}</code> أو أقل\n" if alert_price is not None else "<i>غير محدد</i>\n")
            + f"🔒 أمازون فقط: {'نعم' if amazon_only else 'لا'}\n"
        )
        set_btn = "🚨 تعديل سعر التنبيه"
        clear_btn = "🧹 إلغاء سعر التنبيه"
        toggle_btn = "🔒 أمازون فقط: " + ("نعم" if amazon_only else "لا")
        remove_btn = "🗑️ إزالة المنتج"
        back_btn = "◀ العودة للقائمة"
    else:
        msg = (
            f"📦 <b>{esc(name)}</b>\n\n"
            f"💰 Current price: <code>{price_str}</code>\n"
            f"🏪 Seller: {merchant_badge}\n"
            + ("" if available is None or available else "⚠️ Currently unavailable on Amazon\n")
            + f"🚨 Notify me at: "
            + (f"<code>EGP {alert_price:,.2f}</code> or below\n" if alert_price is not None else "<i>not set</i>\n")
            + f"🔒 Amazon only: {'yes' if amazon_only else 'no'}\n"
        )
        set_btn = "🚨 Set alert price"
        clear_btn = "🧹 Clear alert price"
        toggle_btn = "🔒 Amazon only: " + ("yes" if amazon_only else "no")
        remove_btn = "🗑️ Remove product"
        back_btn = "◀ Back to list"

    keyboard = [[InlineKeyboardButton(set_btn, callback_data=f"edit_alert_{product_id}")]]
    if alert_price is not None:
        keyboard.append([InlineKeyboardButton(clear_btn, callback_data=f"clear_alert_{product_id}")])
    keyboard.append([InlineKeyboardButton(toggle_btn, callback_data=f"toggle_amazon_{product_id}")])
    keyboard.append([InlineKeyboardButton(remove_btn, callback_data=f"confirm_remove_{product_id}")])
    keyboard.append([InlineKeyboardButton(back_btn, callback_data="list")])

    return msg, InlineKeyboardMarkup(keyboard)


async def show_product_edit_screen(query, context, user_id: int, product_id: int, lang: str) -> bool:
    product = get_product_by_id(product_id)
    user_product = next((p for p in get_user_products(user_id) if p[0] == product_id), None)

    if product is None or user_product is None:
        not_found = "❌ المنتج غير موجود" if lang == "ar" else "❌ Product not found"
        await query.edit_message_text(not_found, reply_markup=build_menu_markup(lang, user_id))
        return False

    msg, keyboard = build_product_edit_screen(product, user_product, lang)
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)
    return True


async def send_product_edit_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, product_id: int, lang: str) -> bool:
    """Same as show_product_edit_screen but sends a new message instead of editing one.

    Used by the "✏️ Edit" deep link in the product list, which arrives as a
    fresh /start command rather than a callback query.
    """
    product = get_product_by_id(product_id)
    user_product = next((p for p in get_user_products(user_id) if p[0] == product_id), None)

    if product is None or user_product is None:
        not_found = "❌ المنتج غير موجود أو غير متتبع" if lang == "ar" else "❌ Product not found or not tracked by you"
        await update.message.reply_text(not_found, reply_markup=build_menu_markup(lang, user_id))
        return False

    msg, keyboard = build_product_edit_screen(product, user_product, lang)
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard)
    return True


async def handle_preferences_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = get_lang(context, user_id)
    action = query.data
    product_id = int(action.rsplit("_", 1)[1])

    if action.startswith("edit_product_"):
        context.user_data["awaiting_alert_price_for"] = None
        await show_product_edit_screen(query, context, user_id, product_id, lang)

    elif action.startswith("edit_alert_"):
        context.user_data["awaiting_alert_price_for"] = product_id
        if lang == "ar":
            msg = ("🚨 *سعر التنبيه*\n\n"
                   "أرسل السعر الذي تريد أن أنبهك عنده.\n"
                   "سأرسل لك إشعارًا عندما يصل السعر إلى هذا الرقم أو أقل.\n\n"
                   "مثال: `500`")
            back_btn = "◀ العودة"
        else:
            msg = ("🚨 *Alert price*\n\n"
                   "Send the price you want to be alerted at.\n"
                   "I'll notify you when the price reaches that number or drops below it.\n\n"
                   "Example: `500`")
            back_btn = "◀ Back"
        keyboard = [[InlineKeyboardButton(back_btn, callback_data=f"edit_product_{product_id}")]]
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action.startswith("clear_alert_"):
        clear_user_product_alert(user_id, product_id)
        context.user_data["awaiting_alert_price_for"] = None
        await show_product_edit_screen(query, context, user_id, product_id, lang)

    elif action.startswith("toggle_amazon_"):
        user_product = next((p for p in get_user_products(user_id) if p[0] == product_id), None)
        if user_product is None:
            return
        update_user_product_preferences(user_id, product_id, require_amazon_merchant=not bool(user_product[12]))
        await show_product_edit_screen(query, context, user_id, product_id, lang)

    elif action.startswith("confirm_remove_"):
        product = get_product_by_id(product_id)
        if product is None:
            return
        if lang == "ar":
            msg = f"⚠️ <b>هل تريد إزالة هذا المنتج؟</b>\n\n📦 {esc(product[2])}"
            yes_btn, no_btn = "✅ نعم، احذفه", "◀ إلغاء"
        else:
            msg = f"⚠️ <b>Remove this product?</b>\n\n📦 {esc(product[2])}"
            yes_btn, no_btn = "✅ Yes, remove", "◀ Cancel"
        keyboard = [[
            InlineKeyboardButton(yes_btn, callback_data=f"doremove_{product_id}"),
            InlineKeyboardButton(no_btn, callback_data=f"edit_product_{product_id}"),
        ]]
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action.startswith("doremove_"):
        remove_user_product(user_id, product_id)
        msg = "✅ تم إزالة المنتج" if lang == "ar" else "✅ Product removed"
        await query.edit_message_text(msg, reply_markup=build_menu_markup(lang, user_id))


async def post_init(application):
    add_authorized_user(ADMIN_ID, "Admin", True)
    # Kept on bot_data so the admin panel can retime the job while running.
    application.bot_data["scheduler"] = start_scheduler(
        application.bot, CHAT_ID, get_check_interval(DEFAULT_INTERVAL)
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Catch anything a handler raises so one failure (e.g. the DB being
    briefly unreachable) logs cleanly and tells the user to retry, instead of
    the framework's default bare traceback dump with no user feedback."""
    logger.error("Unhandled exception while processing an update", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            lang = get_user_language(update.effective_user.id) if update.effective_user else "en"
        except Exception:
            lang = "en"
        text = "⚠️ حدث خطأ مؤقت، من فضلك حاول مرة أخرى." if lang == "ar" else "⚠️ Something went wrong, please try again in a moment."
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        except Exception:
            pass


def main():
    validate_config()
    init_db()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(CommandHandler("untrack", untrack))
    app.add_handler(CommandHandler("check", check_now))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("users", admin_users))
    app.add_handler(CommandHandler("stats", admin_stats))

    app.add_handler(CallbackQueryHandler(handle_unauthorized_request, pattern="^(copy_user_id|get_instructions)$"))
    app.add_handler(CallbackQueryHandler(handle_menu_button, pattern="^(track|list|check|untrack|help|language|lang_(en|ar)|back_to_menu|admin_menu)$"))
    app.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^(admin_|revoke_|view_products_)"))
    app.add_handler(CallbackQueryHandler(handle_preferences_setup, pattern="^(edit_product_|edit_alert_|clear_alert_|toggle_amazon_|confirm_remove_|doremove_)"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
