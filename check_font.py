"""
Controlla se le immagini dei font (AWAY e THIRD 26-27) sono state caricate
sullo store Juventus. Se le trova, invia notifica + immagini su Telegram.
Ogni kit ha il suo flag: la notifica di uno non blocca l'altro.
"""

import os
import sys
import requests

KITS = ["HOME-26-27", "AWAY-26-27", "THIRD-26-27"]  # aggiungi qui eventuali kit futuri
BASE_URL = "https://store.juventus.com/images/juventus/customizations/fonts/{kit}/{n}.png"

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


def check_kit(kit):
    flag_file = f".found-{kit}"
    if os.path.exists(flag_file):
        print(f"[{kit}] già notificato, salto.")
        return

    found = []
    for n in range(10):
        url = BASE_URL.format(kit=kit, n=n)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"[{kit}] {n}.png -> errore rete: {e}")
            continue

        # deve essere 200 E un'immagine vera (non pagina 404 mascherata)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and "image" in ctype and len(r.content) > 500:
            print(f"[{kit}] {n}.png -> TROVATA! ({len(r.content)} byte)")
            found.append((n, r.content))
        else:
            print(f"[{kit}] {n}.png -> non ancora ({r.status_code}, {ctype})")

    if not found:
        print(f"[{kit}] nessuna immagine ancora caricata.")
        return

    # Messaggio di avviso
    tg("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": (
            f"🚨 LEAK! Le immagini del font {kit} della Juventus "
            f"sono state caricate sullo store! ({len(found)}/10 cifre trovate)\n\n"
            "Te le invio qui sotto 👇"
        ),
    })

    # Invio le immagini come documenti (qualità originale PNG)
    for n, content in found:
        tg("sendDocument",
           data={"chat_id": CHAT_ID, "caption": f"Cifra {n} — {kit}"},
           files={"document": (f"{kit}-{n}.png", content, "image/png")})

    # Creo il flag: il workflow lo committerà per fermare i run futuri
    with open(flag_file, "w") as f:
        f.write("notified\n")
    print(f"[{kit}] notifica inviata, flag creato.")


def main():
    for kit in KITS:
        check_kit(kit)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)
