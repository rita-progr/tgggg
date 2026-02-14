"""
Database layer for bot.
Provides read-only access to user sessions and authentication status.
Shares the same database with backend.
"""
import os
import logging
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, BigInteger, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from bot.crypto_utils import decrypt
from cryptography.fernet import InvalidToken
import time

logger = logging.getLogger(__name__)


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/database.db")

# Ensure data directory exists
if DATABASE_URL.startswith("sqlite"):
    db_path = DATABASE_URL.replace("sqlite:///", "")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """
    User table - stores authenticated users with encrypted session strings.
    Each user has their own API credentials for isolation.
    """
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True, index=True)
    session_string = Column(Text, nullable=False)
    is_authenticated = Column(Boolean, default=False)
    last_activity = Column(Integer, default=lambda: int(time.time()))
    # User's own Telegram API credentials (encrypted)
    api_id = Column(Text, nullable=True)  # Encrypted TG_API_ID
    api_hash = Column(Text, nullable=True)  # Encrypted TG_API_HASH


class PendingLogin(Base):
    """
    Pending login table - stores temporary auth state.
    """
    __tablename__ = "pending_logins"

    user_id = Column(BigInteger, primary_key=True, index=True)
    phone = Column(Text, nullable=False)
    phone_code_hash = Column(Text, nullable=False)
    temp_session_string = Column(Text, nullable=False)
    created_at = Column(Integer, default=lambda: int(time.time()))


class ChatProgress(Base):
    """
    Tracks export progress for each user-chat pair.
    Enables incremental exports by remembering the last exported message.
    """
    __tablename__ = "chat_progress"

    user_id = Column(BigInteger, primary_key=True, index=True)
    chat_id = Column(BigInteger, primary_key=True, index=True)
    chat_type = Column(Text, primary_key=True)  # 'user', 'chat', or 'channel'
    last_message_id = Column(BigInteger, nullable=False)
    updated_at = Column(Integer, default=lambda: int(time.time()))


# Create tables if they don't exist
Base.metadata.create_all(bind=engine)


def get_session_string(user_id: int) -> Optional[str]:
    """
    Get decrypted Telethon session string for a user.

    Args:
        user_id: Telegram user ID

    Returns:
        Decrypted session string or None if not found
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user and user.session_string:
            try:
                return decrypt(user.session_string)
            except InvalidToken:
                logger.error(f"Failed to decrypt session for user {user_id} - invalid key or corrupted data")
                return None
        return None
    finally:
        db.close()


def get_encrypted_session_string(user_id: int) -> Optional[str]:
    """
    Get encrypted session string (for debugging purposes only).

    Args:
        user_id: Telegram user ID

    Returns:
        Encrypted session string or None if not found
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            return user.session_string
        return None
    finally:
        db.close()


def is_user_authenticated(user_id: int) -> bool:
    """
    Check if user is authenticated.

    Args:
        user_id: Telegram user ID

    Returns:
        True if user is authenticated, False otherwise
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        return user.is_authenticated if user else False
    finally:
        db.close()


def delete_user_data(user_id: int):
    """
    Delete all user data (session, pending login, and chat progress).

    Args:
        user_id: Telegram user ID
    """
    db = SessionLocal()
    try:
        # Delete from users table
        db.query(User).filter(User.user_id == user_id).delete()

        # Delete from pending_logins table
        db.query(PendingLogin).filter(PendingLogin.user_id == user_id).delete()

        # Delete from chat_progress table
        db.query(ChatProgress).filter(ChatProgress.user_id == user_id).delete()

        db.commit()
    finally:
        db.close()


def user_exists(user_id: int) -> bool:
    """
    Check if user exists in database.

    Args:
        user_id: Telegram user ID

    Returns:
        True if user exists, False otherwise
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        return user is not None
    finally:
        db.close()


def get_chat_progress(user_id: int, chat_id: int, chat_type: str) -> Optional[int]:
    """
    Get the last exported message ID for a specific user-chat pair.

    Args:
        user_id: Telegram user ID
        chat_id: Telegram chat/channel/user ID
        chat_type: Type of chat ('user', 'chat', or 'channel')

    Returns:
        Last message ID that was exported, or None if no progress exists
    """
    db = SessionLocal()
    try:
        progress = db.query(ChatProgress).filter(
            ChatProgress.user_id == user_id,
            ChatProgress.chat_id == chat_id,
            ChatProgress.chat_type == chat_type
        ).first()

        return progress.last_message_id if progress else None
    finally:
        db.close()


def upsert_chat_progress(user_id: int, chat_id: int, chat_type: str, last_message_id: int) -> None:
    """
    Create or update export progress for a user-chat pair.

    Args:
        user_id: Telegram user ID
        chat_id: Telegram chat/channel/user ID
        chat_type: Type of chat ('user', 'chat', or 'channel')
        last_message_id: ID of the last exported message
    """
    db = SessionLocal()
    try:
        progress = db.query(ChatProgress).filter(
            ChatProgress.user_id == user_id,
            ChatProgress.chat_id == chat_id,
            ChatProgress.chat_type == chat_type
        ).first()

        if progress:
            # Update existing record
            progress.last_message_id = last_message_id
            progress.updated_at = int(time.time())
        else:
            # Create new record
            progress = ChatProgress(
                user_id=user_id,
                chat_id=chat_id,
                chat_type=chat_type,
                last_message_id=last_message_id,
                updated_at=int(time.time())
            )
            db.add(progress)

        db.commit()
    finally:
        db.close()


def get_user_api_credentials(user_id: int) -> Optional[tuple[int, str]]:
    """
    Get user's own Telegram API credentials (decrypted).

    Args:
        user_id: Telegram user ID

    Returns:
        Tuple of (api_id, api_hash) or None if not set
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user and user.api_id and user.api_hash:
            try:
                api_id = int(decrypt(user.api_id))
                api_hash = decrypt(user.api_hash)
                return (api_id, api_hash)
            except (InvalidToken, ValueError) as e:
                logger.error(f"Failed to decrypt API credentials for user {user_id}: {e}")
                return None
        return None
    finally:
        db.close()


def has_user_api_credentials(user_id: int) -> bool:
    """
    Check if user has provided their own API credentials.

    Args:
        user_id: Telegram user ID

    Returns:
        True if user has API credentials, False otherwise
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        return user and user.api_id is not None and user.api_hash is not None
    finally:
        db.close()
