from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        return con

    async def init(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    kind TEXT NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT NULL,
                    sent_at TEXT NULL,
                    last_error TEXT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, telegram_id, dedupe_key)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_deliveries_updated_at ON deliveries(updated_at)"
            )

    async def can_attempt(self, kind: str, telegram_id: int, dedupe_key: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._can_attempt_sync, kind, telegram_id, dedupe_key
            )

    def _can_attempt_sync(self, kind: str, telegram_id: int, dedupe_key: str) -> bool:
        now = datetime.now(timezone.utc)
        with self._connect() as con:
            row = con.execute(
                """
                SELECT status, next_retry_at
                FROM deliveries
                WHERE kind=? AND telegram_id=? AND dedupe_key=?
                """,
                (kind, telegram_id, dedupe_key),
            ).fetchone()
        if row is None:
            return True
        if row["status"] == "sent":
            return False
        retry_at = row["next_retry_at"]
        if not retry_at:
            return True
        try:
            return datetime.fromisoformat(retry_at) <= now
        except ValueError:
            return True

    async def mark_success(self, kind: str, telegram_id: int, dedupe_key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._mark_success_sync, kind, telegram_id, dedupe_key
            )

    def _mark_success_sync(self, kind: str, telegram_id: int, dedupe_key: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO deliveries (
                    kind, telegram_id, dedupe_key, status, attempts,
                    next_retry_at, sent_at, last_error, updated_at
                ) VALUES (?, ?, ?, 'sent', 1, NULL, ?, NULL, ?)
                ON CONFLICT(kind, telegram_id, dedupe_key) DO UPDATE SET
                    status='sent',
                    attempts=deliveries.attempts + 1,
                    next_retry_at=NULL,
                    sent_at=excluded.sent_at,
                    last_error=NULL,
                    updated_at=excluded.updated_at
                """,
                (kind, telegram_id, dedupe_key, now, now),
            )

    async def mark_failure(
        self,
        kind: str,
        telegram_id: int,
        dedupe_key: str,
        error: str,
        *,
        retry_after_seconds: int | None = None,
        long_backoff: bool = False,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._mark_failure_sync,
                kind,
                telegram_id,
                dedupe_key,
                error,
                retry_after_seconds,
                long_backoff,
            )

    def _mark_failure_sync(
        self,
        kind: str,
        telegram_id: int,
        dedupe_key: str,
        error: str,
        retry_after_seconds: int | None,
        long_backoff: bool,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as con:
            row = con.execute(
                """
                SELECT attempts FROM deliveries
                WHERE kind=? AND telegram_id=? AND dedupe_key=?
                """,
                (kind, telegram_id, dedupe_key),
            ).fetchone()
            attempts = int(row["attempts"]) if row else 0
            next_attempt = attempts + 1

            if retry_after_seconds is not None:
                delay = max(5, int(retry_after_seconds) + 1)
            elif long_backoff:
                delay = 24 * 3600
            else:
                delay = min(3600, 30 * (2 ** min(next_attempt, 7)))

            retry_at = (now + timedelta(seconds=delay)).isoformat()
            now_s = now.isoformat()
            con.execute(
                """
                INSERT INTO deliveries (
                    kind, telegram_id, dedupe_key, status, attempts,
                    next_retry_at, sent_at, last_error, updated_at
                ) VALUES (?, ?, ?, 'failed', ?, ?, NULL, ?, ?)
                ON CONFLICT(kind, telegram_id, dedupe_key) DO UPDATE SET
                    status='failed',
                    attempts=excluded.attempts,
                    next_retry_at=excluded.next_retry_at,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    kind,
                    telegram_id,
                    dedupe_key,
                    next_attempt,
                    retry_at,
                    error[:1000],
                    now_s,
                ),
            )

    async def cleanup(self, days: int = 120) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._cleanup_sync, days)

    def _cleanup_sync(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as con:
            cur = con.execute("DELETE FROM deliveries WHERE updated_at < ?", (cutoff,))
            return cur.rowcount
