"""
Juve Leak Bot

Controlla font e immagini prodotto sullo store Juventus e le notizie Juventus
su Footy Headlines. Un articolo già visto viene notificato nuovamente soltanto
se Footy Headlines lo aggiorna davvero.
"""

import hashlib
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
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROME = ZoneInfo("Europe/Rome")


def state_path(filename):
    return os.path.join(SCRIPT_DIR, filename)


def tg(method, **kwargs):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    response = requests.post(url, timeout=30, **kwargs)
    if not response.ok:
        try:
            description = response.json().get("description", response.text)
        except ValueError:
            description = response.text
        raise RuntimeError(
            f"Telegram {method}: HTTP {response.status_code} - {description}"
        )
    return response.json()


# ---------------------------------------------------------------------------
# SEZIONE 1: font (cifre di personalizzazione)
# ---------------------------------------------------------------------------

FONT_KITS = ["HOME-26-27", "AWAY-26-27", "THIRD-26-27"]
FONT_URL = (
    "https://store.juventus.com/images/juventus/customizations/"
    "fonts/{kit}/{n}.png"
)


def check_font_kit(kit):
    flag_file = state_path(f".found-font-{kit}")
    if os.path.exists(flag_file):
        print(f"[FONT {kit}] già notificato, salto.")
        return

    found = []
    for number in range(10):
        url = FONT_URL.format(kit=kit, n=number)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as error:
            print(f"[FONT {kit}] {number}.png -> errore rete: {error}")
            continue

        content_type = response.headers.get("Content-Type", "")
        if (
            response.status_code == 200
            and "image" in content_type
            and "svg" not in content_type
            and len(response.content) > 500
        ):
            print(
                f"[FONT {kit}] {number}.png -> TROVATA! "
                f"({len(response.content)} byte)"
            )
            found.append((number, response.content))
        else:
            print(
                f"[FONT {kit}] {number}.png -> non ancora "
                f"({response.status_code}, {content_type})"
            )

    if not found:
        print(f"[FONT {kit}] nessuna immagine ancora caricata.")
        return

    tg(
        "sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": (
                f"🚨 LEAK! Le immagini del font {kit} della Juventus "
                f"sono state caricate sullo store! "
                f"({len(found)}/10 cifre trovate)\n\n"
                "Te le invio qui sotto 👇"
            ),
        },
    )
    for number, content in found:
        tg(
            "sendPhoto",
            data={"chat_id": CHAT_ID, "caption": f"Cifra {number} — {kit}"},
            files={
                "photo": (
                    f"{kit}-{number}.png",
                    content,
                    "image/png",
                )
            },
        )

    with open(flag_file, "w", encoding="utf-8") as file:
        file.write("notified\n")
    print(f"[FONT {kit}] notifica inviata, flag creato.")


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
    "10": "AWAY-GK-26-27",
}

PRODUCT_URL = (
    "https://store.juventus.com/images/juventus/products/small/"
    "JU26{letter}{code}{suffix}.webp"
)


def fetch_image(url, log_prefix):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as error:
        print(f"{log_prefix}errore rete: {error}")
        return None

    content_type = response.headers.get("Content-Type", "")
    if (
        response.status_code == 200
        and "image" in content_type
        and "svg" not in content_type
        and len(response.content) > 500
    ):
        print(f"{log_prefix}TROVATA! ({len(response.content)} byte)")
        return response.content

    print(f"{log_prefix}non ancora ({response.status_code}, {content_type})")
    return None


def check_product(code, name):
    flag_file = state_path(f".found-product-{code}")
    if os.path.exists(flag_file):
        print(f"[PRODUCT {name}] già notificato, salto.")
        return

    found = {}

    front_url = PRODUCT_URL.format(
        letter=PRODUCT_LETTER,
        code=code,
        suffix="",
    )
    content = fetch_image(
        front_url,
        f"[PRODUCT {name}] controllo fronte: ",
    )
    if content:
        found["fronte"] = content

    back_url = PRODUCT_URL.format(
        letter=PRODUCT_LETTER,
        code=code,
        suffix="_d",
    )
    content = fetch_image(
        back_url,
        f"[PRODUCT {name}] controllo retro: ",
    )
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
                f"è stata caricata sullo store! "
                f"({len(found)}/2 lati trovati)\n\n"
                "Te la invio qui sotto 👇"
            ),
        },
    )
    for side, image_content in found.items():
        tg(
            "sendPhoto",
            data={"chat_id": CHAT_ID, "caption": f"{name} — {side}"},
            files={
                "photo": (
                    f"JU26{PRODUCT_LETTER}{code}-{side}.png",
                    image_content,
                    "image/png",
                )
            },
        )

    with open(flag_file, "w", encoding="utf-8") as file:
        file.write("notified\n")
    print(f"[PRODUCT {name}] notifica inviata, flag creato.")


