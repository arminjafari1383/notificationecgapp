from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from .config import Settings
from .db import Database
from .state import StateStore
from .telegram import TelegramClient, TelegramSendError


logger = logging.getLogger(__name__)


class NotifierService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        state: StateStore,
        telegram: TelegramClient,
    ):
        self.settings = settings
        self.db = db
        self.state = state
        self.telegram = telegram

        self.timer_sent = 0
        self.referral_sent = 0
        self.failures = 0

        self.last_timer_scan_at: str | None = None
        self.last_referral_scan_at: str | None = None

    def mini_app_button(self) -> list[list[dict[str, str]]]:
        return [
            [
                {
                    "text": "🚀 Open Mini App",
                    "url": self.settings.mini_app_url,
                }
            ]
        ]

    def referral_link(self, referral_code: str) -> str:
        code = quote(
            f"ref_{referral_code}",
            safe=""
        )

        return (
            f"https://t.me/"
            f"{self.settings.telegram_bot_username}/"
            f"{self.settings.mini_app_short_name}"
            f"?startapp={code}"
        )

    def referral_buttons(
        self,
        referral_code: str
    ) -> list[list[dict[str, str]]]:

        link = self.referral_link(referral_code)

        share_text = "Join AI POLIFY with my referral link"
        share_button = "👥 Share referral link"
        app_button = "🚀 Open Mini App"

        share_url = (
            "https://t.me/share/url"
            f"?url={quote(link, safe='')}"
            f"&text={quote(share_text, safe='')}"
        )

        return [
            [
                {
                    "text": share_button,
                    "url": share_url,
                }
            ],
            [
                {
                    "text": app_button,
                    "url": self.settings.mini_app_url,
                }
            ],
        ]

    def timer_message(self) -> str:
        return (
            "⛏️ Mining timer finished!\n\n"
            "Your 1-hour cycle is complete. "
            "Open the app and tap Claim 100 EPL to receive your next 100 EPL."
        )

    @staticmethod
    def _empty_levels(row) -> list[int]:
        """Return only referral positions that are still incomplete."""

        return [
            level
            for level in range(1, 6)
            if int(
                row[f"level_{level}_count"] or 0
            ) <= 0
        ]

    @staticmethod
    def _level_reward(level: int) -> int:
        """Return the reward for a referral position.

        Position 1 = 1,000 EPL.
        Positions 2-5 = 500 EPL each.
        """

        if level == 1:
            return 1000

        return 500

    def referral_message(
        self,
        empty_levels: list[int]
    ) -> str:

        lines = [
            "🎯 Your empty referral positions",
            "",
            "These positions are still incomplete:",
            "",
        ]

        for level in empty_levels:
            reward = self._level_reward(level)

            lines.append(
                f"▫️ Position {level} "
                f"— Reward: {reward:,} EPL"
            )

        lines.extend(
            [
                "",
                "👥 Invite friends to complete these positions.",
                "",
                "🎁 You receive the reward for each position as soon as it is completed.",
                "",
                "✅ Completed positions will no longer appear in future notifications.",
                "",
                "Share your referral link with your friends 👇",
            ]
        )

        return "\n".join(lines)

    async def _deliver(
        self,
        *,
        kind: str,
        telegram_id: int,
        dedupe_key: str,
        text: str,
        keyboard: list[list[dict[str, str]]],
    ) -> bool:

        can_attempt = await self.state.can_attempt(
            kind,
            telegram_id,
            dedupe_key,
        )

        if not can_attempt:
            return False

        try:

            await self.telegram.send_message(
                telegram_id,
                text,
                inline_keyboard=keyboard,
            )

            await self.state.mark_success(
                kind,
                telegram_id,
                dedupe_key,
            )

            if self.settings.send_delay_seconds:
                await asyncio.sleep(
                    self.settings.send_delay_seconds
                )

            return True

        except TelegramSendError as exc:

            self.failures += 1

            long_backoff = (
                exc.status_code == 403
            )

            await self.state.mark_failure(
                kind,
                telegram_id,
                dedupe_key,
                str(exc),
                retry_after_seconds=exc.retry_after,
                long_backoff=long_backoff,
            )

            logger.warning(
                "Telegram message failed "
                "kind=%s telegram_id=%s "
                "status=%s error=%s",
                kind,
                telegram_id,
                exc.status_code,
                exc,
            )

            return False

        except Exception as exc:

            self.failures += 1

            await self.state.mark_failure(
                kind,
                telegram_id,
                dedupe_key,
                str(exc),
            )

            logger.exception(
                "Unexpected message failure "
                "kind=%s telegram_id=%s",
                kind,
                telegram_id,
            )

            return False

    async def scan_timers(self) -> int:

        sent = 0

        async for row in self.db.iter_due_timer_users():

            telegram_id = int(
                row["telegram_id"]
            )

            next_at = row[
                "next_daily_claim_at"
            ]

            if next_at is None:
                continue

            if next_at.tzinfo is None:

                next_at = next_at.replace(
                    tzinfo=timezone.utc
                )

            dedupe_key = (
                next_at
                .astimezone(timezone.utc)
                .isoformat()
            )

            result = await self._deliver(
                kind="timer_finished",
                telegram_id=telegram_id,
                dedupe_key=dedupe_key,
                text=self.timer_message(),
                keyboard=self.mini_app_button(),
            )

            if result:
                sent += 1
                self.timer_sent += 1

        self.last_timer_scan_at = (
            datetime.now(timezone.utc)
            .isoformat()
        )

        return sent

    async def scan_referrals(self) -> int:

        now_utc = datetime.now(
            timezone.utc
        )

        interval_seconds = (
            self.settings
            .referral_reminder_interval_hours
            * 3600
        )

        bucket = int(
            now_utc.timestamp()
            // interval_seconds
        )

        dedupe_key = (
            f"{self.settings.referral_reminder_interval_hours}"
            f"h:{bucket}"
        )

        sent = 0

        async for row in (
            self.db.iter_referral_progress()
        ):

            empty_levels = (
                self._empty_levels(row)
            )

            #
            # Do not send a reminder when all referral positions are complete.
            #
            if not empty_levels:
                continue

            referral_code = str(
                row["referral_code"] or ""
            ).strip()

            if not referral_code:
                continue

            telegram_id = int(
                row["telegram_id"]
            )

            result = await self._deliver(
                kind="referral_positions",
                telegram_id=telegram_id,
                dedupe_key=dedupe_key,
                text=self.referral_message(
                    empty_levels
                ),
                keyboard=self.referral_buttons(
                    referral_code
                ),
            )

            if result:
                sent += 1
                self.referral_sent += 1

        self.last_referral_scan_at = (
            now_utc.isoformat()
        )

        return sent

    async def timer_loop(self) -> None:

        while True:

            try:

                if (
                    self.settings
                    .enable_timer_notifications
                ):
                    await self.scan_timers()

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Timer scan failed"
                )

            await asyncio.sleep(
                self.settings
                .timer_poll_seconds
            )

    async def referral_loop(self) -> None:

        cleanup_day: str | None = None

        while True:

            try:

                if (
                    self.settings
                    .enable_referral_reminders
                ):

                    await self.scan_referrals()

                today = (
                    datetime.now(
                        self.settings.timezone
                    )
                    .date()
                    .isoformat()
                )

                if cleanup_day != today:

                    await self.state.cleanup()

                    cleanup_day = today

            except asyncio.CancelledError:
                raise

            except Exception:

                logger.exception(
                    "Referral scan failed"
                )

            await asyncio.sleep(
                self.settings
                .referral_poll_seconds
            )