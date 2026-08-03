"""Monitor delle immagini dei prodotti nello store Juventus."""

from __future__ import annotations

import requests

from common import HEADERS, get_jersey_year, log_status
from state_store import StateStore
from telegram_client import TelegramClient


# Codici prodotto da 00 a 99.
PRODUCT_CODES = [f"{n:02d}" for n in range(100)]  # 00 → 99

# Varianti prodotto presenti negli URL dello store.
PRODUCT_LETTERS = ["A", "B"]

PRODUCT_URL = (
    "https://store.juventus.com/images/juventus/products/large/"
    "JU{jersey_year}{letter}{code}{suffix}.webp"
)


def fetch_image(url: str) -> tuple[bytes | None, bool]:
    """Scarica un'immagine e segnala eventuali errori di rete."""
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


def check_product(
    code: str,
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """Controlla le immagini principali e _2 delle varianti A e B."""
    jersey_year = get_jersey_year()

    # La chiave include l'anno, così ogni nuova stagione viene ricontrollata.
    state_key = f"{jersey_year}_{code}"
    product_state = state.section("store_products")

    if product_state.get(state_key):
        log_status("PRODUCT", code, "già notificato")
        return

    found: dict[str, bytes] = {}
    network_errors = 0

    for letter in PRODUCT_LETTERS:
        for image_type, suffix in (
            ("principale", ""),
            ("seconda", "_2"),
        ):
            url = PRODUCT_URL.format(
                jersey_year=jersey_year,
                letter=letter,
                code=code,
                suffix=suffix,
            )

            content, network_error = fetch_image(url)
            network_errors += int(network_error)

            if content:
                found[f"{letter}-{image_type}"] = content

    total_requests = len(PRODUCT_LETTERS) * 2

    if not found:
        if network_errors == total_requests:
            raise RuntimeError(
                f"{code}: tutte le richieste sono fallite"
            )

        detail = "non disponibile"

        if network_errors:
            detail += f" ({network_errors} errori rete)"

        log_status("PRODUCT", code, detail)
        return

    telegram.send_message(
        f"🚨 LEAK! Immagine prodotto {code} della Juventus caricata "
        f"sullo store! ({len(found)}/{total_requests} immagini trovate)\n\n"
        "Te le invio qui sotto 👇"
    )

    for key, image_content in found.items():
        letter, image_type = key.split("-", 1)
        suffix = "_2" if image_type == "seconda" else ""

        filename = (
            f"JU{jersey_year}{letter}{code}{suffix}.webp"
        )

        telegram.send_photo_bytes(
            image_content,
            filename,
            f"Codice {code} — variante {letter} — {image_type}",
            "image/webp",
        )

    product_state[state_key] = True
    state.save()

    log_status(
        "PRODUCT",
        code,
        f"notificato ({len(found)}/{total_requests} immagini)",
    )


def run(
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """Controlla tutti i codici prodotto da 00 a 99."""
    failures: list[str] = []

    for code in PRODUCT_CODES:
        try:
            check_product(
                code,
                state,
                telegram,
            )
        except RuntimeError as error:
            failures.append(str(error))
            log_status(
                "PRODUCT",
                code,
                f"errore: {error}",
            )

    if failures:
        raise RuntimeError("; ".join(failures))
