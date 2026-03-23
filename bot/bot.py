"""
Telegram Bot for exporting chat history.
Uses Telethon sessions stored in database after WebApp authentication.
"""
import os
import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

try:
    from telegram.ext import AIORateLimiter
    HAS_RATE_LIMITER = True
except ImportError:
    HAS_RATE_LIMITER = False
from telegram.constants import ParseMode

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.tl.types import (
    User as TelethonUser,
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    MessageMediaGeo,
    MessageMediaContact,
    MessageMediaPoll,
    MessageService,
    MessageEntityUrl,
    MessageEntityTextUrl,
    DocumentAttributeVideo,
)

from . import db
from .transcription import transcribe_voice, is_voice_message, TRANSCRIPTION_AVAILABLE


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]
TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]

def get_user_client(user_id: int) -> Optional[TelegramClient]:
    """
    Create Telethon client from stored session with flood protection.
    Uses user's own API credentials if available, falls back to default.

    Args:
        user_id: Telegram user ID

    Returns:
        TelegramClient instance or None if session not found
    """
    session_string = db.get_session_string(user_id)
    if not session_string:
        return None

    # Try to get user's own API credentials first
    user_credentials = db.get_user_api_credentials(user_id)
    if user_credentials:
        api_id, api_hash = user_credentials
        logger.info(f"Using user's own API credentials for user {user_id}")
    else:
        # Fallback to default credentials from environment
        api_id, api_hash = TG_API_ID, TG_API_HASH
        logger.info(f"Using default API credentials for user {user_id}")

    client = TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash
    )

    # Auto-sleep on FloodWait up to 60 seconds
    client.flood_sleep_threshold = 60

    return client


async def connect_client(client: TelegramClient) -> None:
    """
    Connect client and populate entity cache.
    StringSession starts with empty cache, so get_dialogs() is needed
    to resolve PeerUser/PeerChat entities for iter_messages().
    """
    await client.connect()
    # Populate entity cache so PeerUser/PeerChat can be resolved
    await client.get_dialogs(limit=100)


def get_chat_identity(dialog) -> tuple:
    """
    Extract chat_id and chat_type from Telethon dialog.

    Args:
        dialog: Telethon Dialog object

    Returns:
        Tuple of (chat_id, chat_type) where:
        - chat_id: int (dialog.entity.id)
        - chat_type: str ('user', 'chat', or 'channel')
    """
    chat_id = dialog.entity.id

    if dialog.is_user:
        chat_type = 'user'
    elif dialog.is_channel:
        chat_type = 'channel'
    else:  # dialog.is_group or dialog.is_chat
        chat_type = 'chat'

    return chat_id, chat_type


def extract_links_from_message(message) -> list:
    """
    Extract all links from a message:
    - text entities (URL, TextUrl)
    - media.webpage (link preview)
    - reply_markup buttons (inline keyboard URLs)

    Args:
        message: Telethon Message object

    Returns:
        List of unique URLs
    """
    urls = set()

    # Extract from text entities
    if message.entities:
        for entity in message.entities:
            if isinstance(entity, MessageEntityUrl):
                # Direct URL in text
                if message.text:
                    url = message.text[entity.offset:entity.offset + entity.length]
                    urls.add(url)
            elif isinstance(entity, MessageEntityTextUrl):
                # Hyperlink with URL attribute
                if hasattr(entity, 'url') and entity.url:
                    urls.add(entity.url)

    # Extract from webpage preview
    if message.media and isinstance(message.media, MessageMediaWebPage):
        webpage = message.media.webpage
        if webpage and hasattr(webpage, 'url') and webpage.url:
            urls.add(webpage.url)

    # Extract from inline keyboard buttons
    if message.reply_markup and hasattr(message.reply_markup, 'rows'):
        for row in message.reply_markup.rows:
            for button in row.buttons:
                if hasattr(button, 'url') and button.url:
                    urls.add(button.url)

    return list(urls)


def is_video_message(msg) -> bool:
    """Check if a Telethon message contains a downloadable video (not round/video note)."""
    if not msg.media or not isinstance(msg.media, MessageMediaDocument):
        return False
    doc = msg.media.document
    if not doc:
        return False
    for attr in getattr(doc, 'attributes', []):
        # Exclude round video messages (video notes)
        if getattr(attr, 'round_message', False):
            return False
    mime = getattr(doc, 'mime_type', '') or ''
    if 'video' not in mime:
        return False
    return True


def format_duration(seconds: int) -> str:
    """Format seconds to M:SS or H:MM:SS."""
    if seconds is None or seconds < 0:
        return "0:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_video_metadata(message) -> dict:
    """Extract video metadata from a Telethon message."""
    doc = message.media.document
    size_bytes = getattr(doc, 'size', 0) or 0
    size_mb = round(size_bytes / (1024 * 1024), 1)

    duration = 0
    width = 0
    height = 0
    filename = None

    for attr in getattr(doc, 'attributes', []):
        if isinstance(attr, DocumentAttributeVideo):
            duration = getattr(attr, 'duration', 0) or 0
            width = getattr(attr, 'w', 0) or 0
            height = getattr(attr, 'h', 0) or 0
        if hasattr(attr, 'file_name') and attr.file_name:
            filename = attr.file_name

    sender = get_sender_name(message)
    date_str = message.date.strftime('%Y-%m-%d %H:%M') if message.date else ''

    return {
        'message_id': message.id,
        'date_str': date_str,
        'sender': sender,
        'size_mb': size_mb,
        'duration': duration,
        'width': width,
        'height': height,
        'filename': filename,
        'is_large': size_mb > 50,
    }


def format_message_content(message, transcription: Optional[str] = None) -> Optional[str]:
    """
    Format message content for export, handling all message types.

    Args:
        message: Telethon Message object
        transcription: Optional transcription text for voice messages

    Returns:
        Formatted message string or None if message should be skipped
    """
    # Skip service messages (user joined, left, etc.)
    if isinstance(message, MessageService):
        return None

    # Get text content
    text = message.text or message.message or ""

    # Extract links from message
    links = extract_links_from_message(message)

    # Handle media messages
    if message.media:
        media_type = None
        is_voice = False

        if isinstance(message.media, MessageMediaPhoto):
            media_type = "[Photo]"
        elif isinstance(message.media, MessageMediaDocument):
            # Determine document type
            doc = message.media.document
            if doc:
                mime = getattr(doc, 'mime_type', '') or ''
                if any(
                    getattr(attr, 'voice', False)
                    for attr in getattr(doc, 'attributes', [])
                    if hasattr(attr, 'voice')
                ):
                    is_voice = True
                    if transcription:
                        media_type = f"[Voice message]: \"{transcription}\""
                    else:
                        media_type = "[Voice message]"
                elif any(
                    getattr(attr, 'round_message', False)
                    for attr in getattr(doc, 'attributes', [])
                    if hasattr(attr, 'round_message')
                ):
                    is_voice = True
                    if transcription:
                        media_type = f"[Video message]: \"{transcription}\""
                    else:
                        media_type = "[Video message]"
                elif 'video' in mime:
                    media_type = "[Video]"
                elif 'audio' in mime:
                    media_type = "[Audio]"
                elif 'sticker' in mime or any(
                    type(attr).__name__ == 'DocumentAttributeSticker'
                    for attr in getattr(doc, 'attributes', [])
                ):
                    media_type = "[Sticker]"
                elif 'gif' in mime or any(
                    type(attr).__name__ == 'DocumentAttributeAnimated'
                    for attr in getattr(doc, 'attributes', [])
                ):
                    media_type = "[GIF]"
                else:
                    # Get filename if available
                    filename = None
                    for attr in getattr(doc, 'attributes', []):
                        if hasattr(attr, 'file_name'):
                            filename = attr.file_name
                            break
                    if filename:
                        media_type = f"[File: {filename}]"
                    else:
                        media_type = "[Document]"
            else:
                media_type = "[Document]"
        elif isinstance(message.media, MessageMediaWebPage):
            webpage = message.media.webpage
            if webpage and hasattr(webpage, 'url'):
                title = getattr(webpage, 'title', None)
                if title:
                    media_type = f"[Link preview: {title}]"
                else:
                    media_type = "[Link preview]"
            else:
                media_type = "[Link preview]"
        elif isinstance(message.media, MessageMediaGeo):
            media_type = "[Location]"
        elif isinstance(message.media, MessageMediaContact):
            media_type = "[Contact]"
        elif isinstance(message.media, MessageMediaPoll):
            poll = message.media.poll
            question = getattr(poll, 'question', None)
            if question:
                # Handle both string and TextWithEntities
                q_text = question if isinstance(question, str) else getattr(question, 'text', str(question))
                media_type = f"[Poll: {q_text}]"
            else:
                media_type = "[Poll]"
        else:
            media_type = "[Media]"

        # Combine media type with caption/text (skip for voice with transcription)
        if is_voice and transcription:
            result = media_type
        elif text and not is_voice:
            result = f"{media_type} {text}"
        else:
            result = media_type
    else:
        # Plain text message
        result = text if text else None

    # Add links if present
    if result and links:
        links_text = f" ({', '.join(links)})"
        result = result + links_text

    return result


