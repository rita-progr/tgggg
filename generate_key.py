#!/usr/bin/env python3
"""
Generate a new Fernet encryption key.
Run this script and copy the output to your .env file as ENCRYPTION_KEY.
"""
from cryptography.fernet import Fernet

if __name__ == "__main__":
    key = Fernet.generate_key()
    print("Generated encryption key:")
    print(key.decode())
    print("\nCopy this key to your .env file:")
    print(f"ENCRYPTION_KEY={key.decode()}")