# ---------------------------------------------------------------------------
# SEZIONE 3: notizie Footy Headlines
# ---------------------------------------------------------------------------

NEWS_TEAM_URL = "https://www.footyheadlines.com/team/Juventus"
NEWS_STATE_FILE = state_path(".seen_news.json")
NEWS_MAX_SEEN = 300
NEWS_MAX_AGE_DAYS = 10

NEWS_URL_RE = re.compile(
    r"^https://www\.footyheadlines\.com/.+\.html$",
    re.IGNORECASE,
)


def load_news_state():
    if not os.path.exists(NEWS_STATE_FILE):
        return {}

    with open(NEWS_STATE_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    # Migrazione automatica dal vecchio formato: [url, url, ...]. La firma
    # corrente viene registrata senza reinviare tutti gli articoli già visti.
    if isinstance(data, list):
        return {url: None for url in data if isinstance(url, str)}

    if isinstance(data, dict) and isinstance(data.get("articles"), dict):
        return data["articles"]

    raise RuntimeError(
        ".seen_news.json non valido; interrompo per evitare duplicati."
    )


def save_news_state(state):
    recent_items = list(state.items())[-NEWS_MAX_SEEN:]
    payload = {
        "version": 2,
        "articles": dict(recent_items),
    }
    temporary_file = f"{NEWS_STATE_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, NEWS_STATE_FILE)


def fetch_news_candidates():
    response = requests.get(NEWS_TEAM_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    candidates = []
    urls_done = set()
    headlines = soup.select(
        "h2.post-feed__item-headline, "
        "h2.simple-post-feed__item-headline"
    )

    for heading in headlines:
        link = heading.find_parent("a", href=True)
        if not link:
            continue

        url = urljoin(NEWS_TEAM_URL, link["href"].strip())
        url = url.split("#", 1)[0].split("?", 1)[0]
        if not NEWS_URL_RE.match(url) or url in urls_done:
            continue

        content = heading.find_parent(
            "div",
            class_="post-feed__item-content",
        )
        snippet = ""
        if content:
            paragraph = (
                content.select_one(".content-teaser p")
                or content.select_one(".content-full p")
            )
            if paragraph:
                snippet = paragraph.get_text(" ", strip=True)
                snippet = re.sub(r"\s*More\s*$", "", snippet).strip()

        urls_done.add(url)
        candidates.append(
            {
                "url": url,
                "title": heading.get_text(" ", strip=True),
                "snippet": snippet,
            }
        )

    return candidates


def iter_json_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)


def clean_schema_text(value):
    if not value:
        return ""
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text.replace("\\_", "_")).strip()


