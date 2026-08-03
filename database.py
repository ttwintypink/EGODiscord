"""
database.py — Асинхронная работа с SQLite для EGODiscord.

Схема:
    - blacklist(user_id, added_by, added_at, reason)
    - active_tickets(channel_id, user_id, type, created_at, claimed_at,
                     claimed_by, status, voice_channel_id, transcript_path,
                     form_text, last_message_at, warned_inactive)
    - stats(recruiter_id UNIQUE, ticket_count, total_stars, ratings_count,
            total_reaction_time, last_activity)
    - ticket_messages(id, channel_id, author_id, author_name, content,
                      created_at) — для транскриптов
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional

import aiosqlite

DB_PATH = "database.db"


# ============================================================================
# Инициализация
# ============================================================================

async def init_db() -> None:
    """Создаёт все таблицы, если их ещё нет, и выполняет миграции."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id     INTEGER PRIMARY KEY,
                added_by    INTEGER NOT NULL,
                added_at    INTEGER NOT NULL,
                reason      TEXT
            );

            CREATE TABLE IF NOT EXISTS active_tickets (
                channel_id      INTEGER PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                type            TEXT NOT NULL,
                created_at      INTEGER NOT NULL,
                claimed_at      INTEGER,
                claimed_by      INTEGER,
                status          TEXT DEFAULT 'open',
                voice_channel_id INTEGER,
                transcript_path TEXT,
                form_text       TEXT,
                last_message_at INTEGER,
                warned_inactive INTEGER DEFAULT 0,
                original_nickname TEXT
            );

            CREATE TABLE IF NOT EXISTS stats (
                recruiter_id        INTEGER PRIMARY KEY,
                ticket_count        INTEGER DEFAULT 0,
                total_stars         INTEGER DEFAULT 0,
                ratings_count       INTEGER DEFAULT 0,
                total_reaction_time INTEGER DEFAULT 0,
                last_activity       INTEGER
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id  INTEGER NOT NULL,
                author_id   INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                content     TEXT,
                created_at  INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ticket_messages_channel
                ON ticket_messages(channel_id);

            CREATE INDEX IF NOT EXISTS idx_active_tickets_user
                ON active_tickets(user_id);
            """
        )
        await db.commit()

        # --- Миграции (добавляем колонки, если их нет) ---
        # Проверяем наличие колонки original_nickname
        async with db.execute("PRAGMA table_info(active_tickets)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        if "original_nickname" not in columns:
            await db.execute(
                "ALTER TABLE active_tickets ADD COLUMN original_nickname TEXT"
            )
            await db.commit()


# ============================================================================
# Blacklist
# ============================================================================

async def blacklist_add(user_id: int, added_by: int, reason: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO blacklist(user_id, added_by, added_at, reason) "
            "VALUES(?, ?, ?, ?)",
            (user_id, added_by, int(time.time()), reason),
        )
        await db.commit()


async def blacklist_remove(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM blacklist WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def blacklist_contains(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def blacklist_list() -> list[tuple[int, int, int, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, added_by, added_at, reason FROM blacklist ORDER BY added_at DESC"
        ) as cur:
            return await cur.fetchall()


# ============================================================================
# Active tickets
# ============================================================================

async def ticket_create(channel_id: int, user_id: int, ticket_type: str,
                        form_text: str = "",
                        original_nickname: str = "") -> None:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO active_tickets(channel_id, user_id, type, created_at, "
            "status, form_text, last_message_at, original_nickname) "
            "VALUES(?, ?, ?, ?, 'open', ?, ?, ?)",
            (channel_id, user_id, ticket_type, now, form_text, now, original_nickname),
        )
        await db.commit()


async def ticket_exists_for_user(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM active_tickets WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ) as cur:
            return await cur.fetchone() is not None


async def ticket_get(channel_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_tickets WHERE channel_id = ?", (channel_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def ticket_set_claimed(channel_id: int, claimed_by: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE active_tickets SET claimed_at = ?, claimed_by = ? "
            "WHERE channel_id = ?",
            (int(time.time()), claimed_by, channel_id),
        )
        await db.commit()


async def ticket_set_voice(channel_id: int, voice_channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE active_tickets SET voice_channel_id = ? WHERE channel_id = ?",
            (voice_channel_id, channel_id),
        )
        await db.commit()


async def ticket_set_status(channel_id: int, status: str,
                            transcript_path: Optional[str] = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if transcript_path:
            await db.execute(
                "UPDATE active_tickets SET status = ?, transcript_path = ? "
                "WHERE channel_id = ?",
                (status, transcript_path, channel_id),
            )
        else:
            await db.execute(
                "UPDATE active_tickets SET status = ? WHERE channel_id = ?",
                (status, channel_id),
            )
        await db.commit()


async def ticket_update_last_message(channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE active_tickets SET last_message_at = ?, warned_inactive = 0 "
            "WHERE channel_id = ?",
            (int(time.time()), channel_id),
        )
        await db.commit()


async def ticket_set_warned(channel_id: int, warned: bool = True) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE active_tickets SET warned_inactive = ? WHERE channel_id = ?",
            (1 if warned else 0, channel_id),
        )
        await db.commit()


async def ticket_delete(channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM active_tickets WHERE channel_id = ?", (channel_id,))
        await db.execute("DELETE FROM ticket_messages WHERE channel_id = ?", (channel_id,))
        await db.commit()


async def ticket_get_voice_owner(voice_channel_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_tickets WHERE voice_channel_id = ?",
            (voice_channel_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def tickets_get_all_open() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_tickets WHERE status = 'open'"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def tickets_get_user_active(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_tickets WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ============================================================================
# Ticket messages (для транскриптов)
# ============================================================================

async def message_add(channel_id: int, author_id: int, author_name: str,
                      content: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ticket_messages(channel_id, author_id, author_name, content, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (channel_id, author_id, author_name, content, int(time.time())),
        )
        await db.commit()


async def message_get_last_n(channel_id: int, n: int = 5) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ticket_messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
            (channel_id, n),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in reversed(rows)]


async def message_get_all(channel_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ticket_messages WHERE channel_id = ? ORDER BY id ASC",
            (channel_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ============================================================================
# Stats
# ============================================================================

async def stats_add_ticket(recruiter_id: int, reaction_time_sec: int = 0) -> None:
    """Вызывается при закрытии тикета recruiter'ом."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO stats(recruiter_id, ticket_count, total_stars, ratings_count, "
            "total_reaction_time, last_activity) "
            "VALUES(?, 1, 0, 0, ?, ?) "
            "ON CONFLICT(recruiter_id) DO UPDATE SET "
            "  ticket_count = ticket_count + 1, "
            "  total_reaction_time = total_reaction_time + ?, "
            "  last_activity = ?",
            (recruiter_id, reaction_time_sec, int(time.time()),
             reaction_time_sec, int(time.time())),
        )
        await db.commit()


async def stats_add_rating(recruiter_id: int, stars: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO stats(recruiter_id, ticket_count, total_stars, ratings_count, "
            "total_reaction_time, last_activity) "
            "VALUES(?, 0, ?, 1, 0, ?) "
            "ON CONFLICT(recruiter_id) DO UPDATE SET "
            "  total_stars = total_stars + ?, "
            "  ratings_count = ratings_count + 1, "
            "  last_activity = ?",
            (recruiter_id, stars, int(time.time()), stars, int(time.time())),
        )
        await db.commit()


async def stats_top(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT recruiter_id, ticket_count, total_stars, ratings_count, "
            "total_reaction_time, last_activity FROM stats "
            "WHERE ticket_count > 0 ORDER BY ticket_count DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ============================================================================
# Helpers
# ============================================================================

async def fetch_user_active_ticket_channels(user_id: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT channel_id FROM active_tickets WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]
