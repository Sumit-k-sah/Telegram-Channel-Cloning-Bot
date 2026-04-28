import aiosqlite
import json
import logging
from datetime import datetime

log = logging.getLogger("Database")

DB_PATH = "cloner_bot.db"

async def init_db():
    """Initialize all database tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash TEXT NOT NULL,
                string_session TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Clone jobs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS clone_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_channel TEXT NOT NULL,
                dest_channel TEXT NOT NULL,
                direction TEXT DEFAULT 'oldest',
                delay REAL DEFAULT 1.0,
                only_media INTEGER DEFAULT 0,
                auto_forward INTEGER DEFAULT 0,
                last_cloned_msg_id INTEGER DEFAULT 0,
                total_cloned INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Auto-forward configs (live listeners)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_forwards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_channel TEXT NOT NULL,
                dest_channel TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                last_msg_id INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        await db.commit()
    log.info("Database initialized")

# ── User operations ─────────────────────────────────────

async def save_user(user_id: int, api_id: int, api_hash: str, string_session: str = None):
    """Register or update a user's API credentials."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, api_id, api_hash, string_session, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                api_id = excluded.api_id,
                api_hash = excluded.api_hash,
                string_session = COALESCE(excluded.string_session, users.string_session),
                updated_at = excluded.updated_at
        """, (user_id, api_id, api_hash, string_session, now, now))
        await db.commit()

async def get_user(user_id: int) -> dict:
    """Get a user's credentials."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def update_string_session(user_id: int, string_session: str):
    """Update a user's Pyrogram string session."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET string_session = ?, updated_at = ?
            WHERE user_id = ?
        """, (string_session, now, user_id))
        await db.commit()

# ── Clone job operations ────────────────────────────────

async def create_clone_job(user_id: int, source: str, dest: str,
                           direction: str = "oldest", delay: float = 1.0,
                           only_media: bool = False, auto_forward: bool = False) -> int:
    """Create a new clone job. Returns the job ID."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO clone_jobs 
            (user_id, source_channel, dest_channel, direction, delay, 
             only_media, auto_forward, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?)
        """, (user_id, source, dest, direction, delay, 
              int(only_media), int(auto_forward), now, now))
        await db.commit()
        return cursor.lastrowid

async def get_user_jobs(user_id: int) -> list:
    """Get all clone jobs for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM clone_jobs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_user_job(user_id: int, job_id: int) -> dict:
    """Get a specific clone job."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM clone_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

async def update_job_state(job_id: int, last_msg_id: int = None,
                            total_cloned: int = None, status: str = None):
    """Update a job's clone state."""
    now = datetime.utcnow().isoformat()
    updates = {"updated_at": now}
    if last_msg_id is not None:
        updates["last_cloned_msg_id"] = last_msg_id
    if total_cloned is not None:
        updates["total_cloned"] = total_cloned
    if status is not None:
        updates["status"] = status
    
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [job_id]
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE clone_jobs SET {set_clause} WHERE id = ?",
            values
        )
        await db.commit()

async def delete_job(job_id: int, user_id: int):
    """Delete a clone job."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM clone_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id)
        )
        await db.commit()

# ── Auto-forward operations ─────────────────────────────

async def create_auto_forward(user_id: int, source: str, dest: str):
    """Register an auto-forward listener."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO auto_forwards 
            (user_id, source_channel, dest_channel, active, created_at)
            VALUES (?, ?, ?, 1, ?)
        """, (user_id, source, dest, now))
        await db.commit()

async def get_active_auto_forwards() -> list:
    """Get all active auto-forward configurations."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM auto_forwards WHERE active = 1"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def update_auto_forward_last_msg(auto_fwd_id: int, msg_id: int):
    """Update the last seen message ID for an auto-forward."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE auto_forwards SET last_msg_id = ? WHERE id = ?",
            (msg_id, auto_fwd_id)
        )
        await db.commit()

async def get_user_auto_forwards(user_id: int) -> list:
    """Get all auto-forwards for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM auto_forwards WHERE user_id = ?",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def delete_auto_forward(auto_fwd_id: int, user_id: int):
    """Delete an auto-forward configuration."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM auto_forwards WHERE id = ? AND user_id = ?",
            (auto_fwd_id, user_id)
        )
        await db.commit()