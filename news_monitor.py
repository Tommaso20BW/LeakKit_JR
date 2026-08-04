"""Monitor di nuove pubblicazioni e aggiornamenti su Footy Headlines."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from html import escape
from typing import Any, Iterator
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from common import HEADERS, log_status
from state_store import StateStore
from telegram_client import TelegramClient


NEWS_TEAM_URL = "https://www.footyheadlines.com/team/Juventus"
NEWS_MAX_SEEN = 300
NEWS_MAX_AGE_DAYS = 2
ROME = ZoneInfo("Europe/Rome")

NEWS_URL_RE = re.compile(
    r"^https://www\.footyheadlines\.com/.+\.html$",
    re.IGNORECASE,
)

ARTICLE_URL_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/")

HEADLINE_SELECTOR = (
    "h1, "
    "h2.post-feed__item-headline, "
    "h2.simple-post-feed__item-headline, "
    "h2[class*='headline'], "
    "h3[class*='headline'], "
    "h2, "
    "h3"
)

ARTICLE_CONTAINER_SELECTOR = (
    "article, "
    ".post-feed__item, "
    ".simple-post-feed__item, "
    ".post-feed__item-content, "
    ".simple-post-feed__item-content"
)

LINK_SELECTOR = (
    "a[href*='.html'], "
    ".post-feed__item a[href], "
    ".simple-post-feed__item a[href], "
    ".post-feed__item-headline a[href], "
    ".simple-post-feed__item-headline a[href], "
    ".tab-container__content-tab a[href]"
)


def normalize_space(value: str) -> str:
    """Normalizza spazi, tab e ritorni a capo."""
    return re.sub(r"\s+", " ", value).strip()


def normalize_article_url(raw_url: str) -> str:
    """Converte un URL relativo in assoluto e rimuove query e frammenti."""
    url = urljoin(NEWS_TEAM_URL, raw_url.strip())
    return url.split("#", 1)[0].split("?", 1)[0]


def is_valid_article_url(url: str) -> bool:
    """Controlla che l'URL appartenga a un articolo Footy Headlines."""
    return bool(NEWS_URL_RE.match(url))


def find_article_container(element: Tag) -> Tag | None:
    """Trova il contenitore principale della scheda di un articolo."""
    for selector in (
        "article",
        ".post-feed__item",
        ".simple-post-feed__item",
        ".post-feed__item-content",
        ".simple-post-feed__item-content",
    ):
        container = element.find_parent(
            lambda tag: isinstance(tag, Tag)
            and bool(tag.select_one(selector))
            and (
                tag.matches(selector)
                if hasattr(tag, "matches")
                else False
            )
        )
        if container:
            return container

    return (
        element.find_parent("article")
        or element.find_parent("div", class_="post-feed__item")
        or element.find_parent("div", class_="simple-post-feed__item")
        or element.find_parent("div", class_="post-feed__item-content")
        or element.find_parent(
            "div",
            class_="simple-post-feed__item-content",
        )
    )


def extract_candidate_title(link: Tag, container: Tag | None) -> str:
    """Estrae il titolo dalla scheda o dal link dell'articolo."""
    heading: Tag | None = None

    if container:
        heading = container.select_one(HEADLINE_SELECTOR)

    if heading is None:
        heading = link.find_parent(["h1", "h2", "h3"])

    if heading is None:
        heading = link.select_one(HEADLINE_SELECTOR)

    title = ""
    if heading:
        title = heading.get_text(" ", strip=True)

    if not title:
        title = link.get_text(" ", strip=True)

    if not title:
        title = str(link.get("aria-label", "")).strip()

    if not title:
        title = str(link.get("title", "")).strip()

    return normalize_space(title)


def extract_candidate_snippet(container: Tag | None) -> str:
    """Estrae il breve testo di anteprima dalla scheda."""
    if not container:
        return ""

    paragraph = (
        container.select_one(".content-teaser p")
        or container.select_one(".content-full p")
        or container.select_one(
            ".post-feed__item-description p"
        )
        or container.select_one(
            ".simple-post-feed__item-description p"
        )
        or container.select_one("p")
    )

    if not paragraph:
        return ""

    snippet = normalize_space(paragraph.get_text(" ", strip=True))
    return re.sub(
        r"\s*(?:Read\s+)?More\s*$",
        "",
        snippet,
        flags=re.IGNORECASE,
    ).strip()


