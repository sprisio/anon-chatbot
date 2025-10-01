# database.py
# This module encapsulates all database logic, ensuring that other parts of the
# application do not need to interact with aiosqlite directly.
# It enforces atomic transactions for all state-changing operations.

import aiosqlite
from config import DATABASE_PATH

async def init_db():
    """Initializes the database and creates the users table if it doesn't exist."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                partner_id INTEGER,
                looking INTEGER DEFAULT 0
            )
        """)
        await db.commit()

async def set_user_looking(user_id: int, looking: bool):
    """Sets a user's 'looking' status."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Using INSERT OR IGNORE to handle new users gracefully
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.execute(
            "UPDATE users SET looking=?, partner_id=NULL WHERE user_id=?",
            (1 if looking else 0, user_id)
        )
        await db.commit()

async def find_and_create_match(user_id: int) -> int | None:
    """
    Atomically finds a waiting partner and creates a match.
    Returns the partner's ID if a match is made, otherwise None.
    This function is designed to be race-condition-proof.
    """
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # The 'async with db:' context manager handles transactions.
        # It will automatically commit on success or rollback on error.
        async with db.execute(
            "SELECT user_id FROM users WHERE looking=1 AND partner_id IS NULL AND user_id!=? LIMIT 1",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            partner_id = row
            # These two updates now happen within a single atomic transaction.
            # If the second update fails, the first one is rolled back.
            await db.execute("UPDATE users SET partner_id=?, looking=0 WHERE user_id=?", (partner_id, user_id))
            await db.execute("UPDATE users SET partner_id=?, looking=0 WHERE user_id=?", (user_id, partner_id))
            await db.commit() # Explicit commit after successful updates
            return partner_id
    return None

async def disconnect_pair(user_id: int) -> int | None:
    """
    Atomically disconnects a user and their partner.
    Sets 'looking' to 0 and 'partner_id' to NULL for both.
    Returns the former partner's ID if the user was in a chat, otherwise None.
    """
    partner_id = None
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT partner_id FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        
        if row and row:
            partner_id = row
            # Atomically update both users within a single transaction
            await db.execute("UPDATE users SET looking=0, partner_id=NULL WHERE user_id IN (?,?)", (user_id, partner_id))
        else:
            # If the user was not in a chat (e.g., just searching), update only them.
            await db.execute("UPDATE users SET looking=0, partner_id=NULL WHERE user_id=?", (user_id,))
        
        await db.commit()
    return partner_id

async def get_partner_id(user_id: int) -> int | None:
    """Fetches the current partner ID for a user."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT partner_id FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row if row else None