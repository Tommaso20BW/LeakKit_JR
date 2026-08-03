"""Client Telegram condiviso dai quattro monitor."""

from __future__ import annotations

import os
from typing import Any

import requests

from common import log_status


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        dry_run: bool = False,
    ):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.dry_run = dry_run
        if not self.dry_run and (not self.token or not self.chat_id):
            raise RuntimeError(
                "Configura TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nei Secrets"
            )

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        if self.dry_run:
            summary = kwargs.get("json") or kwargs.get("data") or {}
            log_status("TELEGRAM", "DRY-RUN", f"{method}: {summary}")
            return {"ok": True, "result": {}}

        url = f"https://api.telegram.org/bot{self.token}/{method}"
        response = requests.post(url, timeout=45, **kwargs)
        if not response.ok:
            try:
                description = response.json().get("description", response.text)
            except ValueError:
                description = response.text
            raise RuntimeError(
                f"Telegram {method}: HTTP {response.status_code} - {description}"
            )
        return response.json()

    def send_message(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_preview: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self._call("sendMessage", json=payload)

    def send_photo_bytes(
        self,
        content: bytes,
        filename: str,
        caption: str,
        content_type: str = "image/png",
    ) -> dict[str, Any]:
        return self._call(
            "sendPhoto",
            data={"chat_id": self.chat_id, "caption": caption},
            files={"photo": (filename, content, content_type)},
        )

    def send_photo_url(
        self,
        photo_url: str,
        caption: str,
        *,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption[:1024],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self._call("sendPhoto", json=payload)