def get_sender_name(message) -> str:
    """Extract sender name from message."""
    if not message.sender:
        return "System"

    if isinstance(message.sender, TelethonUser):
        name = f"{message.sender.first_name or ''} {message.sender.last_name or ''}".strip()
        if not name:
            name = f"User_{message.sender.id}"
        return name
    else:
        return getattr(message.sender, 'title', 'Unknown')


def format_messages_with_time_markers(messages_data, time_interval_minutes=30):
    """
    Format messages with periodic time markers.

    Args:
        messages_data: list of (message_date, sender, content) tuples
        time_interval_minutes: show timestamp every N minutes (default 30)

    Returns:
        list of formatted strings
    """
    if not messages_data:
        return []

    formatted = []
    last_timestamp = None
    last_date = None

    for msg_date, sender, content in messages_data:
        current_date = msg_date.date()

        # Show date marker when date changes
        if last_date is None or current_date != last_date:
            formatted.append(f"\n=== {current_date.strftime('%Y-%m-%d')} ===")
            last_date = current_date
            last_timestamp = None  # Force time marker after date change

        # Show time marker every N minutes
        if last_timestamp is None:
            # First message or after date change
            formatted.append(msg_date.strftime('%H:%M:%S'))
            last_timestamp = msg_date
        else:
            delta = (msg_date - last_timestamp).total_seconds() / 60
            if delta >= time_interval_minutes:
                formatted.append(f"\n{msg_date.strftime('%H:%M:%S')}")
                last_timestamp = msg_date

        # Add message
        formatted.append(f"{sender}: {content}")

    return formatted


