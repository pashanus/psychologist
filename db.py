import os
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
_pool: asyncpg.Pool | None = None


async def connect_db() -> asyncpg.Pool:
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=10,
        )
    return _pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool


async def ensure_user(user_id: int, username: Optional[str]) -> None:
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id)
            DO UPDATE SET username = EXCLUDED.username
            """,
            user_id,
            username,
        )


async def get_user(user_id: int) -> Optional[asyncpg.Record]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT user_id, username, test_completed, test_mode, created_at FROM users WHERE user_id = $1",
            user_id,
        )


async def set_test_completed(user_id: int, test_mode: str) -> None:
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET test_completed = TRUE,
                test_mode = $2
            WHERE user_id = $1
            """,
            user_id,
            test_mode,
        )


async def upsert_profile(
    user_id: int,
    introversion: float,
    need_support: float,
    directness: float,
    detail_preference: float,
) -> None:
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_profile (
                user_id, introversion, need_support, directness, detail_preference, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                introversion = EXCLUDED.introversion,
                need_support = EXCLUDED.need_support,
                directness = EXCLUDED.directness,
                detail_preference = EXCLUDED.detail_preference,
                updated_at = NOW()
            """,
            user_id,
            introversion,
            need_support,
            directness,
            detail_preference,
        )


async def get_profile(user_id: int) -> Optional[asyncpg.Record]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT user_id, introversion, need_support, directness, detail_preference, updated_at
            FROM user_profile
            WHERE user_id = $1
            """,
            user_id,
        )


async def save_message(user_id: int, role: str, content: str) -> None:
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO messages (user_id, role, content)
            VALUES ($1, $2, $3)
            """,
            user_id,
            role,
            content,
        )


async def load_recent_messages(user_id: int, limit: int = 12) -> List[Dict[str, str]]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content
            FROM messages
            WHERE user_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )

    # Возвращаем в правильном порядке: от старых к новым
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]