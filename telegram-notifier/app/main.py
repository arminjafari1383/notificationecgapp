from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings
from .db import Database
from .notifier import NotifierService
from .state import StateStore
from .telegram import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = Settings.from_env()
state = StateStore(settings.state_db_path)
db = Database(settings)
telegram = TelegramClient(settings.telegram_bot_token)
notifier = NotifierService(settings, db, state, telegram)
_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await state.init()
    await db.connect()

    try:
        bot = await telegram.get_me()
        logger.info("Telegram bot connected: @%s", bot.get("username"))
    except Exception:
        # Keep the service alive; transient Telegram failures are retried by the worker loops.
        logger.exception("Telegram getMe check failed")

    if settings.enable_timer_notifications:
        _tasks.append(asyncio.create_task(notifier.timer_loop(), name="timer-loop"))
    if settings.enable_referral_reminders:
        _tasks.append(asyncio.create_task(notifier.referral_loop(), name="referral-loop"))

    try:
        yield
    finally:
        for task in _tasks:
            task.cancel()
        await asyncio.gather(*_tasks, return_exceptions=True)
        _tasks.clear()
        await telegram.close()
        await db.close()


app = FastAPI(
    title="AI POLIFY Telegram Notifier",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "service": "telegram-notifier",
        "status": "running",
        "port": 5000,
        "timer_notifications": settings.enable_timer_notifications,
        "referral_reminders": settings.enable_referral_reminders,
    }


@app.get("/health")
async def health():
    try:
        db_ok = await db.ping()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": False, "error": str(exc)},
        )

    return {
        "status": "ok" if db_ok else "unhealthy",
        "database": db_ok,
        "timer_sent": notifier.timer_sent,
        "referral_sent": notifier.referral_sent,
        "failures": notifier.failures,
        "last_timer_scan_at": notifier.last_timer_scan_at,
        "last_referral_scan_at": notifier.last_referral_scan_at,
    }