def extract_candidate_source(link: Tag) -> str:
    """Determina da quale tab della pagina proviene l'articolo."""
    tab = link.find_parent(
        "div",
        class_="tab-container__content-tab",
    )

    if not tab:
        return "page"

    return str(tab.get("data-id", "page")).strip().lower() or "page"


def fetch_news_candidates() -> list[dict[str, Any]]:
    """Estrae tutti gli articoli presenti nella pagina Juventus."""
    response = requests.get(
        NEWS_TEAM_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    candidates: list[dict[str, Any]] = []
    candidates_by_url: dict[str, dict[str, Any]] = {}

    links: list[Tag] = [
        link
        for link in soup.select(LINK_SELECTOR)
        if isinstance(link, Tag)
    ]

    # Supporta anche la struttura in cui il link avvolge direttamente l'h2.
    for heading in soup.select(
        "h2.post-feed__item-headline, "
        "h2.simple-post-feed__item-headline, "
        "h2[class*='headline'], "
        "h3[class*='headline']"
    ):
        if not isinstance(heading, Tag):
            continue

        parent_link = heading.find_parent("a", href=True)
        if isinstance(parent_link, Tag):
            links.append(parent_link)

    for link in links:
        raw_href = str(link.get("href", "")).strip()
        if not raw_href:
            continue

        url = normalize_article_url(raw_href)
        if not is_valid_article_url(url):
            continue

        source = extract_candidate_source(link)

        existing = candidates_by_url.get(url)
        if existing:
            if source not in existing["sources"]:
                existing["sources"].append(source)
            continue

        container = (
            link.find_parent("article")
            or link.find_parent("div", class_="post-feed__item")
            or link.find_parent(
                "div",
                class_="simple-post-feed__item",
            )
            or link.find_parent(
                "div",
                class_="post-feed__item-content",
            )
            or link.find_parent(
                "div",
                class_="simple-post-feed__item-content",
            )
        )

        title = extract_candidate_title(link, container)

        # Scarta link interni, immagini e contatori privi di vero titolo.
        if not title:
            continue
        if title.isdigit():
            continue
        if len(title) < 8:
            continue

        candidate = {
            "url": url,
            "title": title,
            "snippet": extract_candidate_snippet(container),
            "sources": [source],
        }

        candidates.append(candidate)
        candidates_by_url[url] = candidate

    log_status(
        "NEWS",
        "FOOTY-HEADLINES",
        f"{len(candidates)} articoli estratti dalla pagina Juventus",
    )

    return candidates


def iter_json_nodes(value: Any) -> Iterator[dict[str, Any]]:
    """Attraversa ricorsivamente un documento JSON."""
    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from iter_json_nodes(child)

    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)


def clean_schema_text(value: Any) -> str:
    """Pulisce il testo proveniente dai metadati JSON-LD."""
    if not value:
        return ""

    text = BeautifulSoup(
        str(value),
        "html.parser",
    ).get_text(" ", strip=True)

    text = text.replace("\\_", "_")
    return normalize_space(text)


def find_news_article_metadata(
    soup: BeautifulSoup,
) -> dict[str, Any] | None:
    """Trova i metadati NewsArticle o Article nella pagina."""
    preferred_types = {
        "NewsArticle",
        "Article",
        "BlogPosting",
    }

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):
        raw = script.string or script.get_text()

        if not raw or not raw.strip():
            continue

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        for node in iter_json_nodes(parsed):
            article_type = node.get("@type")

            if isinstance(article_type, str):
                article_types = {article_type}
            elif isinstance(article_type, list):
                article_types = {
                    str(item)
                    for item in article_type
                }
            else:
                article_types = set()

            if article_types.intersection(preferred_types):
                return node

    return None


