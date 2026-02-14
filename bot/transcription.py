"""
Voice message transcription using Groq Whisper API.
"""
import os
import asyncio
import logging
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# Groq API key (optional)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Check if transcription is available
TRANSCRIPTION_AVAILABLE = bool(GROQ_API_KEY)


async def transcribe_voice(client, message) -> Optional[str]:
    """
    Download and transcribe a voice message using Groq Whisper API.

    Args:
        client: Telethon client (connected)
        message: Telethon message with voice/audio

    Returns:
        Transcribed text or None if failed
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set, skipping transcription")
        return None

    tmp_path = None
    try:
        from groq import Groq
        from telethon.errors import FloodWaitError

        # Download voice message to temp file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        # Download with retry logic for FloodWait
        for attempt in range(3):
            try:
                await client.download_media(message, tmp_path)
                await asyncio.sleep(0.3)  # 300ms delay after download
                break
            except FloodWaitError as e:
                if attempt == 2:  # Last attempt
                    logger.error(f"FloodWait on download after 3 attempts: {e.seconds}s")
                    raise
                logger.warning(f"FloodWait {e.seconds}s during media download, attempt {attempt + 1}/3")
                await asyncio.sleep(e.seconds)

        # Transcribe with Groq
        groq_client = Groq(api_key=GROQ_API_KEY)

        with open(tmp_path, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), audio_file.read()),
                model="whisper-large-v3",
                response_format="text",
            )

        # Return transcribed text
        text = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()

        if text:
            logger.info(f"Transcribed voice message: {len(text)} chars")
            return text
        else:
            return None

    except Exception as e:
        logger.error(f"Transcription failed: {str(e)}")
        return None
    finally:
        # Always clean up temp file
        if tmp_path:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception as e:
                logger.error(f"Failed to remove temp file {tmp_path}: {e}")


def is_voice_message(message) -> bool:
    """Check if message is a voice message or video message (round video)."""
    if not message.media:
        return False

    from telethon.tl.types import MessageMediaDocument

    if not isinstance(message.media, MessageMediaDocument):
        return False

    doc = message.media.document
    if not doc:
        return False

    # Check for voice attribute
    for attr in getattr(doc, 'attributes', []):
        if hasattr(attr, 'voice') and attr.voice:
            return True
        if hasattr(attr, 'round_message') and attr.round_message:
            return True

    return False
