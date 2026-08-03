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

PRODUCT_URL = (
    "https://store.juventus.com/images/juventus/products/large/"
    "JU{jersey_year}{letter}{code}{suffix}.webp"
)


def get_product_images(
    letter: str,
    code: str,
) -> list[tuple[str, str]]:
    """
    Restituisce i suffissi delle immagini da controllare.

    Per i prodotti da A01 ad A14:
    - immagine principale: nessun suffisso
    - seconda immagine: _d

    Per tutti gli altri prodotti:
    - immagine principale: nessun suffisso
    - seconda immagine: _2
    """
    code_number = int(code)

    if letter == "A" and 1 <= code_number <= 14:
        second_image_suffix = "_d"
    else:
        second_image_suffix = "_2"

    return [
        ("principale", ""),
        ("seconda", second_image_suffix),
    ]


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
    - A01 + A01_d
    - A15 + A15_2
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

    product_images = get_product_images(
        letter=letter,
        code=code,
    )

    found: list[tuple[bytes, str, str, str]] = []
    network_errors = 0

    for image_type, suffix in product_images:
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

        filename = f"JU{jersey_year}{letter}{code}{suffix}.webp"
        caption = f"Codice {letter}{code} — {image_type}"

        found.append(
            (
                content,
                filename,
                caption,
                "image/webp",
            )
        )

    total_requests = len(product_images)

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
