"""Telegram Login Widget hash verification."""

import hashlib
import hmac
import os
import time


def verify_telegram_auth(data: dict, max_age_seconds: int = 86400) -> bool:
    """
    Verify Telegram Login Widget callback per
    https://core.telegram.org/widgets/login#checking-authorization
    """
    payload = dict(data)
    if "hash" not in payload:
        return False

    check_hash = payload.pop("hash")
    auth_date = int(payload.get("auth_date", 0))
    if auth_date and time.time() - auth_date > max_age_seconds:
        return False

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return False

    data_check_arr = [f"{k}={v}" for k, v in sorted(payload.items())]
    data_check_string = "\n".join(data_check_arr)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    hash_val = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return hash_val == check_hash