def extract_fallback_metadata(
    soup: BeautifulSoup,
    candidate: dict[str, Any],
) -> dict[str, str]:
    """Usa meta tag HTML quando il JSON-LD non è disponibile."""
    title_tag = (
        soup.select_one("meta[property='og:title']")
        or soup.select_one("meta[name='twitter:title']")
    )

    description_tag = (
        soup.select_one("meta[property='og:description']")
        or soup.select_one("meta[name='description']")
        or soup.select_one("meta[name='twitter:description']")
    )

    published_tag = (
        soup.select_one(
            "meta[property='article:published_time']"
        )
        or soup.select_one(
            "meta[name='article:published_time']"
        )
    )

    modified_tag = (
        soup.select_one(
            "meta[property='article:modified_time']"
        )
        or soup.select_one(
            "meta[name='article:modified_time']"
        )
    )

    title = ""
    description = ""
    published = ""
    modified = ""

    if title_tag:
        title = clean_schema_text(title_tag.get("content"))

    if description_tag:
        description = clean_schema_text(
            description_tag.get("content")
        )

    if published_tag:
        published = str(
            published_tag.get("content", "")
        ).strip()

    if modified_tag:
        modified = str(
            modified_tag.get("content", "")
        ).strip()

    return {
        "title": title or candidate["title"],
        "description": description or candidate["snippet"],
        "published": published,
        "modified": modified or published,
    }


def fetch_article_version(
    candidate: dict[str, Any],
) -> dict[str, str]:
    """Scarica e identifica la versione corrente di un articolo."""
    response = requests.get(
        candidate["url"],
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    metadata = find_news_article_metadata(soup)

    if metadata:
        title = clean_schema_text(
            metadata.get("headline")
            or metadata.get("name")
        )
        description = clean_schema_text(
            metadata.get("description")
        )
        published = str(
            metadata.get("datePublished") or ""
        ).strip()
        modified = str(
            metadata.get("dateModified")
            or published
        ).strip()

        title = title or candidate["title"]
        description = description or candidate["snippet"]

    else:
        fallback = extract_fallback_metadata(
            soup,
            candidate,
        )
        title = fallback["title"]
        description = fallback["description"]
        published = fallback["published"]
        modified = fallback["modified"]

        if not title:
            raise RuntimeError(
                "metadati articolo non trovati"
            )

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


def parse_article_datetime(value: str) -> datetime | None:
    """Converte una data ISO nel fuso orario di Roma."""
    if not value:
        return None

    normalized = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ROME)

    return parsed.astimezone(ROME)


def is_recent_version(version: dict[str, str]) -> bool:
    """Controlla se pubblicazione o modifica sono recenti."""
    dates = [
        parsed
        for parsed in (
            parse_article_datetime(
                version.get("published", "")
            ),
            parse_article_datetime(
                version.get("modified", "")
            ),
        )
        if parsed is not None
    ]

    if not dates:
        return False

    threshold = datetime.now(ROME) - timedelta(
        days=NEWS_MAX_AGE_DAYS
    )
    return max(dates) >= threshold


def is_recent_publication(
    version: dict[str, str],
) -> bool:
    """Controlla se la pubblicazione è recente."""
    published = parse_article_datetime(
        version.get("published", "")
    )

    if published is None:
        return False

    threshold = datetime.now(ROME) - timedelta(
        days=NEWS_MAX_AGE_DAYS
    )
    return published >= threshold


def is_republished_old_url(
    candidate: dict[str, Any],
    version: dict[str, str],
) -> bool:
    """Rileva articoli ripubblicati usando un vecchio URL datato."""
    url_date = ARTICLE_URL_DATE_RE.search(
        candidate["url"]
    )
    published = parse_article_datetime(
        version.get("published", "")
    )

    if not url_date or published is None:
        return False

    url_year = int(url_date.group(1))
    url_month = int(url_date.group(2))

    return (published.year, published.month) > (
        url_year,
        url_month,
    )


def handled_version(
    version: dict[str, str],
) -> dict[str, str]:
    """Marca una versione come già gestita."""
    result = dict(version)
    result["handled_fingerprint"] = version["fingerprint"]
    return result


