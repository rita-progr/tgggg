"""
Telegram Bot for exporting chat history.
Uses Telethon sessions stored in database after WebApp authentication.
"""
import os
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
    Create Telethon client from stored session.

    Args:
        user_id: Telegram user ID

    Returns:
        TelegramClient instance or None if session not found
    """
    session_string = db.get_session_string(user_id)
    if not session_string:
        return None

    return TelegramClient(
        StringSession(session_string),
        TG_API_ID,
        TG_API_HASH
    )


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
        if text and not is_voice:
            return f"{media_type} {text}"
        else:
            return media_type

    # Plain text message
    if text:
        return text

    # Message with no content we can export
    return None


def get_sender_name(message) -> str:
    """Extract sender name from message."""
    if message.sender:
        if isinstance(message.sender, TelethonUser):
            name = f"{message.sender.first_name or ''} {message.sender.last_name or ''}".strip()
            if not name:
                name = f"User_{message.sender.id}"
            return name
        else:
            return getattr(message.sender, 'title', 'Unknown')
    return "Unknown"


# Command handlers

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    keyboard = [[
        InlineKeyboardButton("🔐 Войти через WebApp", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]

    await update.message.reply_text(
        "👋 Привет! Это бот для экспорта истории чатов Telegram.\n\n"
        "Я помогу сохранить переписку в текстовые файлы.\n\n"
        "Для начала:\n"
        "1️⃣ Нажми кнопку ниже для авторизации\n"
        "2️⃣ Используй /export для выбора и экспорта чата\n"
        "3️⃣ Или /search для поиска по названию\n\n"
        "Напиши /help для справки.",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
    keyboard = [[
        InlineKeyboardButton("🔐 Войти через WebApp", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]

    await update.message.reply_text(
        "🔐 *Авторизация*\n\n"
        "Нажми кнопку ниже, чтобы открыть страницу авторизации.\n\n"
        "📝 *Шаги:*\n"
        "1️⃣ Введи номер телефона\n"
        "2️⃣ Введи код подтверждения\n"
        "3️⃣ Введи пароль 2FA (если включён)\n\n"
        "⚠️ Все данные вводятся на веб-странице, не в этом чате.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    user_id = update.effective_user.id

    has_session = db.user_exists(user_id)
    is_authenticated = db.is_user_authenticated(user_id)

    if not has_session:
        await update.message.reply_text(
            "❌ *Не авторизован*\n\n"
            "Ты ещё не вошёл в аккаунт. Используй /login для авторизации.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if is_authenticated:
        await update.message.reply_text(
            "✅ *Авторизован*\n\n"
            "Ты вошёл в аккаунт и можешь использовать /export и /search.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "⚠️ *Сессия есть, но не авторизована*\n\n"
            "Попробуй войти заново через /login",
            parse_mode=ParseMode.MARKDOWN
        )


CHATS_PER_PAGE = 10


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
            await client.disconnect()
            return

        # Get all dialogs
        dialogs = await client.get_dialogs(limit=100)
        await client.disconnect()

        # Filter by search query
        results = [d for d in dialogs if search_query in d.name.lower()]

        if not results:
            await update.message.reply_text(
                f"❌ Чаты по запросу '{search_query}' не найдены"
            )
            return

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
            await client.disconnect()
            return

        await update.message.reply_text("📋 Загружаю твои чаты...")

        # Get dialogs
        dialogs = await client.get_dialogs(limit=50)
        await client.disconnect()

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
        try:
            await client.disconnect()
        except:
            pass

    except Exception as e:
        logger.error(f"Error starting export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
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

    page = int(query.data.split('_')[2])
    await show_export_page(update, context, page)


async def export_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle chat selection from export list."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    try:
        index = int(query.data.split('_')[2])
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
                keyboard.append([InlineKeyboardButton("🎤 Все + транскрипция", callback_data="export_mode_all_max_transcribe")])
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
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Сколько сообщений экспортировать?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

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

    except Exception as e:
        logger.error(f"Error in export_mode_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


async def export_do_incremental(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform incremental export (new messages only)."""
    user_id = update.effective_user.id

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.effective_chat.send_message("❌ Выбор чата потерян. Попробуй снова.")
            return

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get last message id for incremental export
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Сессия не найдена")
            return

        await client.connect()

        # Export only new messages
        messages = []
        message_ids = []

        async for message in client.iter_messages(selected_chat['id'], min_id=last_message_id):
            content = format_message_content(message)
            if content:
                sender = get_sender_name(message)
                timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender}: {content}")
                message_ids.append(message.id)

        await client.disconnect()

        # Check if there are any new messages
        if not messages:
            await update.effective_chat.send_message(
                f"⚠️ Нет новых сообщений в *{selected_chat['name']}* с последнего экспорта.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Тип экспорта: Инкрементальный (только новые сообщения)\n")
            f.write(f"Всего сообщений: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        # Send file
        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=f"✅ Экспортировано {len(messages)} новых сообщений из *{selected_chat['name']}* (с последнего экспорта)",
                parse_mode=ParseMode.MARKDOWN
            )

        # Clean up file
        os.remove(filepath)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during incremental export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
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

        await client.connect()

        # Export messages
        messages = []
        message_ids = []

        async for message in client.iter_messages(selected_chat['id'], limit=limit):
            content = format_message_content(message)
            if content:
                sender = get_sender_name(message)
                timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender}: {content}")
                message_ids.append(message.id)

        await client.disconnect()

        if not messages:
            await update.message.reply_text("❌ Сообщения в этом чате не найдены")
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Тип экспорта: Полный экспорт\n")
            f.write(f"Всего сообщений: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        caption = f"✅ Полный экспорт *{selected_chat['name']}* - {len(messages)} сообщений"

        # Send file
        with open(filepath, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )

        # Clean up file
        os.remove(filepath)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка экспорта: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass


async def export_do_export_with_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int):
    """Perform export with a preset limit (called from callback buttons)."""
    user_id = update.effective_user.id
    transcribe = context.user_data.get('transcribe_voice', False)

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

        await client.connect()

        # Export messages
        messages = []
        message_ids = []
        voice_count = 0
        transcribed_count = 0

        async for message in client.iter_messages(selected_chat['id'], limit=limit):
            transcription = None

            # Transcribe voice messages if enabled
            if transcribe and is_voice_message(message):
                voice_count += 1
                transcription = await transcribe_voice(client, message)
                if transcription:
                    transcribed_count += 1

            content = format_message_content(message, transcription)
            if content:
                sender = get_sender_name(message)
                timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender}: {content}")
                message_ids.append(message.id)

        await client.disconnect()

        if not messages:
            await update.effective_chat.send_message("❌ Сообщения в этом чате не найдены")
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Тип экспорта: Полный экспорт\n")
            if transcribe:
                f.write(f"Транскрипция голосовых: {transcribed_count}/{voice_count} транскрибировано\n")
            f.write(f"Всего сообщений: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        caption = f"✅ Полный экспорт *{selected_chat['name']}* - {len(messages)} сообщений"
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

        # Clean up file
        os.remove(filepath)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
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
        index = int(query.data.split('_')[2])
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
                keyboard.append([InlineKeyboardButton("🎤 Все + транскрипция", callback_data=f"search_export_mode_transcribe_{index}")])
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
            await query.edit_message_text(
                f"📊 Выбран: *{selected_chat['name']}*\n\n"
                "Сколько сообщений экспортировать?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting_search_export_limit'] = True

    except Exception as e:
        logger.error(f"Error in search_export_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


async def search_export_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle export mode selection (incremental vs full)."""
    query = update.callback_query
    await query.answer()

    try:
        callback_data = query.data

        if callback_data.startswith("search_export_mode_incremental_"):
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

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.callback_query.edit_message_text("❌ Выбор чата потерян. Попробуй снова.")
            return

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get last message id for incremental export
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.callback_query.edit_message_text("❌ Сессия не найдена")
            return

        await client.connect()

        # Export only new messages
        messages = []
        message_ids = []

        async for message in client.iter_messages(selected_chat['id'], min_id=last_message_id):
            content = format_message_content(message)
            if content:
                sender = get_sender_name(message)
                timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender}: {content}")
                message_ids.append(message.id)

        await client.disconnect()

        # Check if there are any new messages
        if not messages:
            await update.callback_query.edit_message_text(
                f"⚠️ Нет новых сообщений в *{selected_chat['name']}* с последнего экспорта.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Тип экспорта: Инкрементальный (только новые сообщения)\n")
            f.write(f"Всего сообщений: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        # Send file
        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=f"✅ Экспортировано {len(messages)} новых сообщений из *{selected_chat['name']}* (с последнего экспорта)",
                parse_mode=ParseMode.MARKDOWN
            )

        # Clean up file
        os.remove(filepath)

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
        try:
            await client.disconnect()
        except:
            pass


async def search_export_with_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int):
    """Perform search export with a preset limit (called from callback buttons)."""
    user_id = update.effective_user.id
    transcribe = context.user_data.get('transcribe_voice', False)

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

        await client.connect()

        # Export messages
        messages = []
        message_ids = []
        voice_count = 0
        transcribed_count = 0

        async for message in client.iter_messages(selected_chat['id'], limit=limit):
            transcription = None

            # Transcribe voice messages if enabled
            if transcribe and is_voice_message(message):
                voice_count += 1
                transcription = await transcribe_voice(client, message)
                if transcription:
                    transcribed_count += 1

            content = format_message_content(message, transcription)
            if content:
                sender = get_sender_name(message)
                timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender}: {content}")
                message_ids.append(message.id)

        await client.disconnect()

        if not messages:
            await update.effective_chat.send_message("❌ Сообщения в этом чате не найдены")
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Чат: {selected_chat['name']}\n")
            f.write(f"Дата экспорта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Тип экспорта: Полный экспорт\n")
            if transcribe:
                f.write(f"Транскрипция голосовых: {transcribed_count}/{voice_count} транскрибировано\n")
            f.write(f"Всего сообщений: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        caption = f"✅ Полный экспорт *{selected_chat['name']}* - {len(messages)} сообщений"
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

        # Clean up file
        os.remove(filepath)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

    except Exception as e:
        logger.error(f"Error during search export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Ошибка экспорта: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass


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

    # Create application
    application = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("logout", logout_command))

    # Export command handler
    application.add_handler(CommandHandler("export", export_start))

    # Export pagination callback handler
    application.add_handler(CallbackQueryHandler(export_page_callback, pattern="^export_page_"))

    # Export chat selection callback handler
    application.add_handler(CallbackQueryHandler(export_chat_callback, pattern="^export_chat_"))

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
