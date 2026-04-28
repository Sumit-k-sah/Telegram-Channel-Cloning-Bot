"""
Interactive Telegram bot command handlers.
Uses python-telegram-bot with ConversationHandler for multi-step flows.
"""

import logging
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler, ContextTypes
)

from database import (
    save_user, get_user, get_user_jobs, get_user_job,
    create_clone_job, delete_job, update_job_state,
    get_user_auto_forwards, delete_auto_forward
)
from clone_engine import run_historical_clone
from user_client import get_user_client, remove_user_client

log = logging.getLogger("Handlers")

# ── Conversation States ─────────────────────────────────
(SETUP_API_ID, SETUP_API_HASH, 
 CLONE_SOURCE, CLONE_DEST, CLONE_DIRECTION, CLONE_DELAY, CLONE_MEDIA_ONLY, CLONE_AUTO_FORWARD,
 CONFIRM_CLONE) = range(9)

# ── Helper Functions ────────────────────────────────────

def main_menu_keyboard():
    """Build the main interactive menu keyboard."""
    keyboard = [
        [InlineKeyboardButton("🔧 Setup API Credentials", callback_data="setup")],
        [InlineKeyboardButton("📋 My Clone Jobs", callback_data="my_jobs")],
        [InlineKeyboardButton("➕ New Clone Job", callback_data="new_clone")],
        [InlineKeyboardButton("🔄 Auto-Forwards", callback_data="auto_forwards")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def progress_callback(user_id, job_id, current, total, percent):
    """Called periodically during cloning to update the job state."""
    # State is already saved in clone_engine — this is for future
    # real-time notifications if needed
    pass

async def complete_callback(user_id, job_id, total, error=None):
    """Called when a clone job completes."""
    log.info(f"Job {job_id} for user {user_id} complete: {total} messages")

# ── Core Commands ───────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message with interactive menu."""
    user = update.effective_user
    
    # Check if user is registered
    user_data = await get_user(user.id)
    
    welcome = (
        f"👋 Welcome, {user.first_name}!\n\n"
        f"🤖 **Multi-Channel Telegram Cloner**\n\n"
        f"Clone any Telegram channel's content to your own channels.\n"
        f"✅ No 'Forwarded from' tag\n"
        f"✅ Supports all media types\n"
        f"✅ Auto-forward new posts in real-time\n"
        f"✅ Multiple users can use this bot simultaneously\n\n"
    )
    
    if user_data:
        welcome += f"✅ **You're registered!** (API ID: {str(user_data['api_id'])[:4]}...)\n"
    else:
        welcome += "⚠️ **You need to set up your API credentials first.**\n"
    
    await update.message.reply_text(
        welcome,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed help."""
    help_text = (
        "📖 **How to Use This Bot**\n\n"
        "**Step 1: /setup** — Provide your Telegram API ID and Hash\n"
        "   (Get these from https://my.telegram.org/apps)\n\n"
        "**Step 2: Add bot to channels**\n"
        "   • Add this bot as a member of the SOURCE channel\n"
        "   • Add this bot as ADMIN of the DESTINATION channel\n"
        "     (needs 'Post Messages' permission)\n\n"
        "**Step 3: /clone** — Create a clone job\n"
        "   • Source: the channel to copy FROM\n"
        "   • Destination: the channel to copy TO\n"
        "   • Direction: 'oldest' (chronological) or 'newest' (recent first)\n"
        "   • Auto-forward: YES = also forward new posts automatically\n\n"
        "**Commands:**\n"
        "`/start` — Main menu\n"
        "`/setup` — Set your API credentials\n"
        "`/clone` — Create a new clone job\n"
        "`/jobs` — View your clone jobs\n"
        "`/forwards` — Manage auto-forwards\n"
        "`/cancel` — Cancel current operation\n"
        "`/help` — This message\n\n"
        "🔒 Your API credentials are stored encrypted and never shared."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing conversation."""
    await update.message.reply_text(
        "❌ Operation cancelled. Use /start to return to the menu."
    )
    return ConversationHandler.END

# ── Setup Conversation ──────────────────────────────────

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Begin the API setup conversation."""
    query = update.callback_query
    if query:
        await query.answer()
    
    text = (
        "🔧 **API Credentials Setup**\n\n"
        "I need your Telegram API credentials to clone channels.\n"
        "These are **NOT** your bot token.\n\n"
        "1️⃣ Go to https://my.telegram.org/apps\n"
        "2️⃣ Log in with your phone number\n"
        "3️⃣ Create an application (if you haven't)\n"
        "4️⃣ Copy your **API ID** and **API Hash**\n\n"
        "📤 **Send me your API ID** (just the number):\n"
        "_(or type /cancel to abort)_"
    )
    
    if query:
        await query.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")
    
    return SETUP_API_ID

async def setup_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive API ID."""
    try:
        api_id = int(update.message.text.strip())
        context.user_data["setup_api_id"] = api_id
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid API ID. It should be a number (e.g., 123456).\n"
            "Please send the correct API ID:"
        )
        return SETUP_API_ID
    
    await update.message.reply_text(
        "✅ API ID received!\n\n"
        "📤 **Now send me your API Hash:**\n"
        "_(a long string like '1a2b3c4d5e6f7g8h9i0j')_\n"
        "_(or type /cancel to abort)_"
    )
    return SETUP_API_HASH

async def setup_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive API Hash and complete setup."""
    api_hash = update.message.text.strip()
    api_id = context.user_data["setup_api_id"]
    
    if len(api_hash) < 10:
        await update.message.reply_text(
            "❌ That doesn't look like a valid API Hash (too short).\n"
            "Please send the correct API Hash:"
        )
        return SETUP_API_HASH
    
    # Save user credentials
    await save_user(update.effective_user.id, api_id, api_hash)
    
    # Try to establish a Pyrogram session
    try:
        client = await get_user_client(update.effective_user.id)
        await update.message.reply_text(
            "✅ **Setup complete!** Your Telegram account is connected.\n\n"
            "Your string session has been saved — you won't need to log in again.\n\n"
            "Now you can:\n"
            "• `/clone` — Create a clone job\n"
            "• `/start` — Return to the main menu",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(
            "⚠️ **Credentials saved**, but I couldn't establish a session.\n"
            f"Error: {e}\n\n"
            "Make sure the API ID and Hash are correct from my.telegram.org",
            parse_mode="Markdown"
        )
    
    # Clean up context
    context.user_data.pop("setup_api_id", None)
    
    return ConversationHandler.END

# ── New Clone Job Conversation ──────────────────────────

async def new_clone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Begin clone job creation."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    user_data = await get_user(user_id)
    
    if not user_data:
        text = (
            "⚠️ **You need to set up your API credentials first!**\n\n"
            "Use /setup to provide your Telegram API ID and Hash."
        )
        if query:
            await query.message.reply_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return ConversationHandler.END
    
    text = (
        "📋 **New Clone Job — Step 1/7**\n\n"
        "What is the **source channel**?\n"
        "(The channel you want to clone FROM)\n\n"
        "Send the username (e.g., `@tech_news`) or chat ID:\n"
        "_(or type /cancel to abort)_"
    )
    
    if query:
        await query.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")
    
    return CLONE_SOURCE

async def clone_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive source channel."""
    source = update.message.text.strip()
    context.user_data["clone_source"] = source
    
    await update.message.reply_text(
        f"✅ Source: `{source}`\n\n"
        "**Step 2/7** — What is the **destination channel**?\n"
        "(The channel to clone INTO)\n\n"
        "Send the username (e.g., `@my_backup`) or chat ID:\n"
        "_(or type /cancel to abort)_",
        parse_mode="Markdown"
    )
    return CLONE_DEST

async def clone_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive destination channel."""
    dest = update.message.text.strip()
    context.user_data["clone_dest"] = dest
    
    keyboard = [
        [InlineKeyboardButton("📅 Oldest (chronological)", callback_data="dir_oldest")],
        [InlineKeyboardButton("🕐 Newest (recent first)", callback_data="dir_newest")],
    ]
    
    await update.message.reply_text(
        f"✅ Destination: `{dest}`\n\n"
        "**Step 3/7** — Clone direction?\n\n"
        "• **Oldest** — Preserves original order (recommended)\n"
        "• **Newest** — Gets recent content first",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CLONE_DIRECTION

async def clone_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive clone direction via callback."""
    query = update.callback_query
    await query.answer()
    
    direction = "oldest" if "oldest" in query.data else "newest"
    context.user_data["clone_direction"] = direction
    
    await query.message.reply_text(
        f"✅ Direction: `{direction.upper()}`\n\n"
        "**Step 4/7** — Delay between messages (seconds)?\n\n"
        "Recommended: `1.0` (safe)\n"
        "Faster: `0.5` (risk of rate limits)\n"
        "Slow: `2.0` (very safe)\n\n"
        "Send a number (e.g., `1.0`):\n"
        "_(or /cancel to abort)_",
        parse_mode="Markdown"
    )
    return CLONE_DELAY

async def clone_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive delay value."""
    try:
        delay = float(update.message.text.strip())
        if delay < 0.3:
            await update.message.reply_text("❌ Delay too low. Minimum 0.3 seconds. Try again:")
            return CLONE_DELAY
        context.user_data["clone_delay"] = delay
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Send a number like `1.0`:", parse_mode="Markdown")
        return CLONE_DELAY
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes — Clone ALL content", callback_data="media_all")],
        [InlineKeyboardButton("📷 Media only (photos/videos)", callback_data="media_only")],
    ]
    
    await update.message.reply_text(
        f"✅ Delay: `{delay}s`\n\n"
        "**Step 5/7** — Clone only media content?\n\n"
        "• **All content** — Text, photos, videos, documents, etc.\n"
        "• **Media only** — Just photos and videos (skip text posts)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CLONE_MEDIA_ONLY

async def clone_media_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive media-only preference."""
    query = update.callback_query
    await query.answer()
    
    only_media = query.data == "media_only"
    context.user_data["clone_media_only"] = only_media
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes — Also auto-forward new posts", callback_data="af_yes")],
        [InlineKeyboardButton("❌ No — Just clone history", callback_data="af_no")],
    ]
    
    await query.message.reply_text(
        f"✅ Media only: `{'Yes' if only_media else 'No'}`\n\n"
        "**Step 6/7** — Enable **Auto-Forward**?\n\n"
        "• **Yes** — After cloning history, the bot will\n"
        "  automatically forward every new post from the\n"
        "  source to the destination in real-time.\n"
        "• **No** — Just clone existing messages, no live forwarding.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CLONE_AUTO_FORWARD

async def clone_auto_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive auto-forward preference and show summary."""
    query = update.callback_query
    await query.answer()
    
    auto_forward = query.data == "af_yes"
    context.user_data["clone_auto_forward"] = auto_forward
    
    # Build summary
    source = context.user_data.get("clone_source", "?")
    dest = context.user_data.get("clone_dest", "?")
    direction = context.user_data.get("clone_direction", "oldest")
    delay = context.user_data.get("clone_delay", 1.0)
    only_media = context.user_data.get("clone_media_only", False)
    
    summary = (
        "📋 **Clone Job Summary**\n\n"
        f"**Source:** `{source}`\n"
        f"**Destination:** `{dest}`\n"
        f"**Direction:** `{direction.upper()}`\n"
        f"**Delay:** `{delay}s`\n"
        f"**Media only:** `{'Yes' if only_media else 'No'}`\n"
        f"**Auto-forward:** `{'Yes' if auto_forward else 'No'}`\n\n"
        "Start this clone job?"
    )
    
    keyboard = [
        [InlineKeyboardButton("🚀 Start Clone!", callback_data="confirm_clone")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_clone")],
    ]
    
    await query.message.reply_text(
        summary,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CONFIRM_CLONE

async def clone_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed — create and run the clone job."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_clone":
        await query.message.reply_text("❌ Clone cancelled.")
        # Clean up
        for key in ["clone_source", "clone_dest", "clone_direction", "clone_delay", "clone_media_only", "clone_auto_forward"]:
            context.user_data.pop(key, None)
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    source = context.user_data.get("clone_source")
    dest = context.user_data.get("clone_dest")
    direction = context.user_data.get("clone_direction", "oldest")
    delay = context.user_data.get("clone_delay", 1.0)
    only_media = context.user_data.get("clone_media_only", False)
    auto_forward = context.user_data.get("clone_auto_forward", False)
    
    # Create job in database
    job_id = await create_clone_job(
        user_id=user_id,
        source=source,
        dest=dest,
        direction=direction,
        delay=delay,
        only_media=only_media,
        auto_forward=auto_forward
    )
    
    await query.message.reply_text(
        f"🚀 **Clone job #{job_id} created!**\n\n"
        f"Starting clone from `{source}` to `{dest}`...\n"
        f"Direction: `{direction.upper()}`\n\n"
        f"⏳ This may take a while depending on channel size.\n"
        f"Use `/jobs` to check progress.\n"
        f"Use `/forwards` to manage auto-forward settings.",
        parse_mode="Markdown"
    )
    
    # Run clone in the background
    asyncio.create_task(
        run_historical_clone(
            user_id=user_id,
            job_id=job_id,
            on_progress=progress_callback,
            on_complete=complete_callback
        )
    )
    
    # Clean up
    for key in ["clone_source", "clone_dest", "clone_direction", "clone_delay", "clone_media_only", "clone_auto_forward"]:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END

# ── View Jobs ───────────────────────────────────────────

async def my_jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all clone jobs for the user."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    jobs = await get_user_jobs(user_id)
    
    if not jobs:
        text = "📋 **No clone jobs yet.**\n\nUse `/clone` or tap 'New Clone Job' to create one."
        if query:
            await query.message.reply_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return
    
    lines = ["📋 **Your Clone Jobs**\n"]
    for job in jobs[:10]:  # Show last 10
        status_emoji = {
            "idle": "⏸️", "running": "🔄", "complete": "✅", "failed": "❌"
        }.get(job["status"], "❓")
        
        lines.append(
            f"{status_emoji} **Job #{job['id']}**\n"
            f"   FROM: `{job['source_channel']}`\n"
            f"   TO: `{job['dest_channel']}`\n"
            f"   Cloned: `{job['total_cloned']}` msgs\n"
            f"   Auto-fwd: `{'ON' if job['auto_forward'] else 'OFF'}`\n"
            f"   Status: `{job['status']}`\n"
        )
    
    if query:
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def auto_forwards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show and manage auto-forwards."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    forwards = await get_user_auto_forwards(user_id)
    
    if not forwards:
        text = (
            "🔄 **No active auto-forwards.**\n\n"
            "When creating a clone job, enable 'Auto-Forward'\n"
            "to automatically forward new posts in real-time."
        )
        if query:
            await query.message.reply_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return
    
    lines = ["🔄 **Your Auto-Forwards**\n"]
    for fwd in forwards:
        status = "✅ Active" if fwd["active"] else "⏸️ Paused"
        lines.append(
            f"**ID #{fwd['id']}** — {status}\n"
            f"   FROM: `{fwd['source_channel']}`\n"
            f"   TO: `{fwd['dest_channel']}`\n"
            f"   Last msg: `{fwd['last_msg_id']}`\n"
        )
    
    # Add delete button (just show IDs, user can /delete_forward <id>)
    lines.append("\nTo remove: `/delete_forward <ID>`")
    
    if query:
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def delete_forward_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete an auto-forward by ID."""
    try:
        fwd_id = int(context.args[0])
        await delete_auto_forward(fwd_id, update.effective_user.id)
        await update.message.reply_text(f"✅ Auto-forward #{fwd_id} deleted.")
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: `/delete_forward <ID>`\n"
            "Get IDs from `/forwards`",
            parse_mode="Markdown"
        )

# ── Callback Router ─────────────────────────────────────

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu button presses."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "setup":
        return await setup_start(update, context)
    
    elif query.data == "my_jobs":
        await my_jobs_command(update, context)
        return ConversationHandler.END
    
    elif query.data == "new_clone":
        return await new_clone_start(update, context)
    
    elif query.data == "auto_forwards":
        await auto_forwards_command(update, context)
        return ConversationHandler.END
    
    elif query.data == "help":
        await help_command(update, context)
        return ConversationHandler.END

# ── Register Handlers ───────────────────────────────────

def register_handlers(app: Application):
    """Register all command and conversation handlers."""
    
    # Simple commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("jobs", my_jobs_command))
    app.add_handler(CommandHandler("forwards", auto_forwards_command))
    app.add_handler(CommandHandler("delete_forward", delete_forward_command))
    
    # Setup conversation
    setup_conv = ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_start),
            CallbackQueryHandler(setup_start, pattern="^setup$")
        ],
        states={
            SETUP_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_api_id)],
            SETUP_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_api_hash)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
    app.add_handler(setup_conv)
    
    # New clone conversation
    clone_conv = ConversationHandler(
        entry_points=[
            CommandHandler("clone", new_clone_start),
            CallbackQueryHandler(new_clone_start, pattern="^new_clone$")
        ],
        states={
            CLONE_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, clone_source)],
            CLONE_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, clone_dest)],
            CLONE_DIRECTION: [CallbackQueryHandler(clone_direction, pattern="^dir_")],
            CLONE_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, clone_delay)],
            CLONE_MEDIA_ONLY: [CallbackQueryHandler(clone_media_only, pattern="^media_")],
            CLONE_AUTO_FORWARD: [CallbackQueryHandler(clone_auto_forward, pattern="^af_")],
            CONFIRM_CLONE: [CallbackQueryHandler(clone_confirm, pattern="^(confirm_clone|cancel_clone)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
    app.add_handler(clone_conv)
    
    # Main menu callback router (for non-conversation callbacks)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^(help|my_jobs|auto_forwards)$"))
    
    log.info("All handlers registered")