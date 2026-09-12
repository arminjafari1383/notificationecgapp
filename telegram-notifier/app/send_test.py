from __future__ import annotations

import asyncio
import sys

from .config import Settings
from .telegram import TelegramClient


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.send_test <telegram_id>")

    telegram_id = int(sys.argv[1])
    settings = Settings.from_env()
    client = TelegramClient(settings.telegram_bot_token)
    try:
        result = await client.send_message(
            telegram_id,
            "✅ The notification service is successfully connected to the Telegram bot.",
            inline_keyboard=[[
                {"text": "🚀 Open Mini App", "url": settings.mini_app_url}
            ]],
        )
        print(f"sent message_id={result.get('message_id')}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
