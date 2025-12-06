"""
Telethon utilities for backend authentication flow.
Creates Telegram clients using StringSession.
"""
import os
from telethon import TelegramClient
from telethon.sessions import StringSession


API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]


def create_client_from_string(session_string: str | None = None) -> TelegramClient:
    """
    Create a TelegramClient with StringSession.

    Args:
        session_string: Optional session string. If None, creates new empty session.

    Returns:
        TelegramClient instance (not connected)
    """
    if session_string:
        session = StringSession(session_string)
    else:
        session = StringSession()

    return TelegramClient(session, API_ID, API_HASH)
