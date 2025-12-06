"""
Encryption utilities using Fernet symmetric encryption.
Shared between backend and bot for encrypting/decrypting session strings.
"""
import os
from cryptography.fernet import Fernet


# Read encryption key from environment variable
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"].encode()
cipher = Fernet(ENCRYPTION_KEY)


def encrypt(value: str) -> str:
    """
    Encrypt a string value using Fernet encryption.

    Args:
        value: Plain text string to encrypt

    Returns:
        Encrypted string (base64 encoded)
    """
    return cipher.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """
    Decrypt a Fernet-encrypted string.

    Args:
        value: Encrypted string (base64 encoded)

    Returns:
        Decrypted plain text string
    """
    return cipher.decrypt(value.encode()).decode()
