"""Monitor delle cifre per la personalizzazione delle maglie Juventus."""

from __future__ import annotations

import requests

from common import HEADERS, log_status
from state_store import StateStore, utc_now
from telegram_client import TelegramClient


FONT_KITS = ["HOME-26-27", "AWAY-26-27", "THIRD-26-27"]
FONT_URL = (
    "https://store.juventus.com/images/juventus/customizations/"
    "fonts/{kit}/{number}.png"
)


def _valid_image(response: requests.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    return (
        response.status_code == 200
        and "image" in content_type
        and "svg" not in content_type
        and len(response.content) > 500
    )


def check_font_kit(
    kit: str,
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    font_state = state.section("fonts")
    if bool(font_state.get(kit, {}).get("notified")):
        log_status("FONT", kit, "già notificato")
        return

    found: list[tuple[int, bytes]] = []
    network_errors = 0
    for number in range(10):
        url = FONT_URL.format(kit=kit, number=number)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException:
            network_errors += 1
            continue
        if _valid_image(response):
            found.append((number, response.content))

    if not found:
        if network_errors == 10:
            raise RuntimeError(f"{kit}: tutte le richieste sono fallite")
        detail = "non disponibile"
        if network_errors:
            detail += f" ({network_errors} errori rete)"
        log_status("FONT", kit, detail)
        return

    telegram.send_message(
        "🚨 LEAK! Le immagini del font "
        f"{kit} della Juventus sono state caricate sullo store! "
        f"({len(found)}/10 cifre trovate)\n\nTe le invio qui sotto 👇"
    )
    for number, content in found:
        telegram.send_photo_bytes(
            content,
            f"{kit}-{number}.png",
            f"Cifra {number} — {kit}",
        )

    font_state[kit] = {
        "notified": True,
        "notified_at": utc_now(),
        "digits_found": [number for number, _ in found],
    }
    state.save()
    log_status("FONT", kit, f"notificato ({len(found)}/10 immagini)")


def run(state: StateStore, telegram: TelegramClient) -> None:
    failures: list[str] = []
    for kit in FONT_KITS:
        try:
            check_font_kit(kit, state, telegram)
        except RuntimeError as error:
            failures.append(str(error))
            log_status("FONT", kit, f"errore: {error}")
    if failures:
        raise RuntimeError("; ".join(failures))
