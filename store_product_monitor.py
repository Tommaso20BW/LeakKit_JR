"""Monitor delle immagini dei prodotti nello store Juventus."""

from __future__ import annotations

import requests

from common import HEADERS, get_jersey_year, log_status
from state_store import StateStore
from telegram_client import TelegramClient


# Codici prodotto da 00 a 99.
PRODUCT_CODES = [f"{n:02d}" for n in range(100)]

# Varianti prodotto da controllare separatamente.
PRODUCT_LETTERS = ["A", "B"]

# Immagine principale e seconda immagine.
PRODUCT_IMAGES = [
    ("principale", ""),
    ("seconda", "_2"),
]

PRODUCT_URL = (
    "https://store.juventus.com/images/juventus/products/large/"
    "JU{jersey_year}{letter}{code}{suffix}.webp"
)


def fetch_image(url: str) -> tuple[bytes | None, bool]:
    """Scarica un'immagine e indica se si è verificato un errore di rete."""
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
        )
    except requests.RequestException:
        return None, True

    content_type = response.headers.get("Content-Type", "").lower()

    if (
        response.status_code == 200
        and "image" in content_type
        and "svg" not in content_type
        and len(response.content) > 500
    ):
        return response.content, False

    return None, False


def check_product_variant(
    code: str,
    letter: str,
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """
    Controlla separatamente una variante prodotto.

    Esempi:
    - A01 + A01_2
    - B01 + B01_2
    """
    jersey_year = get_jersey_year()

    # A e B vengono salvate separatamente.
    state_key = f"{jersey_year}_{letter}_{code}"
    product_state = state.section("store_products")

    if product_state.get(state_key):
        log_status(
            "PRODUCT",
            f"{letter}{code}",
            "già notificato",
        )
        return

    found: list[tuple[bytes, str, str, str]] = []
    network_errors = 0

    for image_type, suffix in PRODUCT_IMAGES:
        url = PRODUCT_URL.format(
            jersey_year=jersey_year,
            letter=letter,
            code=code,
            suffix=suffix,
        )

        content, network_error = fetch_image(url)
        network_errors += int(network_error)

        if content is None:
            continue

        filename = (
            f"JU{jersey_year}{letter}{code}{suffix}.webp"
        )

        caption = (
            f"Codice {letter}{code} — {image_type}"
        )

        found.append(
            (
                content,
                filename,
                caption,
                "image/webp",
            )
        )

    total_requests = len(PRODUCT_IMAGES)

    if network_errors == total_requests:
        raise RuntimeError(
            f"{letter}{code}: tutte le richieste sono fallite"
        )

    if not found:
        detail = "non disponibile"

        if network_errors:
            detail += f" ({network_errors} errori rete)"

        log_status(
            "PRODUCT",
            f"{letter}{code}",
            detail,
        )
        return

    # Aspetta entrambe le immagini prima di inviare l'album.
    # In questo modo A01 e A01_2 vengono sempre raggruppate.
    if len(found) < total_requests:
        detail = (
            f"{len(found)}/{total_requests} immagini disponibili, "
            "attendo il caricamento completo"
        )

        if network_errors:
            detail += f" ({network_errors} errori rete)"

        log_status(
            "PRODUCT",
            f"{letter}{code}",
            detail,
        )
        return

    telegram.send_message(
        f"🚨 LEAK! Immagini prodotto {letter}{code} della Juventus "
        "caricate sullo store!\n\n"
        "Te le invio qui sotto 👇"
    )

    # Un solo album con:
    # JU26A01.webp + JU26A01_2.webp
    # oppure:
    # JU26B01.webp + JU26B01_2.webp
    telegram.send_media_group_bytes(found)

    product_state[state_key] = True
    state.save()

    log_status(
        "PRODUCT",
        f"{letter}{code}",
        f"notificato ({len(found)}/{total_requests} immagini)",
    )


def check_product(
    code: str,
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """Controlla separatamente le varianti A e B dello stesso codice."""
    failures: list[str] = []

    for letter in PRODUCT_LETTERS:
        try:
            check_product_variant(
                code=code,
                letter=letter,
                state=state,
                telegram=telegram,
            )
        except RuntimeError as error:
            failures.append(str(error))

            log_status(
                "PRODUCT",
                f"{letter}{code}",
                f"errore: {error}",
            )

    if failures:
        raise RuntimeError("; ".join(failures))


def run(
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """Controlla tutti i codici da 00 a 99."""
    failures: list[str] = []

    for code in PRODUCT_CODES:
        try:
            check_product(
                code=code,
                state=state,
                telegram=telegram,
            )
        except RuntimeError as error:
            failures.append(str(error))

    if failures:
        raise RuntimeError("; ".join(failures))
