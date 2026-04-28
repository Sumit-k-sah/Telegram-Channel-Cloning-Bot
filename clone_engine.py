"""
Core cloning engine.
Handles:
  1. Historical clone (old messages with batch processing)
  2. Auto-forward (live detection of new messages)
"""

import asyncio
import logging
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import FloodWait, ChatWriteForbidden
from pyrogram.types import Message

from database import (
    update_job_state, get_user_job, create_auto_forward,
    update_auto_forward_last_msg, get_active_auto_forwards,
    get_user
)
from user_client import get_user_client

log = logging.getLogger("CloneEngine")

# ── Rate-limited copy helpers ───────────────────────────

async def _safe_copy(client: Client, from_chat, to_chat, msg_id, retries=3):
    """Copy a single message (strips 'Forwarded from' tag)."""
    for attempt in range(retries):
        try:
            return await client.copy_message(
                chat_id=to_chat,
                from_chat_id=from_chat,
                message_id=msg_id
            )
        except FloodWait as e:
            wait = e.value + 1
            log.warning(f"FloodWait {wait}s — msg {msg_id}")
            await asyncio.sleep(wait)
        except ChatWriteForbidden:
            log.error(f"Can't write to {to_chat} — check permissions")
            return None
        except Exception as e:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                log.error(f"Failed to copy msg {msg_id}: {e}")
                return None
    return None

async def _safe_copy_media_group(client: Client, from_chat, to_chat, msg_id, retries=3):
    """Copy a media group (album) — also strips forward tag."""
    for attempt in range(retries):
        try:
            return await client.copy_media_group(
                chat_id=to_chat,
                from_chat_id=from_chat,
                message_id=msg_id
            )
        except FloodWait as e:
            wait = e.value + 1
            log.warning(f"FloodWait {wait}s — media group {msg_id}")
            await asyncio.sleep(wait)
        except ChatWriteForbidden:
            log.error(f"Can't write to {to_chat}")
            return None
        except Exception as e:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                log.error(f"Failed to copy media group {msg_id}: {e}")
                return None
    return None

# ── Historical Clone ────────────────────────────────────

async def run_historical_clone(user_id: int, job_id: int,
                                on_progress=None, on_complete=None):
    """
    Clone all historical messages from source to destination.
    Runs in the background; reports progress via callbacks.
    
    Callback signatures:
      on_progress(user_id, job_id, current, total, percent)
      on_complete(user_id, job_id, total_cloned)
    """
    log.info(f"[User {user_id}] Starting historical clone job {job_id}")
    
    # Get the client for this user
    try:
        client = await get_user_client(user_id)
    except ValueError as e:
        log.error(f"[User {user_id}] {e}")
        if on_complete:
            await on_complete(user_id, job_id, 0, str(e))
        return
    
    # Get job details
    job = await get_user_job(user_id, job_id)
    if not job:
        log.error(f"[User {user_id}] Job {job_id} not found")
        return
    
    source = job["source_channel"]
    dest = job["dest_channel"]
    direction = job["direction"]
    delay = job["delay"]
    only_media = bool(job["only_media"])
    last_cloned = job["last_cloned_msg_id"]
    total_cloned = job["total_cloned"]
    
    # Resolve channels
    try:
        source_chat = await client.get_chat(source)
        dest_chat = await client.get_chat(dest)
    except Exception as e:
        msg = f"Cannot access channel: {e}"
        log.error(f"[User {user_id}] {msg}")
        await update_job_state(job_id, status="failed")
        if on_complete:
            await on_complete(user_id, job_id, 0, msg)
        return
    
    source_id = source_chat.id
    dest_id = dest_chat.id
    label = f"{source_chat.title} → {dest_chat.title}"
    
    await update_job_state(job_id, status="running")
    
    # Get message range
    try:
        first = await client.get_messages(source_id, 1)
        latest = await client.get_messages(source_id, 0)
        first_id = first.id if first else 1
        latest_id = latest.id if latest else 0
        if latest_id == 0:
            await update_job_state(job_id, status="complete")
            log.info(f"[User {user_id}] {label} — channel empty")
            if on_complete:
                await on_complete(user_id, job_id, total_cloned)
            return
    except Exception as e:
        log.error(f"[User {user_id}] Range fetch failed: {e}")
        await update_job_state(job_id, status="failed")
        if on_complete:
            await on_complete(user_id, job_id, total_cloned, str(e))
        return
    
    processed = 0
    seen_media_groups = set()
    
    if direction == "oldest":
        current_id = last_cloned + 1 if last_cloned > 0 else first_id
        
        while current_id <= latest_id:
            batch_end = min(current_id + 99, latest_id)
            msg_ids = list(range(current_id, batch_end + 1))
            
            try:
                messages = await client.get_messages(source_id, msg_ids)
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
                continue
            except Exception as e:
                log.error(f"[User {user_id}] Batch fetch error: {e}")
                current_id = batch_end + 1
                continue
            
            for msg in messages:
                if msg is None or msg.empty:
                    continue
                
                # Media-only filter
                if only_media and not msg.media and not msg.media_group_id:
                    continue
                
                # Media group dedup
                if msg.media_group_id:
                    if msg.media_group_id in seen_media_groups:
                        continue
                    seen_media_groups.add(msg.media_group_id)
                    await _safe_copy_media_group(client, source_id, dest_id, msg.id)
                else:
                    await _safe_copy(client, source_id, dest_id, msg.id)
                
                processed += 1
                await update_job_state(job_id, 
                    last_msg_id=msg.id,
                    total_cloned=total_cloned + processed)
                
                # Progress callback
                if on_progress and processed % 10 == 0:
                    pct = (msg.id - first_id) / (latest_id - first_id) * 100
                    await on_progress(user_id, job_id, 
                        total_cloned + processed, latest_id - first_id, pct)
                
                await asyncio.sleep(delay)
            
            current_id = batch_end + 1
    
    else:  # newest direction
        offset_id = 0
        while True:
            try:
                async for msg in client.get_chat_history(source_id, limit=100, offset_id=offset_id):
                    if msg.id <= last_cloned:
                        return  # Reached already-cloned content
                    
                    if only_media and not msg.media and not msg.media_group_id:
                        continue
                    
                    if msg.media_group_id:
                        if msg.media_group_id in seen_media_groups:
                            continue
                        seen_media_groups.add(msg.media_group_id)
                        await _safe_copy_media_group(client, source_id, dest_id, msg.id)
                    else:
                        await _safe_copy(client, source_id, dest_id, msg.id)
                    
                    processed += 1
                    await update_job_state(job_id,
                        last_msg_id=msg.id,
                        total_cloned=total_cloned + processed)
                    
                    if on_progress and processed % 10 == 0:
                        await on_progress(user_id, job_id, total_cloned + processed, None, None)
                    
                    await asyncio.sleep(delay)
                    offset_id = msg.id
                    
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
            except Exception as e:
                log.error(f"[User {user_id}] History error: {e}")
                break
    
    # Mark complete
    final_total = total_cloned + processed
    await update_job_state(job_id, status="complete", total_cloned=final_total)
    log.info(f"[User {user_id}] {label} — COMPLETE: {final_total} messages")
    
    # If auto-forward was requested, set it up
    if job["auto_forward"]:
        await create_auto_forward(user_id, source, dest)
        log.info(f"[User {user_id}] Auto-forward enabled for {source} → {dest}")
    
    if on_complete:
        await on_complete(user_id, job_id, final_total)