def send_news_article(
    telegram: TelegramClient,
    candidate: dict[str, Any],
    version: dict[str, str],
    is_update: bool,
) -> None:
    """Invia una nuova notizia o un aggiornamento su Telegram."""
    heading = (
        "🔄 <b>AGGIORNAMENTO FOOTY HEADLINES</b>"
        if is_update
        else "📰 <b>FOOTY HEADLINES</b>"
    )

    text = (
        f"{heading}\n\n"
        f"<b>{escape(version['title'])}</b>"
    )

    if version["description"]:
        text += (
            f"\n\n{escape(version['description'])}"
        )

    text += (
        f'\n\n<a href="'
        f'{escape(candidate["url"], quote=True)}'
        f'">Leggi l’articolo</a>'
    )

    telegram.send_message(
        text,
        parse_mode="HTML",
        disable_preview=False,
    )


def _trim_articles(
    articles: dict[str, Any],
) -> None:
    """Mantiene nello stato soltanto gli ultimi articoli."""
    overflow = len(articles) - NEWS_MAX_SEEN

    if overflow <= 0:
        return

    for url in list(articles)[:overflow]:
        articles.pop(url, None)


def _save_article(
    articles: dict[str, Any],
    url: str,
    version: dict[str, str],
) -> None:
    """Salva un articolo spostandolo in fondo all'ordine."""
    articles.pop(url, None)
    articles[url] = handled_version(version)
    _trim_articles(articles)


