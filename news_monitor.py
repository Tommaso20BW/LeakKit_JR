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


def fetch_news_candidates() -> list[dict[str, Any]]:
    response = requests.get(NEWS_TEAM_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    candidates: list[dict[str, Any]] = []
    candidates_by_url: dict[str, dict[str, Any]] = {}
    headlines = soup.select(
        "h2.post-feed__item-headline, h2.simple-post-feed__item-headline"
    )

    for heading in headlines:
        link = heading.find_parent("a", href=True)
        if not link:
            continue

        url = urljoin(NEWS_TEAM_URL, str(link["href"]).strip())
        url = url.split("#", 1)[0].split("?", 1)[0]

        if not NEWS_URL_RE.match(url):
            continue

        tab = heading.find_parent(
            "div",
            class_="tab-container__content-tab",
        )
        source = str(
            tab.get("data-id", "page") if tab else "page"
        ).lower()

        if url in candidates_by_url:
            sources = candidates_by_url[url]["sources"]
            if source not in sources:
                sources.append(source)
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
                snippet = re.sub(
                    r"\s*More\s*$",
                    "",
                    snippet,
                ).strip()

        candidate = {
            "url": url,
            "title": heading.get_text(" ", strip=True),
            "snippet": snippet,
            "sources": [source],
        }

        candidates.append(candidate)
        candidates_by_url[url] = candidate

    return candidates


def iter_json_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from iter_json_nodes(child)

    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)


def clean_schema_text(value: Any) -> str:
    if not value:
        return ""

    text = BeautifulSoup(
        str(value),
        "html.parser",
    ).get_text(" ", strip=True)

    return re.sub(
        r"\s+",
        " ",
        text.replace("\\_", "_"),
    ).strip()


def fetch_article_version(
    candidate: dict[str, Any],
) -> dict[str, str]:
    response = requests.get(
        candidate["url"],
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    metadata = None

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):
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
        raise RuntimeError(
            "metadati NewsArticle non trovati"
        )

    title = clean_schema_text(
        metadata.get("headline")
        or metadata.get("name")
    )
    title = title or candidate["title"]

    description = clean_schema_text(
        metadata.get("description")
    )
    description = description or candidate["snippet"]

    published = str(
        metadata.get("datePublished") or ""
    )
    modified = str(
        metadata.get("dateModified") or published
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


def parse_article_datetime(
    value: str,
) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ROME)

    return parsed.astimezone(ROME)


def is_recent_version(
    version: dict[str, str],
) -> bool:
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

    return max(dates) >= (
        datetime.now(ROME)
        - timedelta(days=NEWS_MAX_AGE_DAYS)
    )


def is_recent_publication(
    version: dict[str, str],
) -> bool:
    published = parse_article_datetime(
        version.get("published", "")
    )

    if published is None:
        return False

    return published >= (
        datetime.now(ROME)
        - timedelta(days=NEWS_MAX_AGE_DAYS)
    )


def is_republished_old_url(
    candidate: dict[str, Any],
    version: dict[str, str],
) -> bool:
    url_date = re.search(
        r"/(\d{4})/(\d{2})/",
        candidate["url"],
    )
    published = parse_article_datetime(
        version.get("published", "")
    )

    if not url_date or published is None:
        return False

    return (published.year, published.month) > (
        int(url_date.group(1)),
        int(url_date.group(2)),
    )


def handled_version(
    version: dict[str, str],
) -> dict[str, str]:
    result = dict(version)
    result["handled_fingerprint"] = version["fingerprint"]
    return result


def send_news_article(
    telegram: TelegramClient,
    candidate: dict[str, Any],
    version: dict[str, str],
    is_update: bool,
) -> None:
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

    # TelegramClient gestisce già internamente formato HTML
    # e anteprima del collegamento.
    telegram.send_message(text)


def _trim_articles(
    articles: dict[str, Any],
) -> None:
    overflow = len(articles) - NEWS_MAX_SEEN

    for url in list(articles)[:max(0, overflow)]:
        articles.pop(url, None)


def run(
    state: StateStore,
    telegram: TelegramClient,
) -> None:
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
    old_candidates = 0

    for url, previous in list(articles.items()):
        if (
            url in candidate_urls
            or not NEWS_URL_RE.match(url)
        ):
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

    for candidate in reversed(candidates):
        try:
            version = fetch_article_version(candidate)
        except (
            requests.RequestException,
            RuntimeError,
        ) as error:
            log_status(
                "NEWS",
                "FOOTY-HEADLINES",
                (
                    f"errore verifica "
                    f"'{candidate['title']}': {error}"
                ),
            )
            continue

        successful_checks += 1

        previous = articles.get(
            candidate["url"],
            "__missing__",
        )

        unhandled_republished = (
            is_republished_old_url(
                candidate,
                version,
            )
            and is_recent_version(version)
            and (
                previous is None
                or (
                    isinstance(previous, dict)
                    and previous.get("fingerprint")
                    == version["fingerprint"]
                    and previous.get(
                        "handled_fingerprint"
                    )
                    != version["fingerprint"]
                )
            )
        )

        unseen_old_update = (
            previous == "__missing__"
            and initialized
            and "latest"
            in candidate.get("sources", [])
            and not is_recent_publication(version)
        )

        if previous is None or (
            previous == "__missing__"
            and not is_recent_version(version)
            and not unseen_old_update
        ):
            if not unhandled_republished:
                articles.pop(
                    candidate["url"],
                    None,
                )
                articles[candidate["url"]] = (
                    handled_version(version)
                )
                changed_state = True
                continue

        is_new = previous == "__missing__"

        is_update = (
            isinstance(previous, dict)
            and previous.get("fingerprint")
            != version["fingerprint"]
        )

        if (
            not is_new
            and not is_update
            and not unhandled_republished
        ):
            continue

        notify_as_update = (
            is_update
            or unseen_old_update
            or unhandled_republished
        )

        send_news_article(
            telegram,
            candidate,
            version,
            notify_as_update,
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

        articles.pop(
            candidate["url"],
            None,
        )
        articles[candidate["url"]] = (
            handled_version(version)
        )

        _trim_articles(articles)
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
        checked = (
            len(page_candidates)
            + old_candidates
        )

        log_status(
            "NEWS",
            "FOOTY-HEADLINES",
            f"nessuna novità ({checked} controllati)",
        )