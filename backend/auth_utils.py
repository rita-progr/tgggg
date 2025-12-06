"""
Telegram WebApp authentication utilities.
Validates initData signature from Telegram WebApp.
"""
import json
import hmac
import hashlib
from urllib.parse import parse_qs
from typing import Dict


def check_telegram_auth(init_data: str, bot_token: str) -> int:
    """
    Verify Telegram WebApp initData and extract user_id.

    According to Telegram WebApp docs:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Args:
        init_data: The initData string from Telegram.WebApp.initData
        bot_token: Bot token from BotFather

    Returns:
        user_id: Telegram user ID (integer)

    Raises:
        ValueError: If signature is invalid or data is malformed
    """
    try:
        # Parse init_data as URL query string
        parsed = parse_qs(init_data)

        # Extract hash from init_data
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            raise ValueError("Missing hash in initData")

        # Remove hash from parsed data
        data_check_dict = {k: v[0] for k, v in parsed.items() if k != "hash"}

        # Create data_check_string: sort keys alphabetically and join with newlines
        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(data_check_dict.items())
        )

        # Calculate expected hash
        # secret_key = HMAC_SHA256(bot_token, "WebAppData")
        secret_key = hmac.new(
            key="WebAppData".encode(),
            msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()

        # hash = HMAC_SHA256(secret_key, data_check_string)
        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()

        # Compare hashes
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("Invalid hash signature")

        # Extract user data
        user_json = data_check_dict.get("user")
        if not user_json:
            raise ValueError("Missing user data in initData")

        user_data = json.loads(user_json)
        user_id = user_data.get("id")

        if not user_id:
            raise ValueError("Missing user id in user data")

        return int(user_id)

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise ValueError(f"Invalid initData format: {str(e)}")