def run(
    state: StateStore,
    telegram: TelegramClient,
) -> None:
    """Esegue il controllo delle notizie Footy Headlines."""
    news_state = state.section("news")
    articles = news_state.setdefault("articles", {})
    initialized = bool(news_state.get("initialized"))

    try:
        page_candidates = fetch_news_candidates()
    except requests.RequestException as error:
        raise RuntimeError(
            f"pagina Juventus non raggiungibile: {error}"
        ) from error

    if not page_candidates:
        log_status(
            "NEWS",
            "FOOTY-HEADLINES",
            "nessun articolo trovato",
        )
        return

    candidates = list(page_candidates)
    candidate_urls = {
        candidate["url"]
        for candidate in candidates
    }

    # Continua a controllare anche gli articoli già salvati.
    # In questo modo vengono rilevati gli aggiornamenti anche quando
    # un articolo scompare dalla prima pagina della squadra.
    old_candidates = 0

    for url, previous in list(articles.items()):
        if url in candidate_urls:
            continue

        if not is_valid_article_url(url):
            continue

        previous_dict = (
            previous
            if isinstance(previous, dict)
            else {}
        )

        candidates.append(
            {
                "url": url,
                "title": previous_dict.get(
                    "title",
                    "Articolo Footy Headlines",
                ),
                "snippet": previous_dict.get(
                    "description",
                    "",
                ),
                "sources": ["tracked"],
            }
        )

        candidate_urls.add(url)
        old_candidates += 1

    changed_state = False
    notifications = 0
    successful_checks = 0
    missing = object()

    # Il reverse mantiene l'ordine cronologico quando la pagina
    # presenta gli articoli dal più recente al più vecchio.
    for candidate in reversed(candidates):
        try:
            version = fetch_article_version(candidate)
        except requests.RequestException as error:
            log_status(
                "NEWS",
                "FOOTY-HEADLINES",
                (
                    "errore HTTP durante la verifica di "
                    f"'{candidate['title']}': {error}"
                ),
            )
            continue
        except RuntimeError as error:
            log_status(
                "NEWS",
                "FOOTY-HEADLINES",
                (
                    "errore durante la verifica di "
                    f"'{candidate['title']}': {error}"
                ),
            )
            continue

        successful_checks += 1

        previous = articles.get(
            candidate["url"],
            missing,
        )

        is_missing = previous is missing
        is_legacy_null = previous is None
        previous_dict = (
            previous
            if isinstance(previous, dict)
            else {}
        )

        fingerprint_changed = (
            bool(previous_dict)
            and previous_dict.get("fingerprint")
            != version["fingerprint"]
        )

        republished_old_url = (
            is_republished_old_url(
                candidate,
                version,
            )
            and is_recent_version(version)
        )

        republished_not_handled = (
            republished_old_url
            and (
                is_missing
                or is_legacy_null
                or (
                    previous_dict.get("fingerprint")
                    == version["fingerprint"]
                    and previous_dict.get(
                        "handled_fingerprint"
                    )
                    != version["fingerprint"]
                )
            )
        )

        recent_publication = is_recent_publication(
            version
        )
        recent_version = is_recent_version(version)

        source_names = {
            str(source).lower()
            for source in candidate.get(
                "sources",
                [],
            )
        }

        appears_in_recent_tab = bool(
            source_names.intersection(
                {
                    "latest",
                    "recent",
                    "news",
                    "page",
                }
            )
        )

        # Prima esecuzione:
        # crea la base iniziale senza inviare tutti gli articoli
        # già presenti sul sito.
        if not initialized:
            _save_article(
                articles,
                candidate["url"],
                version,
            )
            changed_state = True
            continue

        # Vecchi valori null presenti nello stato vengono migrati.
        # Se l'articolo è recente, viene comunque notificato.
        if is_legacy_null:
            if recent_version:
                send_news_article(
                    telegram,
                    candidate,
                    version,
                    is_update=republished_old_url,
                )

                label = (
                    "aggiornamento ripubblicato"
                    if republished_old_url
                    else "nuova notizia"
                )

                log_status(
                    "NEWS",
                    "FOOTY-HEADLINES",
                    (
                        f"notificato {label}: "
                        f"{version['title']}"
                    ),
                )

                notifications += 1

            _save_article(
                articles,
                candidate["url"],
                version,
            )
            news_state["initialized"] = True
            state.save()
            changed_state = False
            continue

        # Articolo mai visto dopo l'inizializzazione.
        if is_missing:
            notify_as_update = (
                republished_not_handled
                or (
                    not recent_publication
                    and recent_version
                    and appears_in_recent_tab
                )
            )

            should_notify = (
                recent_publication
                or notify_as_update
            )

            if should_notify:
                send_news_article(
                    telegram,
                    candidate,
                    version,
                    is_update=notify_as_update,
                )

                label = (
                    "aggiornamento"
                    if notify_as_update
                    else "nuova notizia"
                )

                log_status(
                    "NEWS",
                    "FOOTY-HEADLINES",
                    (
                        f"notificato {label}: "
                        f"{version['title']}"
                    ),
                )

                notifications += 1

            else:
                log_status(
                    "NEWS",
                    "FOOTY-HEADLINES",
                    (
                        "articolo mai visto ma non recente, "
                        f"registrato senza notifica: "
                        f"{version['title']}"
                    ),
                )

            _save_article(
                articles,
                candidate["url"],
                version,
            )
            news_state["initialized"] = True
            state.save()
            changed_state = False
            continue

        # Articolo già presente ma modificato.
        if fingerprint_changed:
            send_news_article(
                telegram,
                candidate,
                version,
                is_update=True,
            )

            log_status(
                "NEWS",
                "FOOTY-HEADLINES",
                (
                    "notificato aggiornamento: "
                    f"{version['title']}"
                ),
            )

            _save_article(
                articles,
                candidate["url"],
                version,
            )
            news_state["initialized"] = True
            state.save()
            changed_state = False
            notifications += 1
            continue

        # Articolo ripubblicato con URL vecchio, ma stessa impronta
        # non ancora marcata come gestita.
        if republished_not_handled:
            send_news_article(
                telegram,
                candidate,
                version,
                is_update=True,
            )

            log_status(
                "NEWS",
                "FOOTY-HEADLINES",
                (
                    "notificato articolo ripubblicato: "
                    f"{version['title']}"
                ),
            )

            _save_article(
                articles,
                candidate["url"],
                version,
            )
            news_state["initialized"] = True
            state.save()
            changed_state = False
            notifications += 1

    if successful_checks == 0:
        raise RuntimeError(
            "nessun articolo è stato verificato correttamente"
        )

    if not news_state.get("initialized"):
        news_state["initialized"] = True
        changed_state = True

    if changed_state:
        _trim_articles(articles)
        state.save()

    if notifications == 0:
        checked = len(page_candidates) + old_candidates

        log_status(
            "NEWS",
            "FOOTY-HEADLINES",
            f"nessuna novità ({checked} controllati)",
        )