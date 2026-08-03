"""Client per l'invio di messaggi e immagini tramite Telegram Bot API."""

from __future__ import annotations

import json
import os
from typing import Any

import requests


class TelegramClient:
    """Client essenziale per comunicare con Telegram."""

    API_BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | int | None = None,
    ) -> None:
        """
        Inizializza il client Telegram.

        Se token e chat_id non vengono passati direttamente, vengono letti
        dalle variabili d'ambiente:

        - TELEGRAM_BOT_TOKEN oppure TELEGRAM_TOKEN
        - TELEGRAM_CHAT_ID
        """
        self.token = (
            token
            or os.getenv("TELEGRAM_BOT_TOKEN")
            or os.getenv("TELEGRAM_TOKEN")
        )

        self.chat_id = (
            chat_id
            if chat_id is not None
            else os.getenv("TELEGRAM_CHAT_ID")
        )

        if not self.token:
            raise ValueError(
                "Token Telegram mancante. Imposta TELEGRAM_BOT_TOKEN "
                "oppure TELEGRAM_TOKEN."
            )

        if self.chat_id is None or str(self.chat_id).strip() == "":
            raise ValueError(
                "Chat ID Telegram mancante. Imposta TELEGRAM_CHAT_ID."
            )

        self.session = requests.Session()

    def _api_url(self, method: str) -> str:
        """Restituisce l'URL completo di un metodo Telegram."""
        return f"{self.API_BASE_URL}/bot{self.token}/{method}"

    def _post(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: int = 60,
    ) -> Any:
        """Esegue una richiesta POST e controlla la risposta Telegram."""
        try:
            response = self.session.post(
                self._api_url(method),
                data=data,
                files=files,
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise RuntimeError(
                f"Errore di rete Telegram durante {method}: {error}"
            ) from error

        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(
                f"Risposta Telegram non valida durante {method}: "
                f"HTTP {response.status_code}"
            ) from error

        if not response.ok or not payload.get("ok"):
            description = payload.get(
                "description",
                f"errore HTTP {response.status_code}",
            )

            retry_after = (
                payload.get("parameters", {}).get("retry_after")
            )

            if retry_after is not None:
                description += (
                    f" — riprovare tra {retry_after} secondi"
                )

            raise RuntimeError(
                f"Telegram {method}: {description}"
            )

        return payload.get("result")

    def send_message(
        self,
        text: str,
        *,
        disable_web_page_preview: bool = True,
    ) -> Any:
        """Invia un messaggio di testo."""
        if not text.strip():
            raise ValueError("Il messaggio Telegram non può essere vuoto.")

        return self._post(
            "sendMessage",
            data={
                "chat_id": str(self.chat_id),
                "text": text,
                "link_preview_options": json.dumps(
                    {
                        "is_disabled": disable_web_page_preview,
                    }
                ),
            },
            timeout=30,
        )

    def send_photo_bytes(
        self,
        content: bytes,
        filename: str,
        caption: str = "",
        mime_type: str = "image/jpeg",
    ) -> Any:
        """Invia una singola immagine da contenuto binario."""
        if not content:
            raise ValueError(
                f"Il file {filename!r} non contiene dati."
            )

        if not filename:
            raise ValueError("Il nome del file non può essere vuoto.")

        data: dict[str, Any] = {
            "chat_id": str(self.chat_id),
        }

        if caption:
            data["caption"] = caption

        return self._post(
            "sendPhoto",
            data=data,
            files={
                "photo": (
                    filename,
                    content,
                    mime_type,
                ),
            },
            timeout=60,
        )

    def send_media_group_bytes(
        self,
        images: list[tuple[bytes, str, str, str]],
    ) -> Any:
        """
        Invia da 2 a 10 immagini come album Telegram.

        Ogni elemento della lista deve contenere:

        (
            contenuto_binario,
            nome_file,
            didascalia,
            mime_type,
        )

        Esempio:

        [
            (
                image_a,
                "JU26A01.webp",
                "Codice A01 — principale",
                "image/webp",
            ),
            (
                image_a2,
                "JU26A01_2.webp",
                "Codice A01 — seconda",
                "image/webp",
            ),
        ]
        """
        if not 2 <= len(images) <= 10:
            raise ValueError(
                "Un album Telegram deve contenere da 2 a 10 immagini."
            )

        media: list[dict[str, str]] = {}
        media = []

        files: dict[str, tuple[str, bytes, str]] = {}

        for index, item in enumerate(images):
            if len(item) != 4:
                raise ValueError(
                    "Ogni immagine deve contenere: "
                    "content, filename, caption e mime_type."
                )

            content, filename, caption, mime_type = item

            if not content:
                raise ValueError(
                    f"Il file {filename!r} non contiene dati."
                )

            if not filename:
                raise ValueError(
                    f"Nome file mancante per l'immagine {index + 1}."
                )

            attachment_name = f"media_{index}"

            media_item = {
                "type": "photo",
                "media": f"attach://{attachment_name}",
            }

            if caption:
                media_item["caption"] = caption

            media.append(media_item)

            files[attachment_name] = (
                filename,
                content,
                mime_type,
            )

        return self._post(
            "sendMediaGroup",
            data={
                "chat_id": str(self.chat_id),
                "media": json.dumps(
                    media,
                    ensure_ascii=False,
                ),
            },
            files=files,
            timeout=120,
        )

    def close(self) -> None:
        """Chiude la sessione HTTP."""
        self.session.close()

    def __enter__(self) -> TelegramClient:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()
