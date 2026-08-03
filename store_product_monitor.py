"""Monitor delle immagini fronte/retro dei prodotti nello store Juventus."""

from __future__ import annotations

import requests

from common import HEADERS, log_status
from state_store import StateStore, utc_now
from telegram_client import TelegramClient


PRODUCT_LETTER = "A"
PRODUCTS = {
    "01": "HOME-26-27-REPLICA",
    "02": "AWAY-26-27-REPLICA",
    "03": "THIRD-26-27-REPLICA",
    "04": "HOME-26-27-AUTHENTIC",
    "05": "AWAY-26-27-AUTHENTIC",
    "06": "THIRD-26-27-AUTHENTIC",
    "07": "HOME-26-27-MANICHE-LUNGHE",
    "08": "AWAY-26-27-MANICHE-LUNGHE",
    "09": "GK-26-27",
}
PRODUCT_URL = (
    "https://store.juventus.com/images/juventus/products/small/"
    "JU26{letter}{code}{suffix}.webp"
)


def fetch_image(url: str) -> tuple[bytes | None, bool]:
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
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
    name: str,
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    product_state = state.section("store_products")
    if bool(product_state.get(code, {}).get("notified")):
        log_status("PRODUCT", name, "già notificato")
        return

    found: dict[str, bytes] = {}
    network_errors = 0
    for side, suffix in (("fronte", ""), ("retro", "_d")):
        url = PRODUCT_URL.format(
            letter=PRODUCT_LETTER,
            code=code,
            suffix=suffix,
        )
        content, network_error = fetch_image(url)
        network_errors += int(network_error)
        if content:
            found[side] = content

    if not found:
        if network_errors == 2:
            raise RuntimeError(f"{name}: entrambe le richieste sono fallite")
        detail = "non disponibile"
        if network_errors:
            detail += f" ({network_errors} errori rete)"
        log_status("PRODUCT", name, detail)
        return

    telegram.send_message(
        f"🚨 LEAK! Immagine prodotto {name} della Juventus caricata "
        f"sullo store! ({len(found)}/2 lati trovati)\n\n"
        "Te la invio qui sotto 👇"
    )
    for side, image_content in found.items():
        telegram.send_photo_bytes(
            image_content,
            f"JU26{PRODUCT_LETTER}{code}-{side}.webp",
            f"{name} — {side}",
            "image/webp",
        )

    product_state[code] = {
        "notified": True,
        "notified_at": utc_now(),
        "sides_found": list(found),
    }
    state.save()
    log_status("PRODUCT", name, f"notificato ({len(found)}/2 immagini)")


def run(state: StateStore, telegram: TelegramClient) -> None:
    failures: list[str] = []
    for code, name in PRODUCTS.items():
        try:
            check_product(code, name, state, telegram)
        except RuntimeError as error:
            failures.append(str(error))
            log_status("PRODUCT", name, f"errore: {error}")
    if failures:
        raise RuntimeError("; ".join(failures))
