from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TelegramSendError(Exception):
    message: str
    status_code: int | None = None
    retry_after: int | None = None

    def __str__(self) -> str:
        return self.message


class TelegramClient:
    def __init__(self, token: str):
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def get_me(self) -> dict[str, Any]:
        response = await self._client.get(f"{self._base_url}/getMe")
        data = self._decode(response)
        return data["result"]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        for attempt in range(2):
            response = await self._client.post(f"{self._base_url}/sendMessage", json=payload)
            try:
                data = self._decode(response)
                return data["result"]
            except TelegramSendError as exc:
                if exc.status_code == 429 and exc.retry_after and attempt == 0:
                    await asyncio.sleep(exc.retry_after + 1)
                    continue
                raise
        raise TelegramSendError("Telegram send failed after retry")

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:
            raise TelegramSendError(
                f"Invalid Telegram response: HTTP {response.status_code}",
                status_code=response.status_code,
            ) from exc

        if response.is_success and data.get("ok") is True:
            return data

        params = data.get("parameters") or {}
        description = data.get("description") or f"HTTP {response.status_code}"
        raise TelegramSendError(
            description,
            status_code=response.status_code,
            retry_after=params.get("retry_after"),
        )
