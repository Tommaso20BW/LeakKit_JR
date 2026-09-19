"""Client per l'invio di messaggi e immagini tramite Telegram Bot API."""

from __future__ import annotations

import json
import os
import re
import time
from html import escape, unescape
from typing import Any

import requests


NEWS_MEDIA_MAX_ATTEMPTS = 5
NEWS_TEXT_MAX_ATTEMPTS = 3


class TelegramClient:
    """Client per comunicare con Telegram, con supporto dry-run."""

    API_BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | int | None = None,
        dry_run: bool = False,
        rich_messages: bool | None = None,
    ) -> None:
        """
        Inizializza il client Telegram.

        In modalità dry-run non vengono effettuati invii reali e non sono
        richiesti token o chat ID.

        I Rich Messages sono abilitati con TELEGRAM_RICH_MESSAGES=1 oppure
        passando rich_messages=True. I metodi legacy restano invariati e
        vengono usati come fallback dai monitor.
        """
        self.dry_run = dry_run

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
        self.rich_messages = (
            self._env_flag("TELEGRAM_RICH_MESSAGES", default=True)
            if rich_messages is None
            else bool(rich_messages)
        )

        if not self.dry_run:
            if not self.token:
                raise ValueError(
                    "Token Telegram mancante. Imposta TELEGRAM_BOT_TOKEN."
                )
            if self.chat_id is None or not str(self.chat_id).strip():
                raise ValueError(
                    "Chat ID Telegram mancante. Imposta TELEGRAM_CHAT_ID."
                )

        self.session = requests.Session()

    @staticmethod
    def _env_flag(name: str, *, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() not in {"", "0", "false", "no", "off"}

    def _api_url(self, method: str) -> str:
        """Restituisce l'URL completo del metodo Telegram."""
        if not self.token:
            raise RuntimeError("Token Telegram non disponibile.")

        return f"{self.API_BASE_URL}/bot{self.token}/{method}"

    def _post(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: int = 60,
    ) -> Any:
        """Esegue una richiesta POST verso Telegram."""
        if self.dry_run:
            print(
                f"[DRY RUN][TELEGRAM] {method} "
                f"data={data or {}} "
                f"files={list(files or {})}"
            )
            return None

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
            retry_after = payload.get("parameters", {}).get("retry_after")

            if retry_after is not None:
                description += f"; riprovare tra {retry_after} secondi"

            raise RuntimeError(f"Telegram {method}: {description}")

        return payload.get("result")

    def send_message(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_preview: bool | None = None,
        disable_web_page_preview: bool = True,
    ) -> Any:
        """Invia un messaggio di testo."""
        if not text.strip():
            raise ValueError(
                "Il messaggio Telegram non può essere vuoto."
            )

        if disable_preview is not None:
            disable_web_page_preview = disable_preview

        if self.dry_run:
            print(
                "[DRY RUN][TELEGRAM MESSAGE]\n"
                f"parse_mode={parse_mode!r}\n"
                f"disable_preview={disable_web_page_preview}\n"
                f"{text}"
            )
            return None

        data: dict[str, Any] = {
            "chat_id": str(self.chat_id),
            "text": text,
            "link_preview_options": json.dumps(
                {
                    "is_disabled": disable_web_page_preview,
                }
            ),
        }

        if parse_mode:
            data["parse_mode"] = parse_mode

        return self._post(
            "sendMessage",
            data=data,
            timeout=30,
        )

    @staticmethod
    def _news_rich_html(
        *,
        title: str,
        description: str,
        url: str,
        image_url: str = "",
        is_update: bool = False,
    ) -> str:
        """Crea il Rich Message editoriale usato dalle notizie Footy Headlines."""
        source = "Footy Headlines · Aggiornamento" if is_update else "Footy Headlines"
        source_emoji = "🔄" if is_update else "📰"
        parts = [
            f"<h2>{source_emoji} {escape(source)}</h2>",
            f"<h4>{escape(title.strip())}</h4>",
        ]
        if image_url.strip():
            parts.append(
                f'<img src="{escape(image_url.strip(), quote=True)}"/>'
            )
        if description.strip():
            parts.append(
                f"<p>{escape(description.strip()).replace(chr(10), '<br>')}</p>"
            )
        parts.append(
            '<tg-button-row align="left">'
            f'<tg-button type="url" style="primary" '
            f'url="{escape(url.strip(), quote=True)}">'
            'Apri notizia'
            '</tg-button>'
            '</tg-button-row>'
        )
        return "".join(parts)

    def _send_news_rich_with_retries(
        self,
        rich_message: dict[str, Any],
        *,
        attempts: int,
    ) -> Any:
        """Invia un Rich Message ritentando gli errori temporanei."""
        last_error: RuntimeError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._post(
                    "sendRichMessage",
                    data={
                        "chat_id": str(self.chat_id),
                        "rich_message": json.dumps(
                            rich_message,
                            ensure_ascii=False,
                        ),
                    },
                    timeout=60,
                )
            except RuntimeError as error:
                last_error = error
                if attempt == attempts:
                    break
                time.sleep(min(2 ** (attempt - 1), 10))

        if last_error is not None:
            raise last_error
        raise RuntimeError("Invio Rich Message Footy Headlines fallito.")

    def send_news_rich_message(
        self,
        *,
        title: str,
        description: str,
        url: str,
        image_url: str = "",
        is_update: bool = False,
    ) -> Any:
        """Invia Footy Headlines come Rich Message, senza fallback legacy.

        Se è presente un'immagine, Telegram ha fino a 5 tentativi per
        recuperarla. Se continua a fallire, la notizia viene inviata comunque
        nello stesso formato Rich Message, ma senza media.
        """
        if not title.strip():
            raise ValueError("Il titolo Footy Headlines non può essere vuoto.")
        if not url.strip():
            raise ValueError("L'URL Footy Headlines non può essere vuoto.")

        if self.dry_run:
            print(
                "[DRY RUN][FOOTY HEADLINES RICH]\n"
                f"title={title!r}\n"
                f"description={description!r}\n"
                f"url={url!r}\n"
                f"image_url={image_url!r}\n"
                f"is_update={is_update!r}"
            )
            return None

        def payload(current_image_url: str) -> dict[str, str]:
            return {
                "html": self._news_rich_html(
                    title=title,
                    description=description,
                    url=url,
                    image_url=current_image_url,
                    is_update=is_update,
                )
            }

        if image_url.strip():
            try:
                return self._send_news_rich_with_retries(
                    payload(image_url),
                    attempts=NEWS_MEDIA_MAX_ATTEMPTS,
                )
            except RuntimeError as error:
                print(
                    "[TELEGRAM RICH] immagine Footy Headlines non disponibile "
                    f"dopo {NEWS_MEDIA_MAX_ATTEMPTS} tentativi ({error}); "
                    "invio la notizia senza media, sempre in Rich Message."
                )

        return self._send_news_rich_with_retries(
            payload(""),
            attempts=NEWS_TEXT_MAX_ATTEMPTS,
        )

    def send_rich_gallery_bytes(
        self,
        *,
        heading: str,
        body: str,
        images: list[tuple[bytes, str, str, str]],
        footer: str = "",
    ) -> Any:
        """Invia testo + 1-50 immagini in un singolo Rich Message.

        Con più immagini usa uno slideshow: Telegram mostra un solo media
        alla volta e l'utente può scorrere gli altri. Con una sola immagine
        mantiene un normale blocco foto.

        Ogni immagine usa la stessa tupla del vecchio send_media_group_bytes:
        (contenuto, nome_file, didascalia, mime_type).

        Il metodo viene chiamato dai monitor solo se rich_messages=True.
        In caso di errore il monitor esegue il fallback legacy, quindi lo
        stato e la logica di rilevamento non cambiano.
        """
        if not self.rich_messages:
            raise RuntimeError("Rich Messages disabilitati")
        if not images:
            raise ValueError("Un Rich Message gallery richiede almeno un'immagine.")
        if len(images) > 50:
            raise ValueError("Un Rich Message può contenere al massimo 50 media.")

        files: dict[str, tuple[str, bytes, str]] = {}
        media: list[dict[str, Any]] = []
        image_tags: list[str] = []

        for index, (content, filename, _caption, mime_type) in enumerate(images):
            if not content:
                raise ValueError(f"Il file {filename!r} non contiene dati.")
            if not filename:
                raise ValueError(f"Nome file mancante per l'immagine {index + 1}.")

            attachment_name = f"rich_media_{index}"
            media_id = f"photo_{index}"
            files[attachment_name] = (filename, content, mime_type)
            media.append(
                {
                    "id": media_id,
                    "media": {
                        "type": "photo",
                        "media": f"attach://{attachment_name}",
                    },
                }
            )
            image_tags.append(f'<img src="tg://photo?id={media_id}"/>')

        if len(image_tags) == 1:
            media_block = image_tags[0]
        else:
            media_block = f"<tg-slideshow>{''.join(image_tags)}</tg-slideshow>"

        html_parts = [
            f"<h2>{escape(heading)}</h2>",
            f"<p>{escape(body).replace(chr(10), '<br>')}</p>",
            media_block,
        ]
        if footer:
            html_parts.append(
                f"<footer>{escape(footer).replace(chr(10), '<br>')}</footer>"
            )

        rich_message = {
            "html": "".join(html_parts),
            "media": media,
        }

        if self.dry_run:
            print(
                "[DRY RUN][TELEGRAM RICH GALLERY]\n"
                f"heading={heading!r}\n"
                f"body={body!r}\n"
                f"footer={footer!r}\n"
                f"images={len(images)}"
            )
            return None

        return self._post(
            "sendRichMessage",
            data={
                "chat_id": str(self.chat_id),
                "rich_message": json.dumps(
                    rich_message,
                    ensure_ascii=False,
                ),
            },
            files=files,
            timeout=120,
        )

    @staticmethod
    def _timestamp_rich_metadata(caption: str) -> tuple[str, str, str] | None:
        """Estrae i dati dal caption legacy usato dal timestamp scanner."""
        if "Nuovo asset Juventus" not in caption:
            return None

        link_match = re.search(r"href=[\"']([^\"']+)[\"']", caption)
        url = unescape(link_match.group(1)) if link_match else ""
        plain = re.sub(r"<[^>]+>", "", caption)
        plain = unescape(plain)
        lines = [line.strip() for line in plain.splitlines() if line.strip()]
        body_lines = [
            line
            for line in lines
            if "Nuovo asset Juventus" not in line and "Apri immagine" not in line
        ]
        body = "\n".join(body_lines)
        return "🚨 Nuovo asset Juventus", body, url

    def send_photo_bytes(
        self,
        content: bytes,
        filename: str,
        caption: str = "",
        mime_type: str = "image/jpeg",
        parse_mode: str | None = None,
    ) -> Any:
        """Invia una singola immagine da contenuto binario.

        Per le notifiche del timestamp scanner, quando i Rich Messages sono
        attivi prova prima sendRichMessage e torna automaticamente a sendPhoto
        in caso di errore. Gli altri chiamanti mantengono il comportamento
        legacy, così i fallback dei monitor font/prodotti non vengono alterati.
        """
        if not content:
            raise ValueError(
                f"Il file {filename!r} non contiene dati."
            )

        if not filename:
            raise ValueError(
                "Il nome del file non può essere vuoto."
            )

        timestamp_metadata = self._timestamp_rich_metadata(caption)
        if self.rich_messages and timestamp_metadata is not None:
            heading, body, url = timestamp_metadata
            try:
                return self.send_rich_gallery_bytes(
                    heading=heading,
                    body=body,
                    images=[(content, filename, caption, mime_type)],
                    footer=url,
                )
            except RuntimeError as exc:
                print(
                    "[TELEGRAM RICH FALLBACK] Rich Message timestamp non "
                    f"disponibile ({exc}). Uso sendPhoto legacy."
                )

        if self.dry_run:
            print(
                f"[DRY RUN][TELEGRAM PHOTO] "
                f"file={filename} "
                f"bytes={len(content)} "
                f"mime={mime_type} "
                f"parse_mode={parse_mode!r} "
                f"caption={caption!r}"
            )
            return None

        data: dict[str, Any] = {
            "chat_id": str(self.chat_id),
        }

        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode

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

    def send_document_bytes(
        self,
        content: bytes,
        filename: str,
        caption: str = "",
        mime_type: str = "application/octet-stream",
        parse_mode: str | None = None,
    ) -> Any:
        """Invia un file originale come documento Telegram."""
        if not content:
            raise ValueError(
                f"Il file {filename!r} non contiene dati."
            )

        if not filename:
            raise ValueError(
                "Il nome del file non può essere vuoto."
            )

        if self.dry_run:
            print(
                f"[DRY RUN][TELEGRAM DOCUMENT] "
                f"file={filename} "
                f"bytes={len(content)} "
                f"mime={mime_type} "
                f"parse_mode={parse_mode!r} "
                f"caption={caption!r}"
            )
            return None

        data: dict[str, Any] = {
            "chat_id": str(self.chat_id),
        }

        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode

        return self._post(
            "sendDocument",
            data=data,
            files={
                "document": (
                    filename,
                    content,
                    mime_type,
                ),
            },
            timeout=120,
        )

    def send_media_group_bytes(
        self,
        images: list[tuple[bytes, str, str, str]],
    ) -> Any:
        """
        Invia da 2 a 10 immagini come album Telegram.

        Ogni elemento deve contenere:
        (
            contenuto,
            nome_file,
            didascalia,
            mime_type,
        )
        """
        if not 2 <= len(images) <= 10:
            raise ValueError(
                "Un album Telegram deve contenere da 2 a 10 immagini."
            )

        if self.dry_run:
            print(
                f"[DRY RUN][TELEGRAM ALBUM] "
                f"{len(images)} immagini"
            )
            for index, (
                content,
                filename,
                caption,
                mime_type,
            ) in enumerate(images, start=1):
                print(
                    f"  {index}. "
                    f"file={filename} "
                    f"bytes={len(content)} "
                    f"mime={mime_type} "
                    f"caption={caption!r}"
                )

            return None

        media: list[dict[str, str]] = []
        files: dict[str, tuple[str, bytes, str]] = {}

        for index, (
            content,
            filename,
            caption,
            mime_type,
        ) in enumerate(images):
            if not content:
                raise ValueError(
                    f"Il file {filename!r} non contiene dati."
                )
            if not filename:
                raise ValueError(
                    f"Nome file mancante per l'immagine {index + 1}."
                )

            attachment_name = f"media_{index}"

            media_item: dict[str, str] = {
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
        """Permette l'utilizzo tramite context manager."""
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Chiude la sessione quando termina il context manager."""
        self.close()