# ── Auto-Forward Engine ─────────────────────────────────

class AutoForwardEngine:
    """
    Runs in the background, polling all active auto-forward
    configurations for new messages.
    """
    
    def __init__(self, check_interval: int = 10):
        self.check_interval = check_interval
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the auto-forward polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info(f"Auto-forward engine started (interval: {self.check_interval}s)")
    
    async def stop(self):
        """Stop the auto-forward polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Auto-forward engine stopped")
    
    async def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                log.error(f"Auto-forward poll error: {e}")
            await asyncio.sleep(self.check_interval)
    
    async def _check_all(self):
        """Check all active auto-forward configs for new messages."""
        configs = await get_active_auto_forwards()
        
        for cfg in configs:
            try:
                await self._check_single(cfg)
            except Exception as e:
                log.error(f"Auto-forward check failed for config {cfg['id']}: {e}")
    
    async def _check_single(self, cfg: dict):
        """Check a single auto-forward config for new messages."""
        user_id = cfg["user_id"]
        source = cfg["source_channel"]
        dest = cfg["dest_channel"]
        last_msg_id = cfg["last_msg_id"]
        af_id = cfg["id"]
        
        try:
            client = await get_user_client(user_id)
        except ValueError:
            # User hasn't set up — skip
            return
        
        try:
            # Get the latest message from source
            latest = await client.get_messages(source, 0)
            if latest is None or latest.empty:
                return
            
            if latest.id > last_msg_id:
                # New messages found! Forward from last_msg_id+1 to latest
                log.info(f"[AF {af_id}] New messages detected: {last_msg_id+1} → {latest.id}")
                
                # Forward in batches
                current = last_msg_id + 1
                while current <= latest.id:
                    batch_end = min(current + 50, latest.id)
                    msg_ids = list(range(current, batch_end + 1))
                    
                    try:
                        messages = await client.get_messages(source, msg_ids)
                    except:
                        current = batch_end + 1
                        continue
                    
                    seen_media = set()
                    for msg in messages:
                        if msg is None or msg.empty:
                            continue
                        
                        try:
                            if msg.media_group_id:
                                if msg.media_group_id in seen_media:
                                    continue
                                seen_media.add(msg.media_group_id)
                                await _safe_copy_media_group(client, source, dest, msg.id)
                            else:
                                await _safe_copy(client, source, dest, msg.id)
                        except Exception as e:
                            log.error(f"[AF {af_id}] Copy error msg {msg.id}: {e}")
                    
                    # Update the last seen message ID
                    await update_auto_forward_last_msg(af_id, batch_end)
                    current = batch_end + 1
                    await asyncio.sleep(1.0)  # Rate limit between batches
                    
        except Exception as e:
            log.error(f"[AF {af_id}] Check error for user {user_id}: {e}")