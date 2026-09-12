from __future__ import annotations

import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    value = default if raw in (None, "") else int(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    value = default if raw in (None, "") else float(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _safe_sql_identifier(name: str, value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe SQL identifier in {name}: {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_bot_username: str
    mini_app_short_name: str
    mini_app_url: str

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_ssl: str | None

    appuser_table: str
    referral_table: str
    state_db_path: str
    timezone_name: str

    timer_poll_seconds: int
    referral_poll_seconds: int
    referral_reminder_interval_hours: int
    batch_size: int
    send_delay_seconds: float

    enable_timer_notifications: bool
    enable_referral_reminders: bool
    message_language: str

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        bot_username = (
            os.getenv("TELEGRAM_BOT_USERNAME", "Aipolynetbot")
            .strip()
            .lstrip("@")
        )

        short_name = (
            os.getenv("MINI_APP_SHORT_NAME", "app")
            .strip()
            .strip("/")
        )

        if not bot_username:
            raise ValueError("TELEGRAM_BOT_USERNAME is required")

        if not short_name:
            raise ValueError("MINI_APP_SHORT_NAME is required")

        mini_app_url = os.getenv(
            "MINI_APP_URL",
            f"https://t.me/{bot_username}/{short_name}",
        ).strip()

        timezone_name = os.getenv(
            "APP_TIMEZONE",
            "Asia/Tehran"
        ).strip()

        ZoneInfo(timezone_name)

        lang = os.getenv(
            "MESSAGE_LANGUAGE",
            "fa"
        ).strip().lower()

        if lang not in {"fa", "en"}:
            raise ValueError("MESSAGE_LANGUAGE must be fa or en")

        ssl_raw = os.getenv(
            "POSTGRES_SSLMODE",
            ""
        ).strip().lower()

        postgres_ssl = (
            None
            if ssl_raw in {"", "disable", "false", "0"}
            else ssl_raw
        )

        return cls(
            telegram_bot_token=token,
            telegram_bot_username=bot_username,
            mini_app_short_name=short_name,
            mini_app_url=mini_app_url,

            postgres_host=os.getenv(
                "POSTGRES_HOST",
                "db"
            ).strip(),

            postgres_port=_int(
                "POSTGRES_PORT",
                5432,
                1
            ),

            postgres_db=os.getenv(
                "POSTGRES_DB",
                "mydb"
            ).strip(),

            postgres_user=os.getenv(
                "POSTGRES_USER",
                "user"
            ).strip(),

            postgres_password=os.getenv(
                "POSTGRES_PASSWORD",
                "pass"
            ),

            postgres_ssl=postgres_ssl,

            appuser_table=_safe_sql_identifier(
                "APPUSER_TABLE",
                os.getenv(
                    "APPUSER_TABLE",
                    "core_appuser"
                ).strip()
            ),

            referral_table=_safe_sql_identifier(
                "REFERRAL_TABLE",
                os.getenv(
                    "REFERRAL_TABLE",
                    "core_referrallevel"
                ).strip()
            ),

            state_db_path=os.getenv(
                "STATE_DB_PATH",
                "/data/notifier.sqlite3"
            ).strip(),

            timezone_name=timezone_name,

            timer_poll_seconds=_int(
                "TIMER_POLL_SECONDS",
                30,
                5
            ),

            referral_poll_seconds=_int(
                "REFERRAL_POLL_SECONDS",
                60,
                10
            ),

            referral_reminder_interval_hours=_int(
                "REFERRAL_REMINDER_INTERVAL_HOURS",
                5,
                1
            ),

            batch_size=_int(
                "DB_BATCH_SIZE",
                500,
                1
            ),

            send_delay_seconds=_float(
                "SEND_DELAY_SECONDS",
                0.06,
                0.0
            ),

            enable_timer_notifications=_bool(
                "ENABLE_TIMER_NOTIFICATIONS",
                True
            ),

            enable_referral_reminders=_bool(
                "ENABLE_REFERRAL_REMINDERS",
                True
            ),

            message_language=lang,
        )