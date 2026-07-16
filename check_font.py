"""
Controlla se le immagini del font AWAY 26-27 sono state caricate
sullo store Juventus. Se le trova, invia notifica + immagini su Telegram.
"""

import os
import sys
import requests

BASE_URL = "https://store.juventus.com/images/juventus/customizations/fonts/AWAY-26-27/{}.png"
FLAG_FILE = ".found"  # se esiste, abbiamo già notificato

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def tg(method, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def main():
    if os.path.exists(FLAG_FILE):
        print("Già notificato in precedenza, esco.")
        return

    found = []
    for n in range(10):
        url = BASE_URL.format(n)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"{n}.png -> errore rete: {e}")
            continue

        # deve essere 200 E un'immagine vera (non pagina 404 mascherata)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and "image" in ctype and len(r.content) > 500:
            print(f"{n}.png -> TROVATA! ({len(r.content)} byte)")
            found.append((n, r.content))
        else:
            print(f"{n}.png -> non ancora ({r.status_code}, {ctype})")

    if not found:
        print("Nessuna immagine ancora caricata.")
        return

    # Messaggio di avviso
    tg("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": (
            "🚨 LEAK! Le immagini del font AWAY 26-27 della Juventus "
            f"sono state caricate sullo store! ({len(found)}/10 cifre trovate)\n\n"
            "Te le invio qui sotto 👇"
        ),
    })

    # Invio le immagini come documenti (qualità originale PNG)
    for n, content in found:
        tg("sendDocument",
           data={"chat_id": CHAT_ID, "caption": f"Cifra {n} — AWAY 26-27"},
           files={"document": (f"{n}.png", content, "image/png")})

    # Creo il flag: il workflow lo committerà per fermare i run futuri
    with open(FLAG_FILE, "w") as f:
        f.write("notified\n")
    print("Notifica inviata, flag creato.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)
