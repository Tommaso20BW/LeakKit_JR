"""
Juve Leak Bot 🦓

Controlla se sono state caricate sullo store Juventus:
1) le immagini dei font (cifre di personalizzazione) per HOME/AWAY/THIRD 26-27
2) le immagini prodotto (fronte/retro) 26-27
3) nuovi articoli sulla Juventus su Footy Headlines (leak, release, notizie kit)

Se trova qualcosa, invia notifica (+ immagini, quando previste) su Telegram.
Ogni elemento ha il suo stato: la notifica di uno non blocca gli altri.
"""

import json
import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def tg(method, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# SEZIONE 1: font (cifre di personalizzazione)
# ---------------------------------------------------------------------------

FONT_KITS = ["HOME-26-27", "AWAY-26-27", "THIRD-26-27"]
FONT_URL = (
    "https://store.juventus.com/images/juventus/customizations/"
    "fonts/{kit}/{n}.png"
)


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

    tg(
        "sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": (
                f"🚨 LEAK! Le immagini del font {kit} della Juventus "
                f"sono state caricate sullo store! ({len(found)}/10 cifre trovate)\n\n"
                "Te le invio qui sotto 👇"
            ),
        },
    )
    for n, content in found:
        tg(
            "sendPhoto",
            data={"chat_id": CHAT_ID, "caption": f"Cifra {n} — {kit}"},
            files={"photo": (f"{kit}-{n}.png", content, "image/png")},
        )

    with open(flag_file, "w") as f:
        f.write("notified\n")
    print(f"[FONT {kit}] notifica inviata, flag creato.")


# ---------------------------------------------------------------------------
# SEZIONE 2: immagini prodotto (fronte/retro)
# ---------------------------------------------------------------------------

# La lettera "A" è fissa per questa stagione
# (unico esempio confermato: JU26A07_d.webp).
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

    front_url = PRODUCT_URL.format(
        letter=PRODUCT_LETTER,
        code=code,
        suffix="",
    )
    print(f"[PRODUCT {name}] controllo fronte:")
    content = fetch_image(front_url)
    if content:
        found["fronte"] = content

    back_url = PRODUCT_URL.format(
        letter=PRODUCT_LETTER,
        code=code,
        suffix="_d",
    )
    print(f"[PRODUCT {name}] controllo retro:")
    content = fetch_image(back_url)
    if content:
        found["retro"] = content

    if not found:
        print(f"[PRODUCT {name}] nessuna immagine ancora caricata.")
        return

    tg(
        "sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": (
                f"🚨 LEAK! Immagine prodotto {name} della Juventus "
                f"è stata caricata sullo store! ({len(found)}/2 lati trovati)\n\n"
                "Te la invio qui sotto 👇"
            ),
        },
    )
    for side, content in found.items():
        tg(
            "sendPhoto",
            data={"chat_id": CHAT_ID, "caption": f"{name} — {side}"},
            files={
                "photo": (
                    f"JU26{PRODUCT_LETTER}{code}-{side}.png",
                    content,
                    "image/png",
                )
            },
        )

    with open(flag_file, "w") as f:
        f.write("notified\n")
    print(f"[PRODUCT {name}] notifica inviata, flag creato.")


# ---------------------------------------------------------------------------
# SEZIONE 3: notizie Footy Headlines
# ---------------------------------------------------------------------------

NEWS_TEAM_URL = "https://www.footyheadlines.com/team/Juventus"
NEWS_SEEN_FILE = ".seen_news.json"
NEWS_MAX_SEEN = 300

# Un articolo Footy Headlines ha sempre un URL che finisce in .html
# (es. /0694254978/titolo.html oppure /2025/08/titolo.html). Il sito usa
# sia URL assoluti sia relativi: vengono normalizzati prima del controllo.
NEWS_URL_RE = re.compile(
    r"^https://www\.footyheadlines\.com/.+\.html$",
    re.IGNORECASE,
)


def load_seen_news():
    if os.path.exists(NEWS_SEEN_FILE):
        with open(NEWS_SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_news(seen_list):
    with open(NEWS_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(
            seen_list[-NEWS_MAX_SEEN:],
            f,
            ensure_ascii=False,
            indent=2,
        )


def fetch_news_articles():
    """Ritorna gli articoli nell'ordine della pagina, senza duplicati."""
    r = requests.get(NEWS_TEAM_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    articles = []
    urls_done = set()

    # Nel template attuale il link contiene l'h2 (non il contrario), perciò
    # bisogna risalire al tag <a>. Limitiamo la ricerca alle classi dei titoli
    # per non raccogliere link .html estranei presenti in menu e widget.
    headlines = soup.select(
        "h2.post-feed__item-headline, "
        "h2.simple-post-feed__item-headline"
    )
    for h2 in headlines:
        a = h2.find_parent("a", href=True)
        if not a:
            continue

        url = urljoin(NEWS_TEAM_URL, a["href"].strip())
        url = url.split("#", 1)[0].split("?", 1)[0]
        if not NEWS_URL_RE.match(url) or url in urls_done:
            continue
        urls_done.add(url)

        title = h2.get_text(" ", strip=True)
        snippet = ""
        content = h2.find_parent("div", class_="post-feed__item-content")
        if content:
            paragraph = (
                content.select_one(".content-teaser p")
                or content.select_one(".content-full p")
            )
            if paragraph:
                snippet = paragraph.get_text(" ", strip=True)
                snippet = re.sub(r"\s*More\s*$", "", snippet).strip()

        articles.append(
            {
                "url": url,
                "title": title,
                "snippet": snippet,
            }
        )

    return articles


def check_news():
    seen = load_seen_news()
    try:
        articles = fetch_news_articles()
    except requests.RequestException as e:
        print(f"[NEWS] errore rete: {e}")
        return

    if not articles:
        print("[NEWS] nessun articolo trovato in pagina (possibile cambio di template).")
        return

    new_articles = [a for a in articles if a["url"] not in seen]
    new_articles.reverse()

    if not new_articles:
        print("[NEWS] nessuna notizia nuova.")
        return

    for art in new_articles:
        text = f"📰 *{art['title']}*\n"
        if art["snippet"]:
            text += f"\n{art['snippet']}\n"
        text += f"\n{art['url']}"

        tg(
            "sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
        )
        print(f"[NEWS] notificato: {art['title']}")
        seen.add(art["url"])

    save_seen_news(list(seen))


# ---------------------------------------------------------------------------


def main():
    for kit in FONT_KITS:
        check_font_kit(kit)
    for code, name in PRODUCTS.items():
        check_product(code, name)
    check_news()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)
