"""
Juve Leak Bot 🦓

Controlla se sono state caricate sullo store Juventus:
1) le immagini dei font (cifre di personalizzazione) per HOME/AWAY/THIRD 26-27
2) le immagini prodotto (fronte/retro) 26-27
3) nuovi articoli sulla Juventus su Footy Headlines (leak, release, notizie kit)

Se trova qualcosa, invia notifica (+ immagini, quando previste) su Telegram.
Ogni elemento ha il suo stato: la notifica di uno non blocca gli altri.

Tutto lo stato (font trovati, prodotti trovati, news viste) è salvato
in un unico Gist GitHub invece che su file locali.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID        = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# GIST — stato centralizzato
# ---------------------------------------------------------------------------
GIST_ID_LEAK    = "cfabe2c5157ac77d3beb68ef86f04a5a"
GIST_STATE_FILE = "leak_state.json"   # nome del file dentro il Gist
NEWS_MAX_SEEN   = 300


def _gist_headers() -> dict:
    token = os.environ.get("GIST_TOKEN", "")
    if not token:
        raise RuntimeError("Secret mancante: configura GIST_TOKEN.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_state() -> dict:
    """
    Carica lo stato completo dal Gist.
    Struttura restituita:
    {
        "found_fonts":    ["HOME-26-27", ...],   # kit già notificati
        "found_products": ["01", "04", ...],     # codici prodotto già notificati
        "seen_news":      ["https://...", ...]   # URL notizie già notificati
    }
    """
    url = f"https://api.github.com/gists/{GIST_ID_LEAK}"
    try:
        response = requests.get(url, headers=_gist_headers(), timeout=20)
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(
            f"Impossibile leggere il Gist ({GIST_ID_LEAK}): {error}"
        ) from error

    files = response.json().get("files", {})

    if GIST_STATE_FILE not in files:
        # Prima esecuzione: stato vuoto
        return {"found_fonts": [], "found_products": [], "seen_news": []}

    raw_url = files[GIST_STATE_FILE].get("raw_url", "")
    if not raw_url:
        return {"found_fonts": [], "found_products": [], "seen_news": []}

    try:
        raw = requests.get(raw_url, timeout=20)
        raw.raise_for_status()
        data = raw.json()
    except (requests.RequestException, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Stato non leggibile dal Gist: {error}"
        ) from error

    # Assicura che tutte le chiavi esistano (compatibilità con stati vecchi)
    data.setdefault("found_fonts",    [])
    data.setdefault("found_products", [])
    data.setdefault("seen_news",      [])
    return data


def save_state(state: dict) -> None:
    """Sovrascrive il file di stato nel Gist."""
    # Deduplicazione e troncamento seen_news
    state["seen_news"] = list(dict.fromkeys(state["seen_news"]))[-NEWS_MAX_SEEN:]

    content = json.dumps(state, ensure_ascii=False, indent=2)
    url     = f"https://api.github.com/gists/{GIST_ID_LEAK}"
    payload = {"files": {GIST_STATE_FILE: {"content": content}}}

    try:
        response = requests.patch(
            url, headers=_gist_headers(), json=payload, timeout=20
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(
            f"Impossibile aggiornare il Gist ({GIST_ID_LEAK}): {error}"
        ) from error


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------
def tg(method, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r   = requests.post(url, timeout=30, **kwargs)
    if not r.ok:
        try:
            description = r.json().get("description", r.text)
        except ValueError:
            description = r.text
        raise RuntimeError(
            f"Telegram {method}: HTTP {r.status_code} - {description}"
        )
    return r.json()


# ---------------------------------------------------------------------------
# SEZIONE 1: font (cifre di personalizzazione)
# ---------------------------------------------------------------------------
FONT_KITS = ["HOME-26-27", "AWAY-26-27", "THIRD-26-27"]

FONT_URL = (
    "https://store.juventus.com/images/juventus/customizations/"
    "fonts/{kit}/{n}.png"
)


def check_font_kit(kit: str, state: dict) -> bool:
    """
    Controlla il kit font. Restituisce True se lo stato è stato modificato
    (così il chiamante sa che deve salvare il Gist).
    """
    if kit in state["found_fonts"]:
        print(f"[FONT {kit}] già notificato, salto.")
        return False

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
        return False

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

    state["found_fonts"].append(kit)
    print(f"[FONT {kit}] notifica inviata, stato aggiornato nel Gist.")
    return True


# ---------------------------------------------------------------------------
# SEZIONE 2: immagini prodotto (fronte/retro)
# ---------------------------------------------------------------------------
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


def fetch_image(url: str):
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


def check_product(code: str, name: str, state: dict) -> bool:
    """
    Controlla il prodotto. Restituisce True se lo stato è stato modificato.
    """
    if code in state["found_products"]:
        print(f"[PRODUCT {name}] già notificato, salto.")
        return False

    found = {}

    front_url = PRODUCT_URL.format(letter=PRODUCT_LETTER, code=code, suffix="")
    print(f"[PRODUCT {name}] controllo fronte:")
    content = fetch_image(front_url)
    if content:
        found["fronte"] = content

    back_url = PRODUCT_URL.format(letter=PRODUCT_LETTER, code=code, suffix="_d")
    print(f"[PRODUCT {name}] controllo retro:")
    content = fetch_image(back_url)
    if content:
        found["retro"] = content

    if not found:
        print(f"[PRODUCT {name}] nessuna immagine ancora caricata.")
        return False

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

    state["found_products"].append(code)
    print(f"[PRODUCT {name}] notifica inviata, stato aggiornato nel Gist.")
    return True


# ---------------------------------------------------------------------------
# SEZIONE 3: notizie Footy Headlines
# ---------------------------------------------------------------------------
NEWS_TEAM_URL   = "https://www.footyheadlines.com/team/Juventus"
NEWS_MAX_AGE_DAYS = 10
NEWS_TIMEZONE   = ZoneInfo("Europe/Rome")

NEWS_URL_RE = re.compile(
    r"^https://www\.footyheadlines\.com/.+\.html$",
    re.IGNORECASE,
)
NEWS_ABSOLUTE_DATE_RE = re.compile(r"\b([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\b")
NEWS_RELATIVE_DATE_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
NEWS_IMAGE_DATE_RE    = re.compile(r"/static/img/post/(\d{4})/(\d{2})/(\d{2})/")


def parse_news_date(text, today):
    text     = text.strip()
    absolute = NEWS_ABSOLUTE_DATE_RE.search(text)
    if absolute:
        try:
            return datetime.strptime(absolute.group(1), "%b %d, %Y").date()
        except ValueError:
            return None

    relative = NEWS_RELATIVE_DATE_RE.match(text)
    if not relative:
        return None
    amount = int(relative.group(1))
    unit   = relative.group(2).lower()
    if unit in {"m", "h"}:
        days = 0
    elif unit == "d":
        days = amount
    else:
        days = amount * 7
    return today - timedelta(days=days)


def extract_news_date(item, content, today):
    metadata = None
    if content:
        metadata = content.find("div", class_="post-feed__item-meta", recursive=False)
    if metadata:
        for meta in metadata.select(".post-feed__item-meta-el"):
            published = parse_news_date(meta.get_text(" ", strip=True), today)
            if published:
                return published

    image_date = NEWS_IMAGE_DATE_RE.search(str(item))
    if image_date:
        try:
            return datetime(
                int(image_date.group(1)),
                int(image_date.group(2)),
                int(image_date.group(3)),
            ).date()
        except ValueError:
            return None
    return None


def fetch_news_articles():
    r = requests.get(NEWS_TEAM_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    articles    = []
    urls_done   = set()
    today       = datetime.now(NEWS_TIMEZONE).date()
    oldest_allowed = today - timedelta(days=NEWS_MAX_AGE_DAYS)

    headlines = soup.select(
        "h2.post-feed__item-headline, h2.simple-post-feed__item-headline"
    )
    for h2 in headlines:
        item = h2.find_parent(
            "div", class_=re.compile(r"^(?:simple-)?post-feed__item$")
        )
        if not item:
            continue
        a = h2.find_parent("a", href=True)
        if not a:
            continue

        url = urljoin(NEWS_TEAM_URL, a["href"].strip())
        url = url.split("#", 1)[0].split("?", 1)[0]
        if not NEWS_URL_RE.match(url) or url in urls_done:
            continue

        content   = h2.find_parent("div", class_="post-feed__item-content")
        published = extract_news_date(item, content, today)
        if not published or not (oldest_allowed <= published <= today):
            continue

        urls_done.add(url)
        title   = h2.get_text(" ", strip=True)
        snippet = ""
        if content:
            paragraph = (
                content.select_one(".content-teaser p")
                or content.select_one(".content-full p")
            )
            if paragraph:
                snippet = paragraph.get_text(" ", strip=True)
                snippet = re.sub(r"\s*More\s*$", "", snippet).strip()

        articles.append(
            {"url": url, "title": title, "snippet": snippet, "published": published.isoformat()}
        )
    return articles


def check_news(state: dict) -> bool:
    """
    Controlla le notizie Footy Headlines.
    Restituisce True se lo stato è stato modificato.
    """
    seen     = set(state["seen_news"])
    modified = False

    print(f"[NEWS] stato caricato: {len(seen)} articoli già notificati.")

    try:
        articles = fetch_news_articles()
    except requests.RequestException as e:
        print(f"[NEWS] errore rete: {e}")
        return False

    if not articles:
        print("[NEWS] nessun articolo trovato in pagina (possibile cambio di template).")
        return False

    new_articles = [a for a in articles if a["url"] not in seen]
    new_articles.reverse()

    if not new_articles:
        print("[NEWS] nessuna notizia nuova.")
        return False

    for art in new_articles:
        text = f"📰 <b>{escape(art['title'])}</b>\n"
        if art["snippet"]:
            text += f"\n{escape(art['snippet'])}\n"
        text += f"\n{escape(art['url'])}"

        tg(
            "sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        print(f"[NEWS] notificato: {art['title']}")

        state["seen_news"].append(art["url"])
        save_state(state)   # salva subito dopo ogni invio riuscito
        modified = True

    return modified


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    state    = load_state()
    modified = False

    for kit in FONT_KITS:
        if check_font_kit(kit, state):
            save_state(state)   # salva subito dopo ogni kit trovato
            modified = True

    for code, name in PRODUCTS.items():
        if check_product(code, name, state):
            save_state(state)   # salva subito dopo ogni prodotto trovato
            modified = True

    # check_news gestisce internamente il save_state ad ogni notizia
    check_news(state)

    if not modified:
        print("[STATO] nessuna modifica allo stato.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)