def fetch_article_version(candidate):
    response = requests.get(candidate["url"], headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    metadata = None
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in iter_json_nodes(parsed):
            article_type = node.get("@type")
            if article_type == "NewsArticle" or (
                isinstance(article_type, list)
                and "NewsArticle" in article_type
            ):
                metadata = node
                break
        if metadata:
            break

    if not metadata:
        raise RuntimeError("metadati NewsArticle non trovati")

    title = clean_schema_text(
        metadata.get("headline") or metadata.get("name")
    ) or candidate["title"]
    description = clean_schema_text(metadata.get("description"))
    if not description:
        description = candidate["snippet"]

    published = str(metadata.get("datePublished") or "")
    modified = str(metadata.get("dateModified") or published)
    signature_source = json.dumps(
        {
            "title": title,
            "description": description,
            "modified": modified,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(
        signature_source.encode("utf-8")
    ).hexdigest()

    return {
        "fingerprint": fingerprint,
        "published": published,
        "modified": modified,
        "title": title,
        "description": description,
    }


def parse_article_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ROME)
    return parsed.astimezone(ROME)


def is_recent_version(version):
    dates = [
        parsed
        for parsed in (
            parse_article_datetime(version.get("published")),
            parse_article_datetime(version.get("modified")),
        )
        if parsed is not None
    ]
    if not dates:
        return False
    cutoff = datetime.now(ROME) - timedelta(days=NEWS_MAX_AGE_DAYS)
    return max(dates) >= cutoff


def send_news_article(candidate, version, is_update):
    if is_update:
        heading = "🔄 <b>AGGIORNAMENTO FOOTY HEADLINES</b>"
    else:
        heading = "📰 <b>FOOTY HEADLINES</b>"

    text = f"{heading}\n\n<b>{escape(version['title'])}</b>"
    if version["description"]:
        text += f"\n\n{escape(version['description'])}"
    text += (
        f"\n\n<a href=\"{escape(candidate['url'], quote=True)}\">"
        "Leggi l’articolo</a>"
    )

    tg(
        "sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
    )


def check_news():
    state = load_news_state()
    print(f"[NEWS] stato caricato: {len(state)} articoli monitorati.")

    try:
        page_candidates = fetch_news_candidates()
    except requests.RequestException as error:
        print(f"[NEWS] errore rete: {error}")
        return

    if not page_candidates:
        print("[NEWS] nessun articolo trovato nella pagina.")
        return

    # Un articolo vecchio può essere aggiornato quando non compare più nella
    # prima pagina Juventus. Manteniamo quindi sotto controllo anche tutti gli
    # URL già salvati nella cache, non soltanto quelli elencati oggi dal sito.
    candidates = list(page_candidates)
    candidate_urls = {candidate["url"] for candidate in candidates}
    old_candidates = 0
    for url, previous in state.items():
        if url in candidate_urls or not NEWS_URL_RE.match(url):
            continue
        previous = previous if isinstance(previous, dict) else {}
        candidates.append(
            {
                "url": url,
                "title": previous.get("title", "Articolo Footy Headlines"),
                "snippet": previous.get("description", ""),
            }
        )
        candidate_urls.add(url)
        old_candidates += 1

    print(
        f"[NEWS] controllo aggiornamenti: {len(page_candidates)} in pagina, "
        f"{old_candidates} articoli vecchi monitorati."
    )
    changed_state = False
    notifications = 0

    # La parte corrente della pagina è in ordine dal più recente al più
    # vecchio; Telegram riceve le nuove notizie in ordine cronologico.
    for candidate in reversed(candidates):
        try:
            version = fetch_article_version(candidate)
        except (requests.RequestException, RuntimeError) as error:
            print(
                f"[NEWS] impossibile verificare '{candidate['title']}': "
                f"{error}"
            )
            continue

        previous = state.get(candidate["url"], "__missing__")

        # Migrazione dal vecchio elenco URL o baseline di un articolo vecchio:
        # registra la versione corrente senza generare notifiche retroattive.
        if previous is None or (
            previous == "__missing__" and not is_recent_version(version)
        ):
            state.pop(candidate["url"], None)
            state[candidate["url"]] = version
            changed_state = True
            continue

        is_new = previous == "__missing__"
        is_update = (
            isinstance(previous, dict)
            and previous.get("fingerprint") != version["fingerprint"]
        )

        if not is_new and not is_update:
            continue

        send_news_article(candidate, version, is_update=is_update)
        label = "aggiornamento" if is_update else "nuova notizia"
        print(f"[NEWS] notificato {label}: {version['title']}")

        state.pop(candidate["url"], None)
        state[candidate["url"]] = version
        save_news_state(state)
        changed_state = False
        notifications += 1

    if changed_state:
        save_news_state(state)

    if notifications == 0:
        print("[NEWS] nessuna notizia nuova o aggiornata.")
    else:
        print(f"[NEWS] notifiche inviate: {notifications}")


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
    except Exception as error:
        print(f"Errore: {error}", file=sys.stderr)
        sys.exit(1)
