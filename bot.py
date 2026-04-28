#!/usr/bin/env python3
"""
Multi-User Telegram Channel Cloner Bot
Authorized security testing tool only.
"""

import os
import sys
import asyncio
import logging

from telegram.ext import Application, CommandHandler

from config import BOT_TOKEN, LOG_LEVEL, AUTO_FORWARD_INTERVAL
from database import init_db
from clone_engine import AutoForwardEngine
from handlers import register_handlers
from user_client import shutdown_all

# ── Logging ────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log")
    ]
)
log = logging.getLogger("Bot")

# ── Global references ──────────────────────────────────
auto_forward_engine = None

async def post_init(app: Application):
    """Run after the bot initializes."""
    global auto_forward_engine
    
    log.info("Initializing database...")
    await init_db()
    
    log.info("Starting auto-forward engine...")
    auto_forward_engine = AutoForwardEngine(check_interval=AUTO_FORWARD
    auto_forward_engine = AutoForwardEngine(check_interval=AUTO_FORWARD_INTERVAL)
    await auto_forward_engine.start()
    
    log.info("Bot fully initialized and ready!")

async def post_shutdown(app: Application):
    """Clean up on shutdown."""
    global auto_forward_engine
    
    log.info("Shutting down...")
    
    if auto_forward_engine:
        await auto_forward_engine.stop()
    
    await shutdown_all()
    log.info("Shutdown complete.")

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        log.error("BOT_TOKEN not set! Create a .env file or set environment variable.")
        log.error("Get a token from @BotFather on Telegram.")
        sys.exit(1)
    
    log.info("╔══════════════════════════════════════════════╗")
    log.info("║   Multi-User Telegram Channel Cloner Bot     ║")
    log.info("╚══════════════════════════════════════════════╝")
    
    # Build the application
    app = Application.builder() \
        .token(BOT_TOKEN) \
        .post_init(post_init) \
        .post_shutdown(post_shutdown) \
        .concurrent_updates(True) \
        .build()
    
    # Register all command handlers
    register_handlers(app)
    
    log.info("Bot is polling for updates...")
    
    # Start polling (runs forever until Ctrl+C)
    app.run_polling(allowed_updates=[
        "message", "callback_query", "chat_member", "my_chat_member"
    ])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot stopped by user.")
    except Exception as e:
        log.exception(f"Fatal error: {e}")
        sys.exit(1)