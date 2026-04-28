"""
Manages per-user Pyrogram clients using string sessions.
Each user gets their own in-memory client tied to their API credentials.
"""

import logging
from pyrogram import Client
from database import get_user, update_string_session

log = logging.getLogger("UserClient")

# Pool of active Pyrogram clients: {user_id: Client}
_active_clients = {}

async def get_user_client(user_id: int) -> Client:
    """
    Get or create a Pyrogram client for a user.
    Uses stored string sessions to avoid re-login.
    """
    # Return existing client if still connected
    if user_id in _active_clients:
        client = _active_clients[user_id]
        try:
            me = await client.get_me()
            if me:
                return client
        except:
            # Session expired, will recreate
            pass
    
    # Load user credentials from DB
    user_data = await get_user(user_id)
    if not user_data:
        raise ValueError(f"User {user_id} has not registered API credentials. Use /setup first.")
    
    api_id = user_data["api_id"]
    api_hash = user_data["api_hash"]
    string_session = user_data.get("string_session")
    
    # Create new client
    session_name = f"user_{user_id}"
    
    if string_session:
        # Restore from saved string session
        client = Client(
            session_name,
            api_id=api_id,
            api_hash=api_hash,
            session_string=string_session,
            in_memory=True
        )
    else:
        # Fresh login (first time)
        client = Client(
            session_name,
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True
        )
    
    await client.start()
    
    # Save the string session for future use
    if not string_session:
        new_session = await client.export_session_string()
        await update_string_session(user_id, new_session)
    
    # Cache it
    _active_clients[user_id] = client
    log.info(f"Pyrogram client started for user {user_id}")
    return client

async def remove_user_client(user_id: int):
    """Stop and remove a user's Pyrogram client."""
    if user_id in _active_clients:
        try:
            await _active_clients[user_id].stop()
        except:
            pass
        del _active_clients[user_id]
        log.info(f"Pyrogram client removed for user {user_id}")

async def shutdown_all():
    """Shutdown all active Pyrogram clients (called on bot shutdown)."""
    for user_id in list(_active_clients.keys()):
        await remove_user_client(user_id)
    log.info("All user clients shut down")