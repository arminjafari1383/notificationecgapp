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
        return [[{"text": "🚀 ورود به Mini App", "url": self.settings.mini_app_url}]]

    def referral_link(self, referral_code: str) -> str:
        code = quote(f"ref_{referral_code}", safe="")
        return (
            f"https://t.me/{self.settings.telegram_bot_username}/"
            f"{self.settings.mini_app_short_name}?startapp={code}"
        )

    def referral_buttons(self, referral_code: str) -> list[list[dict[str, str]]]:
        link = self.referral_link(referral_code)
        if self.settings.message_language == "fa":
            share_text = "به AI POLIFY بپیوند و با لینک Referral من شروع کن"
            share_button = "👥 ارسال لینک دعوت"
            app_button = "🚀 ورود به Mini App"
        else:
            share_text = "Join AI POLIFY with my referral link"
            share_button = "👥 Share referral link"
            app_button = "🚀 Open Mini App"
        share_url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(share_text, safe='')}"
        return [
            [{"text": share_button, "url": share_url}],
            [{"text": app_button, "url": self.settings.mini_app_url}],
        ]

    def timer_message(self) -> str:
        if self.settings.message_language == "fa":
            return (
                "⛏️ تایمر Mining تمام شد!\n\n"
                "یک ساعت کامل شد. وارد برنامه شو و روی «Claim 100 EPL» بزن "
                "تا سیکل 100 EPL بعدی شروع شود."
            )
        return (
            "⛏️ Mining timer finished!\n\n"
            "Your 1-hour cycle is complete. Open the app and tap “Claim 100 EPL” "
            "to start the next 100 EPL cycle."
        )

    def referral_message(self, next_level: int) -> str:
        if self.settings.message_language == "fa":
            if next_level == 1:
                return (
                    "🎁 اولین Referral تو هنوز ثبت نشده.\n\n"
                    "لینک دعوتت را برای یک دوست بفرست. با ثبت اولین Referral مستقیم، "
                    "1000 EPL پاداش می‌گیری."
                )
            return (
                f"👥 شبکه Referral تو تا Level {next_level - 1} فعال شده.\n\n"
                f"برای فعال شدن Level {next_level} و ثبت Referral در این سطح، "
                "500 EPL پاداش می‌گیری. لینک دعوتت را برای گسترش شبکه بفرست."
            )

        if next_level == 1:
            return (
                "🎁 Your first referral has not been registered yet.\n\n"
                "Share your invite link with a friend. Your first direct referral earns you 1000 EPL."
            )
        return (
            f"👥 Your referral network is active through Level {next_level - 1}.\n\n"
            f"When Level {next_level} gets its first referral, you earn 500 EPL. "
            "Share your invite link to keep growing the network."
        )

    @staticmethod
    def _next_empty_level(row) -> int | None:
        for level in range(1, 6):
            if int(row[f"level_{level}_count"] or 0) <= 0:
                return level
        return None

    async def _deliver(
        self,
        *,
        kind: str,
        telegram_id: int,
        dedupe_key: str,
        text: str,
        keyboard: list[list[dict[str, str]]],
    ) -> bool:
        if not await self.state.can_attempt(kind, telegram_id, dedupe_key):
            return False

        try:
            await self.telegram.send_message(
                telegram_id,
                text,
                inline_keyboard=keyboard,
            )
            await self.state.mark_success(kind, telegram_id, dedupe_key)
            if self.settings.send_delay_seconds:
                await asyncio.sleep(self.settings.send_delay_seconds)
            return True
        except TelegramSendError as exc:
            self.failures += 1
            # 403 commonly means the bot was blocked or has no permission to message the user.
            long_backoff = exc.status_code == 403
            await self.state.mark_failure(
                kind,
                telegram_id,
                dedupe_key,
                str(exc),
                retry_after_seconds=exc.retry_after,
                long_backoff=long_backoff,
            )
            logger.warning(
                "Telegram message failed kind=%s telegram_id=%s status=%s error=%s",
                kind,
                telegram_id,
                exc.status_code,
                exc,
            )
            return False
        except Exception as exc:
            self.failures += 1
            await self.state.mark_failure(kind, telegram_id, dedupe_key, str(exc))
            logger.exception(
                "Unexpected message failure kind=%s telegram_id=%s",
                kind,
                telegram_id,
            )
            return False

    async def scan_timers(self) -> int:
        sent = 0
        async for row in self.db.iter_due_timer_users():
            telegram_id = int(row["telegram_id"])
            next_at = row["next_daily_claim_at"]
            if next_at is None:
                continue
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            # The cycle's server timestamp is the stable dedupe key.
            dedupe_key = next_at.astimezone(timezone.utc).isoformat()
            if await self._deliver(
                kind="timer_finished",
                telegram_id=telegram_id,
                dedupe_key=dedupe_key,
                text=self.timer_message(),
                keyboard=self.mini_app_button(),
            ):
                sent += 1
                self.timer_sent += 1

        self.last_timer_scan_at = datetime.now(timezone.utc).isoformat()
        return sent

    async def scan_referrals(self) -> int:
        now_local = datetime.now(self.settings.timezone)
        target_minutes = (
            self.settings.referral_reminder_hour * 60
            + self.settings.referral_reminder_minute
        )
        current_minutes = now_local.hour * 60 + now_local.minute

        # The poll can run late after restart; allow reminders any time after the configured clock time.
        if current_minutes < target_minutes:
            self.last_referral_scan_at = datetime.now(timezone.utc).isoformat()
            return 0

        date_key = now_local.date().isoformat()
        sent = 0
        async for row in self.db.iter_referral_progress():
            next_level = self._next_empty_level(row)
            if next_level is None:
                # All five levels already contain at least one referral; the daily progression goal is complete.
                continue

            referral_code = str(row["referral_code"] or "").strip()
            if not referral_code:
                continue

            telegram_id = int(row["telegram_id"])
            if await self._deliver(
                kind="referral_daily",
                telegram_id=telegram_id,
                dedupe_key=date_key,
                text=self.referral_message(next_level),
                keyboard=self.referral_buttons(referral_code),
            ):
                sent += 1
                self.referral_sent += 1

        self.last_referral_scan_at = datetime.now(timezone.utc).isoformat()
        return sent

    async def timer_loop(self) -> None:
        while True:
            try:
                await self.scan_timers()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Timer scan failed")
            await asyncio.sleep(self.settings.timer_poll_seconds)

    async def referral_loop(self) -> None:
        cleanup_day: str | None = None
        while True:
            try:
                await self.scan_referrals()
                today = datetime.now(self.settings.timezone).date().isoformat()
                if cleanup_day != today:
                    await self.state.cleanup()
                    cleanup_day = today
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Referral scan failed")
            await asyncio.sleep(self.settings.referral_poll_seconds)
