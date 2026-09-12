from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import asyncpg

from .config import Settings

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        delay = 1
        last_error: Exception | None = None
        for attempt in range(1, 31):
            try:
                self.pool = await asyncpg.create_pool(
                    host=self.settings.postgres_host,
                    port=self.settings.postgres_port,
                    database=self.settings.postgres_db,
                    user=self.settings.postgres_user,
                    password=self.settings.postgres_password,
                    ssl=self.settings.postgres_ssl,
                    min_size=1,
                    max_size=5,
                    command_timeout=30,
                )
                await self.ping()
                logger.info("Connected to PostgreSQL host=%s db=%s", self.settings.postgres_host, self.settings.postgres_db)
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Database connection attempt %s failed: %s", attempt, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)
        raise RuntimeError(f"Could not connect to PostgreSQL: {last_error}")

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def ping(self) -> bool:
        if self.pool is None:
            return False
        value = await self.pool.fetchval("SELECT 1")
        return value == 1

    async def iter_due_timer_users(self) -> AsyncIterator[asyncpg.Record]:
        if self.pool is None:
            raise RuntimeError("Database is not connected")

        last_id = 0
        table = self.settings.appuser_table
        while True:
            rows = await self.pool.fetch(
                f"""
                SELECT id, telegram_id, telegram_username, referral_code, next_daily_claim_at
                FROM {table}
                WHERE id > $1
                  AND telegram_id IS NOT NULL
                  AND is_active = TRUE
                  AND is_telegram_user = TRUE
                  AND next_daily_claim_at IS NOT NULL
                  AND next_daily_claim_at <= NOW()
                ORDER BY id ASC
                LIMIT $2
                """,
                last_id,
                self.settings.batch_size,
            )
            if not rows:
                break
            for row in rows:
                yield row
                last_id = int(row["id"])
            if len(rows) < self.settings.batch_size:
                break

    async def iter_referral_progress(self) -> AsyncIterator[asyncpg.Record]:
        if self.pool is None:
            raise RuntimeError("Database is not connected")

        last_id = 0
        user_table = self.settings.appuser_table
        referral_table = self.settings.referral_table

        while True:
            rows = await self.pool.fetch(
                f"""
                SELECT
                    u.id,
                    u.telegram_id,
                    u.telegram_username,
                    u.referral_code,
                    COALESCE(r.level_1_count, 0) AS level_1_count,
                    COALESCE(r.level_2_count, 0) AS level_2_count,
                    COALESCE(r.level_3_count, 0) AS level_3_count,
                    COALESCE(r.level_4_count, 0) AS level_4_count,
                    COALESCE(r.level_5_count, 0) AS level_5_count
                FROM {user_table} AS u
                LEFT JOIN {referral_table} AS r ON r.user_id = u.id
                WHERE u.id > $1
                  AND u.telegram_id IS NOT NULL
                  AND u.is_active = TRUE
                  AND u.is_telegram_user = TRUE
                ORDER BY u.id ASC
                LIMIT $2
                """,
                last_id,
                self.settings.batch_size,
            )
            if not rows:
                break
            for row in rows:
                yield row
                last_id = int(row["id"])
            if len(rows) < self.settings.batch_size:
                break
