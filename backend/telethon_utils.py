"""
Telethon utilities for backend authentication flow.
Creates Telegram clients using StringSession.
"""
import os
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession


def create_client_from_string(session_string: Optional[str] = None) -> TelegramClient:
    """
    Create a TelegramClient with StringSession.

    Args:
        session_string: Optional session string. If None, creates new empty session.

    Returns:
        TelegramClient instance (not connected)
    """
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]

    if session_string:
        session = StringSession(session_string)
    else:
        session = StringSession()

    return TelegramClient(session, api_id, api_hash)
