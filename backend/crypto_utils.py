"""
Encryption utilities using Fernet symmetric encryption.
Shared between backend and bot for encrypting/decrypting session strings.
"""
import os
from cryptography.fernet import Fernet


def _get_cipher():
    """Get cipher instance from environment variable."""
    encryption_key = os.environ["ENCRYPTION_KEY"].encode()
    return Fernet(encryption_key)


def encrypt(value: str) -> str:
    """
    Encrypt a string value using Fernet encryption.

    Args:
        value: Plain text string to encrypt

    Returns:
        Encrypted string (base64 encoded)
    """
    cipher = _get_cipher()
    return cipher.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """
    Decrypt a Fernet-encrypted string.

    Args:
        value: Encrypted string (base64 encoded)

    Returns:
        Decrypted plain text string
    """
    cipher = _get_cipher()
    return cipher.decrypt(value.encode()).decode()
