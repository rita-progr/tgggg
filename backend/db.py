"""
Database layer for backend.
Handles users table and pending_logins table using SQLAlchemy.
"""
import os
import time
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from backend.crypto_utils import encrypt, decrypt


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
    Stores authenticated users with their encrypted session strings.
    """
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, index=True)
    session_string = Column(Text, nullable=False)  # Encrypted with Fernet
    is_authenticated = Column(Boolean, default=False)
    last_activity = Column(Integer, default=lambda: int(time.time()))


class PendingLogin(Base):
    """
    Temporary storage for users in the middle of authentication flow.
    """
    __tablename__ = "pending_logins"

    user_id = Column(Integer, primary_key=True, index=True)
    phone = Column(Text, nullable=False)
    phone_code_hash = Column(Text, nullable=False)
    temp_session_string = Column(Text, nullable=False)  # Encrypted with Fernet
    created_at = Column(Integer, default=lambda: int(time.time()))


class ChatProgress(Base):
    """
    Tracks export progress for each user-chat pair.
    Enables incremental exports by remembering the last exported message.
    """
    __tablename__ = "chat_progress"

    user_id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, primary_key=True, index=True)
    chat_type = Column(Text, primary_key=True)  # 'user', 'chat', or 'channel'
    last_message_id = Column(Integer, nullable=False)
    updated_at = Column(Integer, default=lambda: int(time.time()))


# Create tables
Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user(user_id: int) -> Optional[User]:
    """
    Get user by telegram user_id.

    Args:
        user_id: Telegram user ID

    Returns:
        User object or None if not found
    """
    db = SessionLocal()
    try:
        return db.query(User).filter(User.user_id == user_id).first()
    finally:
        db.close()


def save_session_string(user_id: int, session_string: str):
    """
    Save encrypted session string for a user.

    Args:
        user_id: Telegram user ID
        session_string: Plain text Telethon session string (will be encrypted)
    """
    db = SessionLocal()
    try:
        encrypted_session = encrypt(session_string)
        user = db.query(User).filter(User.user_id == user_id).first()

        if user:
            user.session_string = encrypted_session
            user.last_activity = int(time.time())
        else:
            user = User(
                user_id=user_id,
                session_string=encrypted_session,
                is_authenticated=False,
                last_activity=int(time.time())
            )
            db.add(user)

        db.commit()
    finally:
        db.close()


def set_authenticated(user_id: int, value: bool):
    """
    Set authentication status for a user.

    Args:
        user_id: Telegram user ID
        value: True if authenticated, False otherwise
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.is_authenticated = value
            user.last_activity = int(time.time())
            db.commit()
    finally:
        db.close()


def create_or_update_pending_login(user_id: int, phone: str, phone_code_hash: str, temp_session_string: str):
    """
    Create or update pending login record.

    Args:
        user_id: Telegram user ID
        phone: Phone number
        phone_code_hash: Hash from Telegram send_code_request
        temp_session_string: Temporary session string (will be encrypted)
    """
    db = SessionLocal()
    try:
        encrypted_session = encrypt(temp_session_string)
        pending = db.query(PendingLogin).filter(PendingLogin.user_id == user_id).first()

        if pending:
            pending.phone = phone
            pending.phone_code_hash = phone_code_hash
            pending.temp_session_string = encrypted_session
            pending.created_at = int(time.time())
        else:
            pending = PendingLogin(
                user_id=user_id,
                phone=phone,
                phone_code_hash=phone_code_hash,
                temp_session_string=encrypted_session,
                created_at=int(time.time())
            )
            db.add(pending)

        db.commit()
    finally:
        db.close()


def get_pending_login(user_id: int) -> Optional[PendingLogin]:
    """
    Get pending login record for a user.

    Args:
        user_id: Telegram user ID

    Returns:
        PendingLogin object or None if not found
    """
    db = SessionLocal()
    try:
        return db.query(PendingLogin).filter(PendingLogin.user_id == user_id).first()
    finally:
        db.close()


def delete_pending_login(user_id: int):
    """
    Delete pending login record for a user.

    Args:
        user_id: Telegram user ID
    """
    db = SessionLocal()
    try:
        db.query(PendingLogin).filter(PendingLogin.user_id == user_id).delete()
        db.commit()
    finally:
        db.close()


def get_decrypted_session_string(user_id: int) -> Optional[str]:
    """
    Get decrypted session string for a user.

    Args:
        user_id: Telegram user ID

    Returns:
        Decrypted session string or None if not found
    """
    user = get_user(user_id)
    if user and user.session_string:
        return decrypt(user.session_string)
    return None


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
