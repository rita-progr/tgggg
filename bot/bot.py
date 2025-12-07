"""
Telegram Bot for exporting chat history.
Uses Telethon sessions stored in database after WebApp authentication.
"""
import os
import logging
from datetime import datetime
from typing import Optional

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
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
from telethon.tl.types import User as TelethonUser

from bot import db


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


# Command handlers

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    keyboard = [[
        InlineKeyboardButton("🔐 Login via WebApp", url=WEBAPP_URL)
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
        "/list - List your chats/channels\n"
        "/export - Export chat history to file\n"
        "/logout - Delete your session data\n"
        "/help - Show this help message\n\n"
        "*How to use:*\n"
        "1. Click /login and authenticate through the web page\n"
        "2. View your chats with /list\n"
        "3. Export any chat with /export\n\n"
        "⚠️ *Important:* All authentication happens through the web interface. "
        "I will never ask for codes or passwords in this chat.",
        parse_mode=ParseMode.MARKDOWN
    )


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /login command - opens WebApp."""
    keyboard = [[
        InlineKeyboardButton("🔐 Login via WebApp", url=WEBAPP_URL)
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


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command - show user's chats."""
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

        dialogs = await client.get_dialogs(limit=50)

        if not dialogs:
            await update.message.reply_text("No chats found.")
            await client.disconnect()
            return

        # Format chat list
        chat_list = ["*Your Chats:*\n"]
        for i, dialog in enumerate(dialogs, 1):
            chat_name = dialog.name
            chat_type = "👤" if dialog.is_user else "👥" if dialog.is_group else "📢"
            chat_list.append(f"{i}. {chat_type} {chat_name}")

        chat_text = "\n".join(chat_list[:30])  # Limit to 30 chats to avoid message length issues
        if len(dialogs) > 30:
            chat_text += f"\n\n... and {len(dialogs) - 30} more chats"

        chat_text += "\n\nUse /export to export a chat."

        await update.message.reply_text(chat_text, parse_mode=ParseMode.MARKDOWN)
        await client.disconnect()

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
    try:
        chat_num = int(update.message.text)
        dialogs = context.user_data.get('dialogs', [])

        if chat_num < 1 or chat_num > len(dialogs):
            await update.message.reply_text(
                f"❌ Invalid number. Please enter a number between 1 and {len(dialogs)}"
            )
            return SELECTING_CHAT

        context.user_data['selected_chat'] = dialogs[chat_num - 1]

        await update.message.reply_text(
            f"📊 Selected: *{dialogs[chat_num - 1]['name']}*\n\n"
            "How many messages to export? (Default: 1000)\n"
            "Reply with a number or /cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return ENTERING_LIMIT

    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")
        return SELECTING_CHAT


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

        # Check for existing progress
        last_message_id = db.get_chat_progress(user_id, chat_id, chat_type)
        is_incremental = last_message_id is not None

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
                if message.text:
                    sender = "Unknown"
                    if message.sender:
                        if isinstance(message.sender, TelethonUser):
                            sender = f"{message.sender.first_name or ''} {message.sender.last_name or ''}".strip()
                            if not sender:
                                sender = f"User_{message.sender.id}"
                        else:
                            sender = getattr(message.sender, 'title', 'Unknown')

                    timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {sender}: {message.text}")
                    message_ids.append(message.id)
        else:
            # First export: get up to limit messages
            async for message in client.iter_messages(selected_chat['id'], limit=limit):
                if message.text:
                    sender = "Unknown"
                    if message.sender:
                        if isinstance(message.sender, TelethonUser):
                            sender = f"{message.sender.first_name or ''} {message.sender.last_name or ''}".strip()
                            if not sender:
                                sender = f"User_{message.sender.id}"
                        else:
                            sender = getattr(message.sender, 'title', 'Unknown')

                    timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {sender}: {message.text}")
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
                await update.message.reply_text("❌ No text messages found in this chat")
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


async def export_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel export conversation."""
    await update.message.reply_text("Export cancelled.")
    return ConversationHandler.END


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
    application.add_handler(CommandHandler("logout", logout_command))

    # Export conversation handler
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

    # Logout callback handler
    application.add_handler(CallbackQueryHandler(logout_callback, pattern="^logout_"))

    # Start bot
    logger.info("Bot started successfully")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
