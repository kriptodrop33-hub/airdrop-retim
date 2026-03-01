import os
from dotenv import load_dotenv

load_dotenv()  # Lokalde .env'den okur, Railway'de otomatik env var kullanılır

BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_admin = os.getenv("ADMIN_CHAT_ID", "0")
_group = os.getenv("GROUP_CHAT_ID", "0")

try:
    ADMIN_CHAT_ID = int(_admin)
except ValueError:
    raise ValueError(f"ADMIN_CHAT_ID sayı olmalı, şu an: '{_admin}'")

try:
    GROUP_CHAT_ID = int(_group)
except ValueError:
    raise ValueError(f"GROUP_CHAT_ID sayı olmalı, şu an: '{_group}'")
