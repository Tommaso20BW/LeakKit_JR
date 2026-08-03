"""Monitor delle cifre per la personalizzazione delle maglie Juventus."""

from __future__ import annotations

import requests

from common import HEADERS, get_season_label, log_status
from state_store import StateStore
from telegram_client import TelegramClient


FONT_URL = (
    "https://store.juventus.com/images/juventus/customizations/"
    "fonts/{kit}/{number}.png"
)

TOTAL_DIGITS = 10


def get_font_kits() -> list[str]:
    """Ritorna i kit da controllare per la stagione corrente."""
    season = get_season_label()
    return [
        f"HOME-{season}",
        f"AWAY-{season}",
        f"THIRD-{season}",
        f"FOURTH-{season}",
    ]


def _valid_image(response: requests.Response) -> bool:
    """Verifica che la risposta contenga un'immagine valida."""
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
    """Controlla le cifre da 0 a 9 per uno specifico kit."""
    font_state = state.section("fonts")

    if font_state.get(kit):
        log_status("FONT", kit, "già notificato")
        return

    found: list[tuple[str, bytes]] = []
    network_errors = 0

    for n in range(TOTAL_DIGITS):  # 0 → 9
        number = str(n)
        url = FONT_URL.format(
            kit=kit,
            number=number,
        )

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=20,
            )
        except requests.RequestException:
            network_errors += 1
            continue

        if _valid_image(response):
            found.append((number, response.content))

    if not found:
        if network_errors == TOTAL_DIGITS:
            raise RuntimeError(
                f"{kit}: tutte le richieste sono fallite"
            )

        detail = "non disponibile"

        if network_errors:
            detail += f" ({network_errors} errori rete)"

        log_status("FONT", kit, detail)
        return

    telegram.send_message(
        "🚨 LEAK! Le immagini del font "
        f"{kit} della Juventus sono state caricate sullo store! "
        f"({len(found)}/{TOTAL_DIGITS} cifre trovate)\n\n"
        "Te le invio qui sotto 👇"
    )

    for number, content in found:
        telegram.send_photo_bytes(
            content,
            f"{kit}-{number}.png",
            f"Numero {number} — {kit}",
        )

    font_state[kit] = True
    state.save()

    log_status(
        "FONT",
        kit,
        f"notificato ({len(found)}/{TOTAL_DIGITS} immagini)",
    )


def run(
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """Controlla tutti i kit disponibili per la stagione corrente."""
    failures: list[str] = []

    for kit in get_font_kits():
        try:
            check_font_kit(
                kit,
                state,
                telegram,
            )
        except RuntimeError as error:
            failures.append(str(error))
            log_status(
                "FONT",
                kit,
                f"errore: {error}",
            )

    if failures:
        raise RuntimeError("; ".join(failures))
