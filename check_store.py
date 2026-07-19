"""
Controlla se sono state caricate sullo store Juventus:
  1) le immagini dei font (cifre di personalizzazione) per HOME/AWAY/THIRD 26-27
  2) le immagini prodotto (fronte/retro) 26-27

Se trova qualcosa, invia notifica + immagini su Telegram.
Ogni elemento (kit font / codice prodotto) ha il suo flag: la notifica
di uno non blocca gli altri.
"""

import os
import sys
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# ---------------------------------------------------------------------------
# SEZIONE 1: font (cifre di personalizzazione)
# ---------------------------------------------------------------------------

FONT_KITS = ["HOME-26-27", "AWAY-26-27", "THIRD-26-27"]  # aggiungi qui eventuali kit futuri
FONT_URL = "https://store.juventus.com/images/juventus/customizations/fonts/{kit}/{n}.png"


def tg(method, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def check_font_kit(kit):
    flag_file = f".found-font-{kit}"
    if os.path.exists(flag_file):
        print(f"[FONT {kit}] già notificato, salto.")
        return

    found = []
    for n in range(10):
        url = FONT_URL.format(kit=kit, n=n)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"[FONT {kit}] {n}.png -> errore rete: {e}")
            continue

        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and "image" in ctype and len(r.content) > 500:
            print(f"[FONT {kit}] {n}.png -> TROVATA! ({len(r.content)} byte)")
            found.append((n, r.content))
        else:
            print(f"[FONT {kit}] {n}.png -> non ancora ({r.status_code}, {ctype})")

    if not found:
        print(f"[FONT {kit}] nessuna immagine ancora caricata.")
        return

    tg("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": (
            f"🚨 LEAK! Le immagini del font {kit} della Juventus "
            f"sono state caricate sullo store! ({len(found)}/10 cifre trovate)\n\n"
            "Te le invio qui sotto 👇"
        ),
    })

    for n, content in found:
        tg("sendPhoto",
           data={"chat_id": CHAT_ID, "caption": f"Cifra {n} — {kit}"},
           files={"photo": (f"{kit}-{n}.png", content, "image/png")})

    with open(flag_file, "w") as f:
        f.write("notified\n")
    print(f"[FONT {kit}] notifica inviata, flag creato.")


# ---------------------------------------------------------------------------
# SEZIONE 2: immagini prodotto (fronte/retro)
# ---------------------------------------------------------------------------

# La lettera "A" è fissa per questa stagione (unico esempio confermato: JU26A07_d.webp).
# Se in futuro cambia stagione/lettera, aggiorna qui.
PRODUCT_LETTER = "A"

# codice -> nome (stile coerente con FONT_KITS)
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

PRODUCT_URL = "https://store.juventus.com/images/juventus/products/small/JU26{letter}{code}{suffix}.webp"


def fetch_image(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"  -> errore rete: {e}")
        return None
    ctype = r.headers.get("Content-Type", "")
    if r.status_code == 200 and "image" in ctype and len(r.content) > 500:
        print(f"  -> TROVATA! ({len(r.content)} byte)")
        return r.content
    print(f"  -> non ancora ({r.status_code}, {ctype})")
    return None


def check_product(code, name):
    flag_file = f".found-product-{code}"
    if os.path.exists(flag_file):
        print(f"[PRODUCT {name}] già notificato, salto.")
        return

    found = {}

    front_url = PRODUCT_URL.format(letter=PRODUCT_LETTER, code=code, suffix="")
    print(f"[PRODUCT {name}] fronte: {front_url}")
    content = fetch_image(front_url)
    if content:
        found["fronte"] = content

    back_url = PRODUCT_URL.format(letter=PRODUCT_LETTER, code=code, suffix="_d")
    print(f"[PRODUCT {name}] retro: {back_url}")
    content = fetch_image(back_url)
    if content:
        found["retro"] = content

    if not found:
        print(f"[PRODUCT {name}] nessuna immagine ancora caricata.")
        return

    tg("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": (
            f"🚨 LEAK! Immagine prodotto {name} della Juventus "
            f"è stata caricata sullo store! ({len(found)}/2 lati trovati)\n\n"
            "Te la invio qui sotto 👇"
        ),
    })

    for side, content in found.items():
        tg("sendPhoto",
           data={"chat_id": CHAT_ID, "caption": f"{name} — {side}"},
           files={"photo": (f"JU26{PRODUCT_LETTER}{code}-{side}.png", content, "image/png")})

    with open(flag_file, "w") as f:
        f.write("notified\n")
    print(f"[PRODUCT {name}] notifica inviata, flag creato.")


# ---------------------------------------------------------------------------

def main():
    for kit in FONT_KITS:
        check_font_kit(kit)
    for code, name in PRODUCTS.items():
        check_product(code, name)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)
