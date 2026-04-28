import os
from dotenv import load_dotenv

load_dotenv()

# Your main bot token from @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Database path
DATABASE_PATH = os.getenv("DATABASE_PATH", "cloner_bot.db")

# Optional: your own API credentials (used if user doesn't provide theirs)
# These are only for the bot itself, not for users
FALLBACK_API_ID = int(os.getenv("FALLBACK_API_ID", "0"))
FALLBACK_API_HASH = os.getenv("FALLBACK_API_HASH", "")

# Auto-forward check interval (seconds)
AUTO_FORWARD_INTERVAL = int(os.getenv("AUTO_FORWARD_INTERVAL", "10"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")