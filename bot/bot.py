"""
Telegram Bot for exporting chat history.
Uses Telethon sessions stored in database after WebApp authentication.
"""
import os
import logging
from datetime import datetime
from typing import Optional

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
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

# Conversation states
SELECTING_CHAT, ENTERING_LIMIT = range(2)


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
        InlineKeyboardButton("🔐 Login via WebApp", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]

    await update.message.reply_text(
        "👋 Welcome to Telegram Chat Export Bot!\n\n"
        "I can help you export your Telegram chat history to text files.\n\n"
        "To get started:\n"
        "1️⃣ Click the button below to authenticate\n"
        "2️⃣ Use /list to see your chats\n"
        "3️⃣ Use /export to export a chat\n\n"
        "Type /help for more information.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await update.message.reply_text(
        "📖 *Available Commands:*\n\n"
        "/start - Start the bot\n"
        "/login - Authenticate via WebApp\n"
        "/status - Check your authentication status\n"
        "/list - List your chats/channels (with pagination)\n"
        "/search - Search for chats by name\n"
        "/export - Export chat history to file\n"
        "/logout - Delete your session data\n"
        "/help - Show this help message\n\n"
        "*How to use:*\n"
        "1. Click /login and authenticate through the web page\n"
        "2. View your chats with /list (navigate with Previous/Next buttons)\n"
        "3. Or search for a specific chat: /search Python\n"
        "4. Export any chat with /export\n\n"
        "⚠️ *Important:* All authentication happens through the web interface. "
        "I will never ask for codes or passwords in this chat.",
        parse_mode=ParseMode.MARKDOWN
    )


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /login command - opens WebApp."""
    keyboard = [[
        InlineKeyboardButton("🔐 Login via WebApp", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]

    await update.message.reply_text(
        "🔐 *Authentication*\n\n"
        "Click the button below to open the authentication page.\n\n"
        "📝 *Steps:*\n"
        "1️⃣ Enter your phone number\n"
        "2️⃣ Enter the confirmation code\n"
        "3️⃣ Enter 2FA password (if enabled)\n\n"
        "⚠️ All sensitive data is entered in the web page, not in this chat.",
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
            "❌ *Not authenticated*\n\n"
            "You haven't logged in yet. Use /login to authenticate.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if is_authenticated:
        await update.message.reply_text(
            "✅ *Authenticated*\n\n"
            "You are logged in and can use /list and /export commands.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "⚠️ *Session exists but not authenticated*\n\n"
            "Please try logging in again with /login",
            parse_mode=ParseMode.MARKDOWN
        )


CHATS_PER_PAGE = 10


async def show_chats_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show paginated chat list."""
    dialogs = context.user_data.get('all_dialogs', [])
    total_pages = (len(dialogs) + CHATS_PER_PAGE - 1) // CHATS_PER_PAGE

    if page < 0 or page >= total_pages:
        return

    start_idx = page * CHATS_PER_PAGE
    end_idx = start_idx + CHATS_PER_PAGE
    page_dialogs = dialogs[start_idx:end_idx]

    # Format chat list for this page
    chat_list = [f"*Your Chats (Page {page + 1}/{total_pages}):*\n"]
    for i, dialog in enumerate(page_dialogs, start_idx + 1):
        chat_name = dialog.name
        chat_type = "👤" if dialog.is_user else "👥" if dialog.is_group else "📢"
        chat_list.append(f"{i}. {chat_type} {chat_name}")

    chat_text = "\n".join(chat_list)
    chat_text += "\n\nUse /export to export a chat.\nUse /search to find a chat by name."

    # Create navigation buttons
    keyboard = []
    buttons_row = []
    if page > 0:
        buttons_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"list_page_{page - 1}"))
    if page < total_pages - 1:
        buttons_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_page_{page + 1}"))
    if buttons_row:
        keyboard.append(buttons_row)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            chat_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    else:
        await update.message.reply_text(
            chat_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command - show user's chats with pagination."""
    user_id = update.effective_user.id

    if not db.is_user_authenticated(user_id):
        await update.message.reply_text(
            "❌ You need to authenticate first. Use /login"
        )
        return

    client = get_user_client(user_id)
    if not client:
        await update.message.reply_text(
            "❌ Session not found. Please use /login to authenticate."
        )
        return

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await update.message.reply_text(
                "❌ Session expired. Please use /login to re-authenticate."
            )
            await client.disconnect()
            return

        # Get user's dialogs (chats)
        await update.message.reply_text("📋 Fetching your chats...")

        dialogs = await client.get_dialogs(limit=100)

        if not dialogs:
            await update.message.reply_text("No chats found.")
            await client.disconnect()
            return

        # Store dialogs in context for pagination
        context.user_data['all_dialogs'] = dialogs
        context.user_data['current_page'] = 0

        await client.disconnect()

        # Show first page
        await show_chats_page(update, context, 0)

    except FloodWaitError as e:
        await update.message.reply_text(
            f"⏳ Rate limit reached. Please wait {e.seconds} seconds and try again."
        )
        await client.disconnect()

    except Exception as e:
        logger.error(f"Error listing chats: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"❌ Error fetching chats: {str(e)}"
        )
        try:
            await client.disconnect()
        except:
            pass


async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination button clicks."""
    query = update.callback_query
    await query.answer()

    # Extract page number from callback data
    page = int(query.data.split('_')[2])
    context.user_data['current_page'] = page

    await show_chats_page(update, context, page)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command - search for chats by name."""
    user_id = update.effective_user.id
    search_query = ' '.join(context.args).lower() if context.args else ""

    if not search_query:
        await update.message.reply_text(
            "📝 *Usage:* /search <chat name>\n\n"
            "Example: /search Python\n"
            "Example: /search John",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if not db.is_user_authenticated(user_id):
        await update.message.reply_text(
            "❌ You need to authenticate first. Use /login"
        )
        return

    client = get_user_client(user_id)
    if not client:
        await update.message.reply_text(
            "❌ Session not found. Please use /login to authenticate."
        )
        return

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await update.message.reply_text(
                "❌ Session expired. Please use /login to re-authenticate."
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
                f"❌ No chats found matching '{search_query}'"
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
        chat_list = [f"*Search Results for '{search_query}':* ({len(results)} found)\n"]
        for i, dialog in enumerate(results_to_show, 1):
            chat_type = "👤" if dialog.is_user else "👥" if dialog.is_group else "📢"
            chat_list.append(f"{i}. {chat_type} {dialog.name}")

        if len(results) > 10:
            chat_list.append(f"\n... and {len(results) - 10} more results")

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
            f"❌ Error searching chats: {str(e)}"
        )
        try:
            await client.disconnect()
        except:
            pass


async def export_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start export conversation."""
    user_id = update.effective_user.id

    if not db.is_user_authenticated(user_id):
        await update.message.reply_text(
            "❌ You need to authenticate first. Use /login"
        )
        return ConversationHandler.END

    client = get_user_client(user_id)
    if not client:
        await update.message.reply_text(
            "❌ Session not found. Please use /login to authenticate."
        )
        return ConversationHandler.END

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await update.message.reply_text(
                "❌ Session expired. Please use /login to re-authenticate."
            )
            await client.disconnect()
            return ConversationHandler.END

        # Get dialogs
        dialogs = await client.get_dialogs(limit=50)
        await client.disconnect()

        if not dialogs:
            await update.message.reply_text("No chats found.")
            return ConversationHandler.END

        # Store dialogs in context
        context.user_data['dialogs'] = []
        for dialog in dialogs:
            chat_id, chat_type = get_chat_identity(dialog)
            context.user_data['dialogs'].append({
                'id': dialog.id,
                'name': dialog.name,
                'is_user': dialog.is_user,
                'is_group': dialog.is_group,
                'is_channel': dialog.is_channel,
                'chat_id': chat_id,
                'chat_type': chat_type
            })

        # Show chat list
        chat_list = ["*Select a chat to export:*\n"]
        for i, dialog in enumerate(dialogs[:30], 1):
            chat_type = "👤" if dialog.is_user else "👥" if dialog.is_group else "📢"
            chat_list.append(f"{i}. {chat_type} {dialog.name}")

        chat_text = "\n".join(chat_list)
        chat_text += "\n\nReply with the chat number, or /cancel to cancel."

        await update.message.reply_text(chat_text, parse_mode=ParseMode.MARKDOWN)
        return SELECTING_CHAT

    except Exception as e:
        logger.error(f"Error starting export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass
        return ConversationHandler.END


async def export_select_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle chat selection."""
    user_id = update.effective_user.id

    try:
        chat_num = int(update.message.text)
        dialogs = context.user_data.get('dialogs', [])

        if chat_num < 1 or chat_num > len(dialogs):
            await update.message.reply_text(
                f"❌ Invalid number. Please enter a number between 1 and {len(dialogs)}"
            )
            return SELECTING_CHAT

        selected_chat = dialogs[chat_num - 1]
        context.user_data['selected_chat'] = selected_chat
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Check if this chat was previously exported
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        if last_message_id:
            # Chat was already exported - show options
            keyboard = [
                [InlineKeyboardButton("📥 Only new messages", callback_data="export_mode_incremental")],
                [InlineKeyboardButton("🔄 Export all again", callback_data="export_mode_full")],
                [InlineKeyboardButton("⬇️ Export all (10000)", callback_data="export_mode_all_max")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.append([InlineKeyboardButton("🎤 Export all + transcribe voice", callback_data="export_mode_all_max_transcribe")])
            await update.message.reply_text(
                f"📊 Selected: *{selected_chat['name']}*\n\n"
                "This chat was previously exported. Choose an option:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return ENTERING_LIMIT
        else:
            # First export - show options with quick button
            keyboard = [
                [InlineKeyboardButton("⬇️ Export all (10000)", callback_data="export_mode_all_max")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.append([InlineKeyboardButton("🎤 Export all + transcribe voice", callback_data="export_mode_all_max_transcribe")])
            keyboard.append([InlineKeyboardButton("⚙️ Custom amount", callback_data="export_mode_custom")])
            await update.message.reply_text(
                f"📊 Selected: *{selected_chat['name']}*\n\n"
                "How many messages to export?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return ENTERING_LIMIT

    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")
        return SELECTING_CHAT


async def export_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle export mode selection for /export command (incremental vs full)."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    try:
        callback_data = query.data

        if callback_data == "export_mode_incremental":
            # User chose "only new messages"
            context.user_data['export_mode'] = 'incremental'
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Exporting new messages from *{selected_chat['name']}*...\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Trigger the export immediately without waiting for user input
            await export_do_incremental(update, context)
            return ConversationHandler.END

        elif callback_data == "export_mode_full":
            # User chose "export all again"
            context.user_data['export_mode'] = 'full'
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"📊 Selected: *{selected_chat['name']}*\n\n"
                "How many messages to export? (Default: 1000, Max: 10000)\n"
                "Reply with a number or /cancel",
                parse_mode=ParseMode.MARKDOWN
            )
            return ENTERING_LIMIT

        elif callback_data == "export_mode_all_max":
            # User chose "export all (10000)"
            context.user_data['export_mode'] = 'full'
            context.user_data['export_limit'] = 10000
            context.user_data['transcribe_voice'] = False
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Exporting all messages from *{selected_chat['name']}* (up to 10000)...\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Export with preset limit
            await export_do_export_with_limit(update, context, 10000)
            return ConversationHandler.END

        elif callback_data == "export_mode_all_max_transcribe":
            # User chose "export all (10000) + transcribe voice"
            context.user_data['export_mode'] = 'full'
            context.user_data['export_limit'] = 10000
            context.user_data['transcribe_voice'] = True
            selected_chat = context.user_data.get('selected_chat')
            await query.edit_message_text(
                f"⏳ Exporting all messages from *{selected_chat['name']}* (up to 10000)...\n"
                "🎤 Voice messages will be transcribed.\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Export with preset limit and transcription
            await export_do_export_with_limit(update, context, 10000)
            return ConversationHandler.END

        elif callback_data == "export_mode_custom":
            # User chose "custom amount"
            await query.edit_message_text(
                "How many messages to export? (Default: 1000, Max: 10000)\n"
                "Reply with a number or /cancel"
            )
            return ENTERING_LIMIT

    except Exception as e:
        logger.error(f"Error in export_mode_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Error: {str(e)}")
        return ConversationHandler.END


async def export_do_incremental(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform incremental export from /export command (new messages only)."""
    user_id = update.effective_user.id

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            if update.callback_query:
                await update.callback_query.edit_message_text("❌ Chat selection lost. Please search again.")
            else:
                await update.message.reply_text("❌ Chat selection lost. Please search again.")
            return ConversationHandler.END

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get last message id for incremental export
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Session not found")
            return ConversationHandler.END

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
                f"⚠️ No new messages in *{selected_chat['name']}* since last export.",
                parse_mode=ParseMode.MARKDOWN
            )
            return ConversationHandler.END

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Chat: {selected_chat['name']}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Export type: Incremental (new messages only)\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        # Send file
        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=f"✅ Exported {len(messages)} new messages from *{selected_chat['name']}* (since last export)",
                parse_mode=ParseMode.MARKDOWN
            )

        # Clean up file
        os.remove(filepath)

        # Save progress
        if message_ids:
            new_last_message_id = max(message_ids)
            db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
            logger.info(f"Updated chat progress for user {user_id}, chat {chat_id}: last_message_id={new_last_message_id}")

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error during incremental export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Export failed: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass
        return ConversationHandler.END


async def export_do_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform the actual export (with incremental support)."""
    user_id = update.effective_user.id

    try:
        # Parse limit (ignored for incremental exports)
        limit = 1000
        if update.message.text.isdigit():
            limit = min(int(update.message.text), 10000)  # Max 10k messages

        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.message.reply_text("❌ Chat selection lost. Please start over with /export")
            return ConversationHandler.END

        # Get chat identity for progress tracking
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Check for existing progress and user's choice
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)
        export_mode = context.user_data.get('export_mode', 'full')
        is_incremental = (last_message_id is not None and export_mode == 'incremental')

        if is_incremental:
            await update.message.reply_text(
                f"⏳ Exporting *new* messages from *{selected_chat['name']}* since last export...\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                f"⏳ Exporting up to {limit} messages from *{selected_chat['name']}*...\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.message.reply_text("❌ Session not found")
            return ConversationHandler.END

        await client.connect()

        # Export messages
        messages = []
        message_ids = []

        if is_incremental:
            # Incremental export: get only messages newer than last_message_id
            async for message in client.iter_messages(selected_chat['id'], min_id=last_message_id):
                content = format_message_content(message)
                if content:
                    sender = get_sender_name(message)
                    timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {sender}: {content}")
                    message_ids.append(message.id)
        else:
            # First export: get up to limit messages
            async for message in client.iter_messages(selected_chat['id'], limit=limit):
                content = format_message_content(message)
                if content:
                    sender = get_sender_name(message)
                    timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {sender}: {content}")
                    message_ids.append(message.id)

        await client.disconnect()

        # Check if there are any new messages
        if not messages:
            if is_incremental:
                await update.message.reply_text(
                    f"⚠️ No new messages in *{selected_chat['name']}* since last export.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text("❌ No messages found in this chat")
            return ConversationHandler.END

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))  # Sanitize

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Chat: {selected_chat['name']}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if is_incremental:
                f.write(f"Export type: Incremental (new messages only)\n")
            else:
                f.write(f"Export type: Full export\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        # Prepare caption
        if is_incremental:
            caption = f"✅ Exported {len(messages)} new messages from *{selected_chat['name']}* (since last export)"
        else:
            caption = f"✅ Full export of *{selected_chat['name']}* - {len(messages)} messages"

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

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error during export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Export failed: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass
        return ConversationHandler.END


async def export_do_export_with_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int):
    """Perform export with a preset limit (called from callback buttons)."""
    user_id = update.effective_user.id
    transcribe = context.user_data.get('transcribe_voice', False)

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.effective_chat.send_message("❌ Chat selection lost. Please start over with /export")
            return ConversationHandler.END

        # Get chat identity for progress tracking
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Session not found")
            return ConversationHandler.END

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
            await update.effective_chat.send_message("❌ No messages found in this chat")
            return ConversationHandler.END

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Chat: {selected_chat['name']}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Export type: Full export\n")
            if transcribe:
                f.write(f"Voice transcription: {transcribed_count}/{voice_count} transcribed\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        caption = f"✅ Full export of *{selected_chat['name']}* - {len(messages)} messages"
        if transcribe and voice_count > 0:
            caption += f"\n🎤 Transcribed {transcribed_count}/{voice_count} voice messages"

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

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error during export: {str(e)}", exc_info=True)
        await update.effective_chat.send_message(f"❌ Export failed: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass
        return ConversationHandler.END


async def export_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel export conversation."""
    await update.message.reply_text("Export cancelled.")
    return ConversationHandler.END


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
            await query.edit_message_text("❌ Chat not found")
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
                [InlineKeyboardButton("📥 Only new messages", callback_data=f"search_export_mode_incremental_{index}")],
                [InlineKeyboardButton("🔄 Export all again", callback_data=f"search_export_mode_full_{index}")],
                [InlineKeyboardButton("⬇️ Export all (10000)", callback_data=f"search_export_mode_all_max_{index}")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.append([InlineKeyboardButton("🎤 Export all + transcribe", callback_data=f"search_export_mode_transcribe_{index}")])
            await query.edit_message_text(
                f"📊 Selected: *{selected_chat['name']}*\n\n"
                "This chat was previously exported. Choose an option:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # First export - show options with quick button
            keyboard = [
                [InlineKeyboardButton("⬇️ Export all (10000)", callback_data=f"search_export_mode_all_max_{index}")]
            ]
            if TRANSCRIPTION_AVAILABLE:
                keyboard.append([InlineKeyboardButton("🎤 Export all + transcribe", callback_data=f"search_export_mode_transcribe_{index}")])
            keyboard.append([InlineKeyboardButton("⚙️ Custom amount", callback_data=f"search_export_mode_custom_{index}")])
            await query.edit_message_text(
                f"📊 Selected: *{selected_chat['name']}*\n\n"
                "How many messages to export?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting_search_export_limit'] = True

    except Exception as e:
        logger.error(f"Error in search_export_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Error: {str(e)}")


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
                f"⏳ Exporting new messages from *{selected_chat['name']}*...\n"
                "This may take a while.",
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
                f"📊 Selected: *{selected_chat['name']}*\n\n"
                "How many messages to export? (Default: 1000, Max: 10000)\n"
                "Reply with a number or /cancel",
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
                f"⏳ Exporting all messages from *{selected_chat['name']}* (up to 10000)...\n"
                "This may take a while.",
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
                f"⏳ Exporting all messages from *{selected_chat['name']}* (up to 10000)...\n"
                "🎤 Voice messages will be transcribed.\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Export with preset limit and transcription
            await search_export_with_limit(update, context, 10000)

        elif callback_data.startswith("search_export_mode_custom_"):
            # User chose "custom amount"
            context.user_data['awaiting_search_export_limit'] = True
            await query.edit_message_text(
                "How many messages to export? (Default: 1000, Max: 10000)\n"
                "Reply with a number or /cancel"
            )

    except Exception as e:
        logger.error(f"Error in search_export_mode_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def search_export_do_incremental(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform incremental export (new messages only)."""
    user_id = update.effective_user.id

    try:
        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.callback_query.edit_message_text("❌ Chat selection lost. Please search again.")
            return

        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get last message id for incremental export
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.callback_query.edit_message_text("❌ Session not found")
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
                f"⚠️ No new messages in *{selected_chat['name']}* since last export.",
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
            f.write(f"Chat: {selected_chat['name']}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Export type: Incremental (new messages only)\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        # Send file
        with open(filepath, 'rb') as f:
            await update.effective_chat.send_document(
                document=f,
                filename=filename,
                caption=f"✅ Exported {len(messages)} new messages from *{selected_chat['name']}* (since last export)",
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
            await update.effective_chat.send_message(f"❌ Export failed: {str(e)}")
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
            await update.effective_chat.send_message("❌ Chat selection lost. Please search again.")
            return

        # Get chat identity for progress tracking
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.effective_chat.send_message("❌ Session not found")
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
            await update.effective_chat.send_message("❌ No messages found in this chat")
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Chat: {selected_chat['name']}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Export type: Full export\n")
            if transcribe:
                f.write(f"Voice transcription: {transcribed_count}/{voice_count} transcribed\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        caption = f"✅ Full export of *{selected_chat['name']}* - {len(messages)} messages"
        if transcribe and voice_count > 0:
            caption += f"\n🎤 Transcribed {transcribed_count}/{voice_count} voice messages"

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
        await update.effective_chat.send_message(f"❌ Export failed: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass


async def search_export_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle message limit input for search export."""
    # Check if we're awaiting a search export limit
    if not context.user_data.get('awaiting_search_export_limit'):
        return

    context.user_data['awaiting_search_export_limit'] = False

    # Reuse the export_do_export logic
    user_id = update.effective_user.id

    try:
        # Parse limit
        limit = 1000
        if update.message.text.isdigit():
            limit = min(int(update.message.text), 10000)  # Max 10k messages

        selected_chat = context.user_data.get('selected_chat')
        if not selected_chat:
            await update.message.reply_text("❌ Chat selection lost. Please search again.")
            return

        # Get chat identity for progress tracking
        chat_id = selected_chat['chat_id']
        chat_type = selected_chat['chat_type']

        # Check for existing progress and user's choice
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)
        export_mode = context.user_data.get('export_mode', 'full')
        is_incremental = (last_message_id is not None and export_mode == 'incremental')

        if is_incremental:
            await update.message.reply_text(
                f"⏳ Exporting *new* messages from *{selected_chat['name']}* since last export...\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                f"⏳ Exporting up to {limit} messages from *{selected_chat['name']}*...\n"
                "This may take a while.",
                parse_mode=ParseMode.MARKDOWN
            )

        # Get client
        client = get_user_client(user_id)
        if not client:
            await update.message.reply_text("❌ Session not found")
            return

        await client.connect()

        # Export messages
        messages = []
        message_ids = []

        if is_incremental:
            # Incremental export: get only messages newer than last_message_id
            async for message in client.iter_messages(selected_chat['id'], min_id=last_message_id):
                content = format_message_content(message)
                if content:
                    sender = get_sender_name(message)
                    timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {sender}: {content}")
                    message_ids.append(message.id)
        else:
            # First export: get up to limit messages
            async for message in client.iter_messages(selected_chat['id'], limit=limit):
                content = format_message_content(message)
                if content:
                    sender = get_sender_name(message)
                    timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {sender}: {content}")
                    message_ids.append(message.id)

        await client.disconnect()

        # Check if there are any new messages
        if not messages:
            if is_incremental:
                await update.message.reply_text(
                    f"⚠️ No new messages in *{selected_chat['name']}* since last export.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text("❌ No messages found in this chat")
            return

        # Reverse to chronological order
        messages.reverse()

        # Create file
        filename = f"export_{selected_chat['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))  # Sanitize

        filepath = f"/tmp/{filename}"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Chat: {selected_chat['name']}\n")
            f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if is_incremental:
                f.write(f"Export type: Incremental (new messages only)\n")
            else:
                f.write(f"Export type: Full export\n")
            f.write(f"Total messages: {len(messages)}\n")
            f.write("=" * 80 + "\n\n")
            f.write("\n".join(messages))

        # Prepare caption
        if is_incremental:
            caption = f"✅ Exported {len(messages)} new messages from *{selected_chat['name']}* (since last export)"
        else:
            caption = f"✅ Full export of *{selected_chat['name']}* - {len(messages)} messages"

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
        logger.error(f"Error during search export: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Export failed: {str(e)}")
        try:
            await client.disconnect()
        except:
            pass


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logout command with confirmation."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, logout", callback_data="logout_yes"),
            InlineKeyboardButton("❌ No, keep session", callback_data="logout_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚠️ *Logout Confirmation*\n\n"
        "Are you sure you want to delete your session?\n"
        "You'll need to authenticate again with /login",
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
            "✅ Session deleted successfully.\n\n"
            "Use /login to authenticate again."
        )
    else:
        await query.edit_message_text("❌ Logout cancelled. Your session is still active.")


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
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("logout", logout_command))

    # Pagination callback handler
    application.add_handler(CallbackQueryHandler(list_page_callback, pattern="^list_page_"))

    # Search export callback handler
    application.add_handler(CallbackQueryHandler(search_export_callback, pattern="^search_export_[0-9]+$"))

    # Search export mode callback handler (for incremental vs full choice)
    application.add_handler(CallbackQueryHandler(search_export_mode_callback, pattern="^search_export_mode_"))

    # Export mode callback handler (for /export command - incremental vs full)
    application.add_handler(CallbackQueryHandler(export_mode_callback, pattern="^export_mode_"))

    # Export conversation handler (MUST be before search_export_limit to take priority)
    export_conv = ConversationHandler(
        entry_points=[CommandHandler("export", export_start)],
        states={
            SELECTING_CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, export_select_chat)
            ],
            ENTERING_LIMIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, export_do_export)
            ],
        },
        fallbacks=[CommandHandler("cancel", export_cancel)],
    )
    application.add_handler(export_conv)

    # Search export limit handler (listen for message responses - AFTER export_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_export_limit))

    # Logout callback handler
    application.add_handler(CallbackQueryHandler(logout_callback, pattern="^logout_"))

    # Start bot
    logger.info("Bot started successfully")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