# Command handlers

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id
    has_credentials = db.has_user_api_credentials(user_id)
    is_authenticated = db.is_user_authenticated(user_id)

    keyboard = [[
        InlineKeyboardButton("🔐 Войти через WebApp", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]

    # Different message for new vs returning users
    if is_authenticated and has_credentials:
        # Returning user with credentials - short message
        await update.message.reply_text(
            "👋 С возвращением!\n\n"
            "✅ Ты авторизован и используешь свои API credentials.\n\n"
            "Доступные команды:\n"
            "📤 /export - Экспортировать чат\n"
            "🔍 /search - Поиск чата по названию\n"
            "ℹ️ /help - Все команды\n\n"
            "🔐 Политика конфиденциальности: /privacy"
        )
    else:
        # New user or user without credentials - detailed instruction
        credentials_tip = ""
        if not has_credentials:
            credentials_tip = (
                "\n━━━━━━━━━━━━━━━━━━━━\n"
                "🔑 *РЕКОМЕНДАЦИЯ (важно!)*\n\n"
                "Для лучшей работы получи свой API ID и Hash:\n"
                "• Переходи на my.telegram.org\n"
                "• Создай приложение (бесплатно)\n"
                "• Используй при входе\n\n"
                "📖 Подробная инструкция: /apihelp\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
            )

        await update.message.reply_text(
            f"👋 Привет! Это бот для экспорта истории чатов Telegram.\n\n"
            f"Я помогу сохранить переписку в текстовые файлы.\n\n"
            f"*Как начать:*\n"
            f"1️⃣ Нажми кнопку ниже для авторизации\n"
            f"2️⃣ Используй /export для выбора и экспорта чата\n"
            f"3️⃣ Или /search для поиска по названию\n"
            f"{credentials_tip}\n"
            f"📚 Все команды: /help\n"
            f"🔐 Политика конфиденциальности: /privacy",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )



async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await update.message.reply_text(
        "📖 *Доступные команды:*\n\n"
        "/start - Запустить бота\n"
        "/login - Авторизоваться через WebApp\n"
        "/status - Проверить статус авторизации\n"
        "/export - Выбрать и экспортировать чат\n"
        "/search - Поиск чата по названию\n"
        "/apihelp - Как получить свой API ID/Hash\n"
        "/privacy - Политика конфиденциальности\n"
        "/logout - Выйти из аккаунта\n"
        "/help - Показать эту справку\n\n"
        "*Как пользоваться:*\n"
        "1. Нажми /login и авторизуйся через веб-страницу\n"
        "2. Используй /export для просмотра и экспорта чатов\n"
        "3. Или /search для поиска конкретного чата\n\n"
        "⚠️ *Важно:* Вся авторизация происходит через веб-интерфейс. "
        "Я никогда не попрошу коды или пароли в этом чате.",
        parse_mode=ParseMode.MARKDOWN
    )


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /login command - opens WebApp."""
    user_id = update.effective_user.id
    has_credentials = db.has_user_api_credentials(user_id)

    keyboard = [[
        InlineKeyboardButton("🔐 Войти через WebApp", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]

    # Add warning if user doesn't have credentials
    credentials_warning = ""
    if not has_credentials:
        credentials_warning = (
            "\n⚠️ *ВАЖНО: API Credentials*\n"
            "Для лучшей работы рекомендуется использовать свои API ID и Hash.\n"
            "📖 Инструкция: /apihelp\n\n"
        )

    await update.message.reply_text(
        f"🔐 *Авторизация*\n\n"
        f"Нажми кнопку ниже, чтобы открыть страницу авторизации.\n\n"
        f"{credentials_warning}"
        f"📝 *Шаги авторизации:*\n"
        f"1️⃣ Введи API ID и API Hash (если есть)\n"
        f"2️⃣ Введи номер телефона\n"
        f"3️⃣ Введи код подтверждения\n"
        f"4️⃣ Если у тебя включён 2FA — введи пароль\n\n"
        f"💡 Если 2FA не включён, авторизация завершится автоматически после ввода кода.\n\n"
        f"⚠️ Все данные вводятся на веб-странице, не в этом чате.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    user_id = update.effective_user.id

    has_session = db.user_exists(user_id)
    is_authenticated = db.is_user_authenticated(user_id)
    has_credentials = db.has_user_api_credentials(user_id)

    if not has_session:
        await update.message.reply_text(
            "❌ *Не авторизован*\n\n"
            "Ты ещё не вошёл в аккаунт. Используй /login для авторизации.\n\n"
            "💡 Рекомендация: получи свой API ID/Hash перед входом\n"
            "Инструкция: /apihelp",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Build credentials status
    if has_credentials:
        creds_status = "✅ Используешь свои API credentials"
        creds_details = "Отлично! Твои лимиты изолированы от других пользователей."
    else:
        creds_status = "⚠️ Используешь общие API credentials"
        creds_details = "Рекомендуется получить свои для лучшей работы.\n📖 Инструкция: /apihelp"

    if is_authenticated:
        await update.message.reply_text(
            f"✅ *Авторизован*\n\n"
            f"Ты вошёл в аккаунт и можешь использовать /export и /search.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*API Credentials:*\n"
            f"{creds_status}\n\n"
            f"{creds_details}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "⚠️ *Сессия есть, но не авторизована*\n\n"
            "Попробуй войти заново через /login",
            parse_mode=ParseMode.MARKDOWN
        )


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /privacy command - show privacy policy."""
    await update.message.reply_text(
        "🔐 *Политика конфиденциальности*\n\n"
        "*Какие данные мы собираем:*\n"
        "• Telegram User ID\n"
        "• Зашифрованная строка сессии\n"
        "• Прогресс экспорта чатов\n"
        "• Ваши API credentials (зашифрованы)\n\n"
        "*Как мы используем данные:*\n"
        "• Для авторизации в Telegram API\n"
        "• Для экспорта истории сообщений\n"
        "• Для отслеживания прогресса экспорта\n\n"
        "*Ваши права:*\n"
        "✅ Удалить все данные: /logout\n"
        "✅ Проверить статус: /status\n"
        "✅ Прекратить использование в любой момент\n\n"
        "*Безопасность:*\n"
        "🔐 Session strings хранятся зашифрованными\n"
        "🔑 API credentials хранятся зашифрованными\n"
        "📁 Временные файлы удаляются автоматически\n"
        "🎤 Транскрипция использует сторонний сервис Groq\n\n"
        "*Полная версия:*\n"
        "Подробная политика доступна в файле `PRIVACY_POLICY.md` "
        "в репозитории бота.\n\n"
        "📅 Последнее обновление: 14 февраля 2026",
        parse_mode=ParseMode.MARKDOWN
    )


async def apihelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /apihelp command - show how to get API credentials."""
    user_id = update.effective_user.id
    has_credentials = db.has_user_api_credentials(user_id)

    if has_credentials:
        status_emoji = "✅"
        status_text = "У тебя уже есть свои API credentials"
        recommendation = "Всё отлично! Ты используешь изолированные лимиты."
    else:
        status_emoji = "⚠️"
        status_text = "Ты используешь общие API credentials"
        recommendation = "Рекомендуется получить свои для лучшей работы!"

    # Create inline keyboard with link to my.telegram.org
    keyboard = [[
        InlineKeyboardButton("🌐 Открыть my.telegram.org", url="https://my.telegram.org")
    ]]

    await update.message.reply_text(
        f"🔑 *Инструкция: Получение API Credentials*\n\n"
        f"{status_emoji} *Твой статус:* {status_text}\n"
        f"{recommendation}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*📋 ПОШАГОВАЯ ИНСТРУКЦИЯ:*\n\n"
        f"*Шаг 1:* Открой сайт my.telegram.org\n"
        f"_Нажми кнопку ниже ⬇️_\n\n"
        f"*Шаг 2:* Войди с помощью номера телефона\n"
        f"_Введи свой номер и код из Telegram_\n\n"
        f"*Шаг 3:* Нажми \"API development tools\"\n"
        f"_Это в меню на сайте_\n\n"
        f"*Шаг 4:* Заполни форму:\n"
        f"• App title: `My Export Bot`\n"
        f"• Short name: `export-bot`\n"
        f"• Platform: `Other`\n"
        f"_Остальное можно оставить пустым_\n\n"
        f"*Шаг 5:* Получи credentials:\n"
        f"• `api_id`: это число (например: 12345678)\n"
        f"• `api_hash`: это строка (32 символа)\n\n"
        f"*Шаг 6:* Войди в бота заново:\n"
        f"1. Нажми /logout\n"
        f"2. Нажми /login\n"
        f"3. Введи API ID и API Hash\n"
        f"4. Введи номер телефона и код\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*⚡ Зачем это нужно?*\n"
        f"✅ Твои лимиты не зависят от других\n"
        f"✅ Никто не может заблокировать твой доступ\n"
        f"✅ Быстрее работает экспорт\n"
        f"✅ Соответствует правилам Telegram\n\n"
        f"*🔐 Безопасность:*\n"
        f"• Храни credentials в секрете\n"
        f"• Не публикуй их в интернете\n"
        f"• Бот хранит их зашифрованными\n"
        f"• Удаляются при /logout\n\n"
        f"❓ Вопросы? Напиши /help",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


CHATS_PER_PAGE = 10


def fuzzy_search(query, text):
    """Improved search with fuzzy matching."""
    query = query.lower()
    text = text.lower()

    # Exact match
    if query == text:
        return True

    # Substring match
    if query in text:
        return True

    # Word-based search (all query words must be in text)
    query_words = query.split()
    return all(word in text for word in query_words)


def relevance_score(dialog_name, query):
    """Calculate relevance score for sorting results."""
    name_lower = dialog_name.lower()
    query_lower = query.lower()

    # Exact match - highest priority
    if query_lower == name_lower:
        return 100

    # Starts with query - high priority
    if name_lower.startswith(query_lower):
        return 80

    # Contains query - medium priority
    if query_lower in name_lower:
        return 60

    # All query words are in name - low priority
    query_words = query_lower.split()
    if all(word in name_lower for word in query_words):
        return 40

    return 0


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command - search for chats by name."""
    user_id = update.effective_user.id
    search_query = ' '.join(context.args).lower() if context.args else ""

    if not search_query:
        await update.message.reply_text(
            "📝 *Использование:* /search <название чата>\n\n"
            "Пример: /search Python\n"
            "Пример: /search Иван",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if not db.is_user_authenticated(user_id):
        await update.message.reply_text(
            "❌ Сначала нужно авторизоваться. Используй /login"
        )
        return

    client = get_user_client(user_id)
    if not client:
        await update.message.reply_text(
            "❌ Сессия не найдена. Используй /login для авторизации."
        )
        return

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await update.message.reply_text(
                "❌ Сессия истекла. Используй /login для повторной авторизации."
            )
            return

        # Get all dialogs (increased limit to find more chats)
        try:
            dialogs = await client.get_dialogs(limit=500)
        except FloodWaitError as e:
            await update.message.reply_text(
                f"⏳ Лимит запросов Telegram. Подожди {e.seconds} сек. и попробуй снова."
            )
            return

        # Filter by search query using fuzzy search
        results = [d for d in dialogs if fuzzy_search(search_query, d.name)]

        if not results:
            await update.message.reply_text(
                f"❌ Чаты по запросу '{search_query}' не найдены\n"
                f"Проверено {len(dialogs)} чатов"
            )
            return

        # Sort results by relevance
        results.sort(key=lambda d: relevance_score(d.name, search_query), reverse=True)

        # Store search results in context for callback handlers
        context.user_data['search_results'] = []
        for dialog in results:
            chat_id, chat_type = get_chat_identity(dialog)
            context.user_data['search_results'].append({
                'id': dialog.id,
                'name': dialog.name,
                'is_user': dialog.is_user,
                'is_group': dialog.is_group,
                'is_channel': dialog.is_channel,
                'chat_id': chat_id,
                'chat_type': chat_type
            })

        # Format results with buttons (limit to 10 for display)
        results_to_show = results[:10]
        chat_list = [f"*Результаты поиска '{search_query}':* (найдено {len(results)})\n"]
        for i, dialog in enumerate(results_to_show, 1):
            chat_type = "👤" if dialog.is_user else "👥" if dialog.is_group else "📢"
            chat_list.append(f"{i}. {chat_type} {dialog.name}")

        if len(results) > 10:
            chat_list.append(f"\n... и ещё {len(results) - 10}")

        chat_text = "\n".join(chat_list)

        # Create inline buttons for exporting (one button per search result, up to 10)
        keyboard = []
        for i in range(min(len(results_to_show), 10)):
            dialog = results_to_show[i]
            chat_type = "👤" if dialog.is_user else "👥" if dialog.is_group else "📢"
            button_text = f"📥 {chat_type} {dialog.name}"
            keyboard.append([
                InlineKeyboardButton(button_text, callback_data=f"search_export_{i}")
            ])

        await update.message.reply_text(
            chat_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )

    except Exception as e:
        logger.error(f"Error searching chats: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка поиска: {str(e)}"
        )
    finally:
        try:
            await client.disconnect()
        except:
            pass


async def show_export_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show paginated chat list with export buttons."""
    dialogs = context.user_data.get('export_dialogs', [])
    total_pages = (len(dialogs) + CHATS_PER_PAGE - 1) // CHATS_PER_PAGE

    if page < 0 or page >= total_pages:
        return

    start_idx = page * CHATS_PER_PAGE
    end_idx = start_idx + CHATS_PER_PAGE
    page_dialogs = dialogs[start_idx:end_idx]

    # Create inline buttons for each chat
    keyboard = []
    for i, dialog in enumerate(page_dialogs):
        idx = start_idx + i
        chat_type = "👤" if dialog['is_user'] else "👥" if dialog['is_group'] else "📢"
        button_text = f"{chat_type} {dialog['name'][:30]}"
        keyboard.append([
            InlineKeyboardButton(button_text, callback_data=f"export_chat_{idx}")
        ])

    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"export_page_{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="export_page_noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"export_page_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    text = "*Выбери чат для экспорта:*\n\nИспользуй /search для поиска."

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def export_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start export - show chats with inline buttons."""
    user_id = update.effective_user.id

    if not db.is_user_authenticated(user_id):
        await update.message.reply_text(
            "❌ Сначала нужно авторизоваться. Используй /login"
        )
        return

    client = get_user_client(user_id)
    if not client:
        await update.message.reply_text(
            "❌ Сессия не найдена. Используй /login для авторизации."
        )
        return

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await update.message.reply_text(
                "❌ Сессия истекла. Используй /login для повторной авторизации."
            )
            return

        await update.message.reply_text("📋 Загружаю твои чаты...")

        # Get dialogs
        dialogs = await client.get_dialogs(limit=50)

        if not dialogs:
            await update.message.reply_text("Чаты не найдены.")
            return

        # Store dialogs in context
        context.user_data['export_dialogs'] = []
        for dialog in dialogs:
            chat_id, chat_type = get_chat_identity(dialog)
            context.user_data['export_dialogs'].append({
                'id': dialog.id,
                'name': dialog.name,
                'is_user': dialog.is_user,
                'is_group': dialog.is_group,
                'is_channel': dialog.is_channel,
                'chat_id': chat_id,
                'chat_type': chat_type
            })

        # Show first page with buttons
        await show_export_page(update, context, 0)

    except FloodWaitError as e:
        await update.message.reply_text(
            f"⏳ Лимит запросов. Подожди {e.seconds} сек. и попробуй снова."
        )
    except Exception as e:
        logger.error(f"Error starting export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    finally:
        try:
            await client.disconnect()
        except:
            pass


async def export_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle export pagination."""
    query = update.callback_query
    await query.answer()

    if query.data == "export_page_noop":
        return

    try:
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ Неверный формат данных")
            return
        page = int(parts[2])
    except (ValueError, IndexError) as e:
        logger.error(f"Invalid callback_data: {query.data}, error: {e}")
        await query.edit_message_text("❌ Ошибка обработки кнопки")
        return

    await show_export_page(update, context, page)


async def export_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle chat selection from export list."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    try:
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ Неверный формат данных")
            return
        index = int(parts[2])
        dialogs = context.user_data.get('export_dialogs', [])

        if index < 0 or index >= len(dialogs):
            await query.edit_message_text("❌ Чат не найден")
            return

        selected_chat = dialogs[index]
        context.user_data['selected_chat'] = selected_chat
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Check if this chat was previously exported
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        if last_message_id:
            # Chat was already exported - show options
            keyboard = [
                [InlineKeyboardButton("📥 Только новые", callback_data="export_mode_incremental")],
                [InlineKeyboardButton("🔄 Экспорт заново", callback_data="export_mode_full")],
                [InlineKeyboardButton("⬇️ Все сообщения (10000)", callback_data="export_mode_all_max")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.insert(1, [InlineKeyboardButton("📥 Только новые + транскрипция", callback_data="export_mode_incremental_transcribe")])
                keyboard.append([InlineKeyboardButton("🎤 Все + транскрипция", callback_data="export_mode_all_max_transcribe")])
            keyboard.append([InlineKeyboardButton("🎬 Скачать видео из чата", callback_data="export_mode_videos")])
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Этот чат уже экспортировался. Выбери опцию:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # First export - show options
            keyboard = [
                [InlineKeyboardButton("⬇️ Все сообщения (10000)", callback_data="export_mode_all_max")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.append([InlineKeyboardButton("🎤 Все + транскрипция", callback_data="export_mode_all_max_transcribe")])
            keyboard.append([InlineKeyboardButton("⚙️ Указать количество", callback_data="export_mode_custom")])
            keyboard.append([InlineKeyboardButton("🎬 Скачать видео из чата", callback_data="export_mode_videos")])
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Сколько сообщений экспортировать?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    except (ValueError, IndexError) as e:
        logger.error(f"Invalid callback_data in export_chat_callback: {query.data}, error: {e}")
        await query.edit_message_text("❌ Ошибка обработки кнопки")
    except Exception as e:
        logger.error(f"Error in export_chat_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


async def export_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle export mode selection for /export command (incremental vs full)."""
    query = update.callback_query
    await query.answer()

    try:
        callback_data = query.data

        if callback_data == "export_mode_incremental":
            # User chose "only new messages"
            context.user_data['export_mode'] = 'incremental'
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую новые сообщения из *{selected_chat['name']}*...\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            await export_do_incremental(update, context)

        elif callback_data == "export_mode_incremental_transcribe":
            # User chose "only new messages + transcription"
            context.user_data['export_mode'] = 'incremental'
            context.user_data['transcribe_voice'] = True
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую новые сообщения из *{selected_chat['name']}* с транскрипцией голосовых...\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            await export_do_incremental(update, context)

        elif callback_data == "export_mode_full":
            # User chose "export all again" - needs custom limit
            context.user_data['export_mode'] = 'full'
            context.user_data['awaiting_export_limit'] = True
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Сколько сообщений экспортировать? (По умолчанию: 1000, Макс: 10000)\n"
                "Напиши число",
                parse_mode=ParseMode.MARKDOWN
            )

        elif callback_data == "export_mode_all_max":
            # User chose "export all (10000)"
            context.user_data['export_mode'] = 'full'
            context.user_data['transcribe_voice'] = False
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую все сообщения из *{selected_chat['name']}* (до 10000)...\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            await export_do_export_with_limit(update, context, 10000)

        elif callback_data == "export_mode_all_max_transcribe":
            # User chose "export all (10000) + transcribe voice"
            context.user_data['export_mode'] = 'full'
            context.user_data['transcribe_voice'] = True
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую все сообщения из *{selected_chat['name']}* (до 10000)...\n"
                "🎤 Голосовые сообщения будут транскрибированы.\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            await export_do_export_with_limit(update, context, 10000)

        elif callback_data == "export_mode_custom":
            # User chose "custom amount"
            context.user_data['awaiting_export_limit'] = True
            await query.edit_message_text(
                "Сколько сообщений экспортировать? (По умолчанию: 1000, Макс: 10000)\n"
                "Напиши число"
            )

        elif callback_data == "export_mode_videos":
            # User chose "download videos"
            await video_scan_callback(update, context)

    except Exception as e:
        logger.error(f"Error in export_mode_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


async def export_do_incremental(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform incremental export (new messages only)."""
    user_id = update.effective_user.id
    client = None
    filepath = None

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.effective_chat.send_message("❌ Выбор чата потерян. Попробуй снова.")
            return

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get transcription flag
        transcribe = context.user_data.get('transcribe_voice', False)

        # Get last message id for incremental export
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Сессия не найдена")
            return

        await connect_client(client)

        # Export only new messages
        messages_data = []
        message_ids = []
        voice_count = 0
        transcribed_count = 0

        async for message in client.iter_messages(selected_chat['chat_id'], min_id=last_message_id):
            transcription = None

            # Transcribe voice messages if enabled
            if transcribe and is_voice_message(message):
                voice_count += 1
                try:
                    transcription = await transcribe_voice(client, message)
                    if transcription:
                        transcribed_count += 1
                    await asyncio.sleep(0.5)  # 500ms delay after transcription
                except Exception as e:
                    logger.error(f"Failed to transcribe voice message {message.id}: {e}")

            content = format_message_content(message, transcription)
            if content:
                sender = get_sender_name(message)
                messages_data.append((message.date, sender, content))
                message_ids.append(message.id)

            # Anti-spam delay to prevent FloodWait
            await asyncio.sleep(0.05)  # 50ms between messages

        # Check if there are any new messages
        if not messages_data:
            await update.effective_chat.send_message(
                f"⚠️ Нет новых сообщений в *{selected_chat['name']}* с последнего экспорта.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Reverse to chronological order
        messages_data.reverse()

        # Format with time markers
        messages = format_messages_with_time_markers(messages_data, time_interval_minutes=30)

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Формат: временные маркеры каждые 30 минут\n")
            f.write(f"Тип экспорта: Инкрементальный (только новые сообщения)\n")
            if transcribe:
                f.write(f"Голосовые сообщения: {voice_count} найдено, {transcribed_count} транскрибировано\n")
            f.write(f"Всего сообщений: {len(messages_data)}\n")
            f.write("=" * 80 + "\n")
            f.write("\n".join(messages))

        # Send file
        caption = f"✅ Экспортировано {len(messages_data)} новых сообщений из *{selected_chat['name']}* (с последнего экспорта)"
        if transcribe and voice_count > 0:
            caption += f"\n🎤 Транскрибировано {transcribed_count} из {voice_count} голосовых сообщений"

        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )

        # Reset transcribe flag
        context.user_data.pop('transcribe_voice', None)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during incremental export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
    finally:
        # Clean up file
        if filepath:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Failed to remove temp file {filepath}: {e}")

        if client:
            try:
                await client.disconnect()
            except:
                pass


async def handle_export_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle message limit input for both /export and /search export."""
    # Check which export is awaiting limit
    awaiting_export = context.user_data.get('awaiting_export_limit')
    awaiting_search = context.user_data.get('awaiting_search_export_limit')

    if not awaiting_export and not awaiting_search:
        return  # Not waiting for export limit input

    # Clear the flags
    context.user_data['awaiting_export_limit'] = False
    context.user_data['awaiting_search_export_limit'] = False

    user_id = update.effective_user.id
    client = None
    filepath = None

    try:
        # Parse limit
        limit = 1000
        if update.message.text.isdigit():
            limit = min(int(update.message.text), 10000)  # Max 10k messages

        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.message.reply_text("❌ Выбор чата потерян. Попробуй снова.")
            return

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        await update.message.reply_text(
            f"⏳ Экспортирую до {limit} сообщений из *{selected_chat['name']}*...\n"
            "Это может занять некоторое время.",
            parse_mode=ParseMode.MARKDOWN
        )

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.message.reply_text("❌ Сессия не найдена")
            return

        await connect_client(client)

        # Export messages
        messages_data = []
        message_ids = []

        async for message in client.iter_messages(selected_chat['chat_id'], limit=limit):
            content = format_message_content(message)
            if content:
                sender = get_sender_name(message)
                messages_data.append((message.date, sender, content))
                message_ids.append(message.id)

            # Anti-spam delay to prevent FloodWait
            await asyncio.sleep(0.05)  # 50ms between messages

        if not messages_data:
            await update.message.reply_text("❌ Сообщения в этом чате не найдены")
            return

        # Reverse to chronological order
        messages_data.reverse()

        # Format with time markers
        messages = format_messages_with_time_markers(messages_data, time_interval_minutes=30)

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Формат: временные маркеры каждые 30 минут\n")
            f.write(f"Тип экспорта: Полный экспорт\n")
            f.write(f"Всего сообщений: {len(messages_data)}\n")
            f.write("=" * 80 + "\n")
            f.write("\n".join(messages))

        caption = f"✅ Полный экспорт *{selected_chat['name']}* - {len(messages_data)} сообщений"

        # Send file
        with open(filepath, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка экспорта: {str(e)}")
    finally:
        # Clean up file
        if filepath:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Failed to remove temp file {filepath}: {e}")

        if client:
            try:
                await client.disconnect()
            except:
                pass


async def export_do_export_with_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int):
    """Perform export with a preset limit (called from callback buttons)."""
    user_id = update.effective_user.id
    transcribe = context.user_data.get('transcribe_voice', False)
    client = None
    filepath = None

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.effective_chat.send_message("❌ Выбор чата потерян. Попробуй снова.")
            return

        # Get chat identity for progress tracking
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Сессия не найдена")
            return

        await connect_client(client)

        # Export messages
        messages_data = []
        message_ids = []
        voice_count = 0
        transcribed_count = 0

        async for message in client.iter_messages(selected_chat['chat_id'], limit=limit):
            transcription = None

            # Transcribe voice messages if enabled
            if transcribe and is_voice_message(message):
                voice_count += 1
                transcription = await transcribe_voice(client, message)
                if transcription:
                    transcribed_count += 1
                await asyncio.sleep(0.5)  # 500ms delay after transcription

            content = format_message_content(message, transcription)
            if content:
                sender = get_sender_name(message)
                messages_data.append((message.date, sender, content))
                message_ids.append(message.id)

            # Anti-spam delay to prevent FloodWait
            await asyncio.sleep(0.05)  # 50ms between messages

        if not messages_data:
            await update.effective_chat.send_message("❌ Сообщения в этом чате не найдены")
            return

        # Reverse to chronological order
        messages_data.reverse()

        # Format with time markers
        messages = format_messages_with_time_markers(messages_data, time_interval_minutes=30)

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Формат: временные маркеры каждые 30 минут\n")
            f.write(f"Тип экспорта: Полный экспорт\n")
            if transcribe:
                f.write(f"Транскрипция голосовых: {transcribed_count}/{voice_count} транскрибировано\n")
            f.write(f"Всего сообщений: {len(messages_data)}\n")
            f.write("=" * 80 + "\n")
            f.write("\n".join(messages))

        caption = f"✅ Полный экспорт *{selected_chat['name']}* - {len(messages_data)} сообщений"
        if transcribe and voice_count > 0:
            caption += f"\n🎤 Транскрибировано {transcribed_count}/{voice_count} голосовых сообщений"

        # Send file
        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
    finally:
        # Clean up file
        if filepath:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Failed to remove temp file {filepath}: {e}")

        if client:
            try:
                await client.disconnect()
            except:
                pass


async def search_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle export button from search results."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    try:
        # Extract index from callback data
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ Неверный формат данных")
            return
        index = int(parts[2])
        search_results = context.user_data.get('search_results', [])

        if index < 0 or index >= len(search_results):
            await query.edit_message_text("❌ Чат не найден")
            return

        # Store selected chat
        selected_chat = search_results[index]
        context.user_data['selected_chat'] = selected_chat
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Check if this chat was previously exported
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        if last_message_id:
            # Chat was already exported - show options
            keyboard = [
                [InlineKeyboardButton("📥 Только новые", callback_data=f"search_export_mode_incremental_{index}")],
                [InlineKeyboardButton("🔄 Экспорт заново", callback_data=f"search_export_mode_full_{index}")],
                [InlineKeyboardButton("⬇️ Все сообщения (10000)", callback_data=f"search_export_mode_all_max_{index}")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.insert(1, [InlineKeyboardButton("📥 Только новые + транскрипция", callback_data=f"search_export_mode_incremental_transcribe_{index}")])
                keyboard.append([InlineKeyboardButton("🎤 Все + транскрипция", callback_data=f"search_export_mode_transcribe_{index}")])
            keyboard.append([InlineKeyboardButton("🎬 Скачать видео", callback_data=f"search_export_mode_videos_{index}")])
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Этот чат уже экспортировался. Выбери опцию:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # First export - show options with quick button
            keyboard = [
                [InlineKeyboardButton("⬇️ Все сообщения (10000)", callback_data=f"search_export_mode_all_max_{index}")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.append([InlineKeyboardButton("🎤 Все + транскрипция", callback_data=f"search_export_mode_transcribe_{index}")])
            keyboard.append([InlineKeyboardButton("⚙️ Указать количество", callback_data=f"search_export_mode_custom_{index}")])
            keyboard.append([InlineKeyboardButton("🎬 Скачать видео", callback_data=f"search_export_mode_videos_{index}")])
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Сколько сообщений экспортировать?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting_search_export_limit'] = True

    except (ValueError, IndexError) as e:
        logger.error(f"Invalid callback_data in search_export_callback: {query.data}, error: {e}")
        await query.edit_message_text("❌ Ошибка обработки кнопки")
    except Exception as e:
        logger.error(f"Error in search_export_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


async def search_export_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle export mode selection (incremental vs full)."""
    query = update.callback_query
    await query.answer()

    try:
        callback_data = query.data

        if callback_data.startswith("search_export_mode_incremental_transcribe_"):
            # User chose "only new messages + transcription"
            context.user_data['export_mode'] = 'incremental'
            context.user_data['transcribe_voice'] = True
            context.user_data['awaiting_search_export_limit'] = False

            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую новые сообщения из *{selected_chat['name']}* с транскрипцией голосовых...\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Trigger the export immediately without waiting for user input
            await search_export_do_incremental(update, context)

        elif callback_data.startswith("search_export_mode_incremental_"):
            # User chose "only new messages"
            context.user_data['export_mode'] = 'incremental'
            context.user_data['awaiting_search_export_limit'] = False

            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую новые сообщения из *{selected_chat['name']}*...\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Trigger the export immediately without waiting for user input
            await search_export_do_incremental(update, context)

        elif callback_data.startswith("search_export_mode_full_"):
            # User chose "export all again"
            context.user_data['export_mode'] = 'full'
            context.user_data['awaiting_search_export_limit'] = True

            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Сколько сообщений экспортировать? (По умолчанию: 1000, Макс: 10000)\n"
                "Напиши число",
                parse_mode=ParseMode.MARKDOWN
            )

        elif callback_data.startswith("search_export_mode_all_max_"):
            # User chose "export all (10000)"
            context.user_data['export_mode'] = 'full'
            context.user_data['awaiting_search_export_limit'] = False
            context.user_data['export_limit'] = 10000
            context.user_data['transcribe_voice'] = False

            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую все сообщения из *{selected_chat['name']}* (до 10000)...\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Export with preset limit
            await search_export_with_limit(update, context, 10000)

        elif callback_data.startswith("search_export_mode_transcribe_"):
            # User chose "export all + transcribe"
            context.user_data['export_mode'] = 'full'
            context.user_data['awaiting_search_export_limit'] = False
            context.user_data['export_limit'] = 10000
            context.user_data['transcribe_voice'] = True

            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Экспортирую все сообщения из *{selected_chat['name']}* (до 10000)...\n"
                "🎤 Голосовые сообщения будут транскрибированы.\n"
                "Это может занять некоторое время.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Export with preset limit and transcription
            await search_export_with_limit(update, context, 10000)

        elif callback_data.startswith("search_export_mode_videos_"):
            # User chose "download videos"
            context.user_data['awaiting_search_export_limit'] = False
            await video_scan_callback(update, context)

        elif callback_data.startswith("search_export_mode_custom_"):
            # User chose "custom amount"
            context.user_data['awaiting_search_export_limit'] = True
            await query.edit_message_text(
                "Сколько сообщений экспортировать? (По умолчанию: 1000, Макс: 10000)\n"
                "Напиши число"
            )

    except Exception as e:
        logger.error(f"Error in search_export_mode_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


async def search_export_do_incremental(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform incremental export (new messages only)."""
    user_id = update.effective_user.id
    client = None
    filepath = None

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.callback_query.edit_message_text("❌ Выбор чата потерян. Попробуй снова.")
            return

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get transcription flag
        transcribe = context.user_data.get('transcribe_voice', False)

        # Get last message id for incremental export
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.callback_query.edit_message_text("❌ Сессия не найдена")
            return

        await connect_client(client)

        # Export only new messages
        messages_data = []
        message_ids = []
        voice_count = 0
        transcribed_count = 0

        async for message in client.iter_messages(selected_chat['chat_id'], min_id=last_message_id):
            transcription = None

            # Transcribe voice messages if enabled
            if transcribe and is_voice_message(message):
                voice_count += 1
                try:
                    transcription = await transcribe_voice(client, message)
                    if transcription:
                        transcribed_count += 1
                    await asyncio.sleep(0.5)  # 500ms delay after transcription
                except Exception as e:
                    logger.error(f"Failed to transcribe voice message {message.id}: {e}")

            content = format_message_content(message, transcription)
            if content:
                sender = get_sender_name(message)
                messages_data.append((message.date, sender, content))
                message_ids.append(message.id)

            # Anti-spam delay to prevent FloodWait
            await asyncio.sleep(0.05)  # 50ms between messages

        # Check if there are any new messages
        if not messages_data:
            await update.callback_query.edit_message_text(
                f"⚠️ Нет новых сообщений в *{selected_chat['name']}* с последнего экспорта.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Reverse to chronological order
        messages_data.reverse()

        # Format with time markers
        messages = format_messages_with_time_markers(messages_data, time_interval_minutes=30)

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Формат: временные маркеры каждые 30 минут\n")
            f.write(f"Тип экспорта: Инкрементальный (только новые сообщения)\n")
            if transcribe:
                f.write(f"Голосовые сообщения: {voice_count} найдено, {transcribed_count} транскрибировано\n")
            f.write(f"Всего сообщений: {len(messages_data)}\n")
            f.write("=" * 80 + "\n")
            f.write("\n".join(messages))

        # Send file
        caption = f"✅ Экспортировано {len(messages_data)} новых сообщений из *{selected_chat['name']}* (с последнего экспорта)"
        if transcribe and voice_count > 0:
            caption += f"\n🎤 Транскрибировано {transcribed_count} из {voice_count} голосовых сообщений"

        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )

        # Reset transcribe flag
        context.user_data.pop('transcribe_voice', None)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during incremental export: {str(e)}", exc_info=True)
        try:
            await update.callback_query.edit_message_text(f"❌ Export failed: {str(e)}")
        except:
            await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
    finally:
        # Clean up file
        if filepath:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Failed to remove temp file {filepath}: {e}")

        if client:
            try:
                await client.disconnect()
            except:
                pass


async def search_export_with_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int):
    """Perform search export with a preset limit (called from callback buttons)."""
    user_id = update.effective_user.id
    transcribe = context.user_data.get('transcribe_voice', False)
    client = None
    filepath = None

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.effective_chat.send_message("❌ Выбор чата потерян. Попробуй снова.")
            return

        # Get chat identity for progress tracking
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Сессия не найдена")
            return

        await connect_client(client)

        # Export messages
        messages_data = []
        message_ids = []
        voice_count = 0
        transcribed_count = 0

        async for message in client.iter_messages(selected_chat['chat_id'], limit=limit):
            transcription = None

            # Transcribe voice messages if enabled
            if transcribe and is_voice_message(message):
                voice_count += 1
                transcription = await transcribe_voice(client, message)
                if transcription:
                    transcribed_count += 1
                await asyncio.sleep(0.5)  # 500ms delay after transcription

            content = format_message_content(message, transcription)
            if content:
                sender = get_sender_name(message)
                messages_data.append((message.date, sender, content))
                message_ids.append(message.id)

            # Anti-spam delay to prevent FloodWait
            await asyncio.sleep(0.05)  # 50ms between messages

        if not messages_data:
            await update.effective_chat.send_message("❌ Сообщения в этом чате не найдены")
            return

        # Reverse to chronological order
        messages_data.reverse()

        # Format with time markers
        messages = format_messages_with_time_markers(messages_data, time_interval_minutes=30)

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Формат: временные маркеры каждые 30 минут\n")
            f.write(f"Тип экспорта: Полный экспорт\n")
            if transcribe:
                f.write(f"Транскрипция голосовых: {transcribed_count}/{voice_count} транскрибировано\n")
            f.write(f"Всего сообщений: {len(messages_data)}\n")
            f.write("=" * 80 + "\n")
            f.write("\n".join(messages))

        caption = f"✅ Полный экспорт *{selected_chat['name']}* - {len(messages_data)} сообщений"
        if transcribe and voice_count > 0:
            caption += f"\n🎤 Транскрибировано {transcribed_count}/{voice_count} голосовых сообщений"

        # Send file
        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during search export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
    finally:
        # Clean up file
        if filepath:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Failed to remove temp file {filepath}: {e}")

        if client:
            try:
                await client.disconnect()
            except:
                pass


VIDEOS_PER_PAGE = 5


async def show_video_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    """Render paginated video list with toggle selection buttons."""
    video_list = context.user_data.get('video_list', [])
    selected = context.user_data.get('video_selected', set())
    total = len(video_list)
    total_pages = max(1, (total + VIDEOS_PER_PAGE - 1) // VIDEOS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    context.user_data['video_page'] = page

    start = page * VIDEOS_PER_PAGE
    end = min(start + VIDEOS_PER_PAGE, total)
    page_videos = video_list[start:end]

    lines = [f"*Видео в чате* ({total} найдено)\n"]
    keyboard = []

    for i, vid in enumerate(page_videos):
        idx = start + i
        check = "\u2705 " if idx in selected else ""
        large_tag = " (> 50MB, -> Saved)" if vid['is_large'] else ""
        dur = format_duration(vid['duration'])
        line = f"{check}{idx + 1}. {vid['date_str']} | {vid['sender']}\n    {vid['size_mb']} MB, {dur}{large_tag}"
        lines.append(line)
        btn_label = f"{'✅ ' if idx in selected else ''}{idx + 1}. {vid['size_mb']}MB {dur}"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"vid_sel_{idx}")])

    # Action buttons row
    action_row = []
    if len(selected) < total:
        action_row.append(InlineKeyboardButton("Выбрать все", callback_data="vid_all"))
    else:
        action_row.append(InlineKeyboardButton("Снять все", callback_data="vid_none"))
    if selected:
        action_row.append(InlineKeyboardButton(f"Скачать ({len(selected)})", callback_data="vid_download"))
    action_row.append(InlineKeyboardButton("Отмена", callback_data="vid_cancel"))
    keyboard.append(action_row)

    # Pagination row
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("<<", callback_data=f"vid_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="vid_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(">>", callback_data=f"vid_page_{page + 1}"))
        keyboard.append(nav_row)

    text = "\n".join(lines)
    reply_markup = InlineKeyboardMarkup(keyboard)

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


async def video_scan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan chat for videos and show paginated list."""
    query = update.callback_query
    user_id = update.effective_user.id

    selected_chat = context.user_data.get('selected_chat')
    if not selected_chat:
        await query.edit_message_text("❌ Чат не выбран. Попробуй снова.")
        return

    await query.edit_message_text(
        f"🔍 Сканирую видео в *{selected_chat['name']}*...\nЭто может занять некоторое время.",
        parse_mode=ParseMode.MARKDOWN
    )

    client = get_user_client(user_id)
    if not client:
        await query.edit_message_text("❌ Сессия не найдена. Используй /login")
        return

    try:
        await connect_client(client)

        video_list = []
        count = 0
        async for message in client.iter_messages(selected_chat['chat_id'], limit=10000):
            count += 1
            if is_video_message(message):
                meta = get_video_metadata(message)
                video_list.append(meta)
            # Progress update every 2000 messages
            if count % 2000 == 0:
                try:
                    await query.edit_message_text(
                        f"🔍 Сканирую видео в *{selected_chat['name']}*...\n"
                        f"Проверено {count} сообщений, найдено {len(video_list)} видео.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.02)

        if not video_list:
            await query.edit_message_text(
                f"❌ Видео не найдены в *{selected_chat['name']}*.\n"
                f"Проверено {count} сообщений.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        context.user_data['video_list'] = video_list
        context.user_data['video_selected'] = set()
        context.user_data['video_page'] = 0

        await show_video_page(update, context, 0)

    except FloodWaitError as e:
        await query.edit_message_text(
            f"⏳ Лимит запросов Telegram. Подожди {e.seconds} сек. и попробуй снова."
        )
    except Exception as e:
        logger.error(f"Error scanning videos: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка сканирования: {str(e)}")
    finally:
        try:
            await client.disconnect()
        except:
            pass


async def video_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all vid_* callbacks: selection, pagination, download, cancel."""
    query = update.callback_query
    await query.answer()

    data = query.data
    video_list = context.user_data.get('video_list', [])
    selected = context.user_data.get('video_selected', set())

    if data.startswith("vid_sel_"):
        idx = int(data.split("_")[2])
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        context.user_data['video_selected'] = selected
        page = context.user_data.get('video_page', 0)
        await show_video_page(update, context, page)

    elif data == "vid_all":
        context.user_data['video_selected'] = set(range(len(video_list)))
        page = context.user_data.get('video_page', 0)
        await show_video_page(update, context, page)

    elif data == "vid_none":
        context.user_data['video_selected'] = set()
        page = context.user_data.get('video_page', 0)
        await show_video_page(update, context, page)

    elif data.startswith("vid_page_"):
        page = int(data.split("_")[2])
        await show_video_page(update, context, page)

    elif data == "vid_download":
        await video_download_execute(update, context)

    elif data == "vid_cancel":
        context.user_data.pop('video_list', None)
        context.user_data.pop('video_selected', None)
        context.user_data.pop('video_page', None)
        await query.edit_message_text("❌ Загрузка видео отменена.")

    elif data == "vid_noop":
        pass


async def video_download_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download selected videos and send to user."""
    query = update.callback_query
    user_id = update.effective_user.id

    video_list = context.user_data.get('video_list', [])
    selected = context.user_data.get('video_selected', set())
    selected_chat = context.user_data.get('selected_chat')

    if not selected or not video_list or not selected_chat:
        await query.edit_message_text("❌ Нет выбранных видео.")
        return

    selected_videos = [video_list[i] for i in sorted(selected)]
    total = len(selected_videos)

    await query.edit_message_text(
        f"⏳ Начинаю загрузку {total} видео из *{selected_chat['name']}*...",
        parse_mode=ParseMode.MARKDOWN
    )

    client = get_user_client(user_id)
    if not client:
        await query.edit_message_text("❌ Сессия не найдена. Используй /login")
        return

    sent_count = 0
    forwarded_count = 0
    failed_count = 0
    chat_id = update.effective_chat.id

    try:
        await connect_client(client)

        for i, vid in enumerate(selected_videos):
            filepath = None
            try:
                # Progress update
                try:
                    await query.edit_message_text(
                        f"⏳ Загрузка видео {i + 1}/{total}...\n"
                        f"({vid['size_mb']} MB, {format_duration(vid['duration'])})",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

                msg = await client.get_messages(selected_chat['chat_id'], ids=vid['message_id'])
                if not msg:
                    failed_count += 1
                    continue

                if vid['is_large']:
                    # Forward to Saved Messages for large files
                    await client.forward_messages('me', msg, selected_chat['chat_id'])
                    forwarded_count += 1
                else:
                    # Download and send via Bot API
                    filepath = f"/tmp/video_{user_id}_{vid['message_id']}.mp4"
                    await client.download_media(msg, file=filepath)

                    if os.path.exists(filepath):
                        caption = f"{vid['date_str']} | {vid['sender']}"
                        with open(filepath, 'rb') as f:
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=f,
                                caption=caption,
                                supports_streaming=True,
                            )
                        sent_count += 1
                    else:
                        failed_count += 1

                # Rate limiting delay between videos
                await asyncio.sleep(2)

            except FloodWaitError as e:
                logger.warning(f"FloodWait {e.seconds}s during video download")
                try:
                    await query.edit_message_text(
                        f"⏳ Telegram просит подождать {e.seconds} сек..."
                    )
                except Exception:
                    pass
                await asyncio.sleep(e.seconds + 1)
                failed_count += 1
            except Exception as e:
                logger.error(f"Error downloading video {vid['message_id']}: {e}")
                failed_count += 1
            finally:
                if filepath and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass

        # Final summary
        parts = []
        if sent_count:
            parts.append(f"✅ Отправлено: {sent_count}")
        if forwarded_count:
            parts.append(f"📨 В Избранное: {forwarded_count}")
        if failed_count:
            parts.append(f"❌ Ошибки: {failed_count}")
        summary = "\n".join(parts) or "Ничего не загружено."

        await query.edit_message_text(
            f"*Загрузка завершена*\n\n{summary}",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error in video_download_execute: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка загрузки: {str(e)}")
    finally:
        try:
            await client.disconnect()
        except:
            pass
        # Clean up user data
        context.user_data.pop('video_list', None)
        context.user_data.pop('video_selected', None)
        context.user_data.pop('video_page', None)


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logout command with confirmation."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, выйти", callback_data="logout_yes"),
            InlineKeyboardButton("❌ Нет, оставить", callback_data="logout_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚠️ *Подтверждение выхода*\n\n"
        "Уверен, что хочешь удалить сессию?\n"
        "Нужно будет заново войти через /login",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def logout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle logout confirmation callback."""
    query = update.callback_query
    await query.answer()

    if query.data == "logout_yes":
        user_id = update.effective_user.id
        db.delete_user_data(user_id)

        await query.edit_message_text(
            "✅ Сессия успешно удалена.\n\n"
            "Используй /login для повторной авторизации."
        )
    else:
        await query.edit_message_text("❌ Выход отменён. Сессия всё ещё активна.")


def main():
    """Start the bot."""
    logger.info("Starting bot...")

    # Create application with rate limiter to prevent FloodWait
    builder = Application.builder().token(BOT_TOKEN)
    if HAS_RATE_LIMITER:
        builder = builder.rate_limiter(AIORateLimiter(
            overall_max_rate=30,      # 30 requests per second globally
            overall_time_period=1.0,  # per 1 second
            group_max_rate=20,        # 20 messages per minute for groups
            group_time_period=60.0,   # per 60 seconds
        ))
    else:
        logger.warning("AIORateLimiter not available. Install python-telegram-bot[rate-limiter] for rate limiting.")
    application = builder.build()

    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("apihelp", apihelp_command))
    application.add_handler(CommandHandler("privacy", privacy_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("logout", logout_command))

    # Export command handler
    application.add_handler(CommandHandler("export", export_start))

    # Export pagination callback handler
    application.add_handler(CallbackQueryHandler(export_page_callback, pattern="^export_page_"))

    # Export chat selection callback handler
    application.add_handler(CallbackQueryHandler(export_chat_callback, pattern="^export_chat_"))

    # Video selection/download callback handler
    application.add_handler(CallbackQueryHandler(video_select_callback, pattern="^vid_"))

    # Export mode callback handler (for /export command - incremental vs full)
    application.add_handler(CallbackQueryHandler(export_mode_callback, pattern="^export_mode_"))

    # Search export callback handler
    application.add_handler(CallbackQueryHandler(search_export_callback, pattern="^search_export_[0-9]+$"))

    # Search export mode callback handler (for incremental vs full choice)
    application.add_handler(CallbackQueryHandler(search_export_mode_callback, pattern="^search_export_mode_"))

    # Export limit handler (listen for message responses for custom amount)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_export_limit))

    # Logout callback handler
    application.add_handler(CallbackQueryHandler(logout_callback, pattern="^logout_"))

    # Start bot
    logger.info("Bot started successfully")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
