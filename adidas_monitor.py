"""Scoperta di codici e asset Adidas Juventus già esposti pubblicamente.

Il sito Adidas applica una protezione anti-bot che può bloccare i runner cloud.
Per questo il monitor prova sia la pagina Juventus ufficiale, sia l'indice
pubblico delle immagini Bing, che espone URL CDN Adidas già indicizzati.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from datetime import datetime
from html import escape
from typing import Any, Iterable
from urllib.parse import quote, unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from curl_cffi import requests as browser_requests

from common import env_bool, env_int, log_status
from state_store import StateStore, utc_now
from telegram_client import TelegramClient


ADIDAS_CATEGORY_URL = "https://www.adidas.it/juventus"
BING_IMAGES_URL = "https://www.bing.com/images/search"
ROME = ZoneInfo("Europe/Rome")
PRODUCT_CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
PRODUCT_PAGE_CODE_RE = re.compile(r"/([A-Z0-9]{6})\.html(?:[?#]|$)", re.I)
IMAGE_CODE_RE = re.compile(
    r"(?:^|[_/])([A-Z0-9]{6})(?=_[^/]*\.(?:jpe?g|png|webp)(?:[?#]|$))",
    re.I,
)
ASSET_SEGMENT_RE = re.compile(r"^[0-9a-f]{32}_\d+$", re.I)
TEAM_TERMS = ("juventus", "juve", "juventus fc", "juventus turin", "juventus torino")


class AdidasSourceError(RuntimeError):
    pass


def _contains_team(value: str) -> bool:
    lowered = unquote(value).casefold()
    return any(term in lowered for term in TEAM_TERMS)


def _is_adidas_host(host: str) -> bool:
    host = host.casefold().split(":", 1)[0]
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)*adidas\.[a-z.]+", host))


def extract_product_code(product_url: str, image_url: str) -> str | None:
    match = PRODUCT_PAGE_CODE_RE.search(product_url)
    if not match:
        match = IMAGE_CODE_RE.search(unquote(urlparse(image_url).path))
    if not match:
        return None
    code = match.group(1).upper()
    return code if PRODUCT_CODE_RE.fullmatch(code) else None


def extract_asset_id(image_url: str) -> str:
    path_parts = [unquote(part) for part in urlparse(image_url).path.split("/") if part]
    filename = path_parts[-1] if path_parts else "asset"
    asset_segment = next(
        (part for part in reversed(path_parts[:-1]) if ASSET_SEGMENT_RE.fullmatch(part)),
        "",
    )
    if asset_segment:
        return f"{asset_segment}/{filename}"
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:20]
    return f"url-{digest}/{filename}"


def classify_candidate(
    title: str,
    product_url: str,
    image_url: str,
) -> tuple[int, int, list[str]]:
    score = 0
    reasons: list[str] = []
    if _contains_team(title):
        score += 5
        reasons.append("Juventus nel titolo")
    if _contains_team(product_url):
        score += 4
        reasons.append("Juventus nell’URL prodotto")
    product_host = urlparse(product_url).netloc.casefold()
    if _is_adidas_host(product_host):
        score += 2
        reasons.append("pagina Adidas ufficiale")
    if urlparse(image_url).netloc.casefold() in {
        "assets.adidas.com",
        "brand.assets.adidas.com",
    }:
        score += 2
        reasons.append("immagine sul CDN Adidas")
    if extract_product_code(product_url, image_url):
        score += 1
        reasons.append("codice prodotto verificabile")
    confidence = min(99, 45 + score * 4)
    return score, confidence, reasons


def make_candidate(
    title: str,
    product_url: str,
    image_url: str,
    source: str,
) -> dict[str, Any] | None:
    code = extract_product_code(product_url, image_url)
    if not code:
        return None
    score, confidence, reasons = classify_candidate(title, product_url, image_url)
    if score < 7:
        return None
    if not product_url or not urlparse(product_url).netloc:
        product_url = f"https://www.adidas.it/{code}.html"
    return {
        "code": code,
        "title": title.strip() or f"Prodotto Adidas Juventus {code}",
        "product_url": product_url,
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "sources": [source],
        "assets": {
            extract_asset_id(image_url): {
                "url": image_url,
                "first_seen": utc_now(),
                "source": source,
            }
        }
        if image_url
        else {},
    }


def merge_candidate(products: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> None:
    code = candidate["code"]
    if code not in products:
        products[code] = candidate
        return
    current = products[code]
    if candidate["score"] > current.get("score", 0):
        for field in ("title", "product_url", "score", "confidence", "reasons"):
            current[field] = candidate[field]
    for source in candidate.get("sources", []):
        if source not in current.setdefault("sources", []):
            current["sources"].append(source)
    current.setdefault("assets", {}).update(candidate.get("assets", {}))


def parse_bing_image_results(document: str, source: str) -> dict[str, dict[str, Any]]:
    soup = BeautifulSoup(document, "html.parser")
    products: dict[str, dict[str, Any]] = {}
    for anchor in soup.select("a.iusc[m]"):
        try:
            payload = json.loads(html.unescape(anchor.get("m", "")))
        except (TypeError, json.JSONDecodeError):
            continue
        title = str(payload.get("t") or payload.get("turl") or "")
        image_url = str(payload.get("murl") or "")
        product_url = str(payload.get("purl") or "")
        if urlparse(image_url).netloc.casefold() not in {
            "assets.adidas.com",
            "brand.assets.adidas.com",
        }:
            continue
        candidate = make_candidate(title, product_url, image_url, source)
        if candidate:
            merge_candidate(products, candidate)
    return products


def parse_adidas_category(document: str, page_url: str) -> dict[str, dict[str, Any]]:
    soup = BeautifulSoup(document, "html.parser")
    products: dict[str, dict[str, Any]] = {}
    for anchor in soup.select("a[href]"):
        product_url = urljoin(page_url, str(anchor.get("href", "")))
        code_match = PRODUCT_PAGE_CODE_RE.search(product_url)
        if not code_match:
            continue
        container = anchor.find_parent(["article", "li"]) or anchor.parent or anchor
        title = anchor.get_text(" ", strip=True)
        if len(title) < 4:
            title = container.get_text(" ", strip=True)
        image_urls: list[str] = []
        for image in container.select("img"):
            for attribute in ("src", "data-src"):
                value = str(image.get(attribute, "")).strip()
                if value:
                    image_urls.append(urljoin(page_url, value))
            srcset = str(image.get("srcset", ""))
            image_urls.extend(part.strip().split(" ")[0] for part in srcset.split(",") if part.strip())
        if not image_urls:
            image_urls = [""]
        for image_url in image_urls:
            candidate = make_candidate(title, product_url, image_url, "adidas.it/juventus")
            if candidate:
                merge_candidate(products, candidate)
    return products


def _season_queries() -> list[str]:
    now = datetime.now(ROME)
    season_start = now.year if now.month >= 6 else now.year - 1
    starts = (season_start - 1, season_start, season_start + 1)
    queries: list[str] = []
    for start in starts:
        short = f"{start % 100:02d} {(start + 1) % 100:02d}"
        full = f"{start} {start + 1}"
        queries.extend(
            [
                f"adidas Juventus {short} jersey",
                f"adidas Juventus {full} jersey",
                f"adidas Juventus {short} authentic jersey",
                f"adidas Juventus {short} goalkeeper jersey",
                f"adidas Juventus {short} training",
            ]
        )
    queries.extend(
        [
            "adidas Juventus new jersey",
            "adidas Juventus kit",
            "adidas Juventus collection",
            "adidas Juventus jacket",
            "adidas Juventus shoes",
        ]
    )
    custom = os.getenv("ADIDAS_EXTRA_QUERIES", "")
    queries.extend(item.strip() for item in custom.split("|") if item.strip())
    return list(dict.fromkeys(queries))


def _fetch_direct_category(
    session: browser_requests.Session,
) -> tuple[dict[str, dict[str, Any]], int]:
    products: dict[str, dict[str, Any]] = {}
    successful_pages = 0
    for start in (0, 48, 96, 144):
        url = ADIDAS_CATEGORY_URL if start == 0 else f"{ADIDAS_CATEGORY_URL}?start={start}"
        try:
            response = session.get(url, timeout=30, allow_redirects=True)
        except browser_requests.RequestsError as error:
            log_status("ADIDAS", "DIRETTO", f"errore rete: {error}")
            continue
        if response.status_code != 200:
            log_status("ADIDAS", "DIRETTO", f"HTTP {response.status_code} su start={start}")
            continue
        page_products = parse_adidas_category(response.text, url)
        successful_pages += 1
        for candidate in page_products.values():
            merge_candidate(products, candidate)
        if start and not page_products:
            break
    return products, successful_pages


def _fetch_bing_index(
    session: browser_requests.Session,
) -> tuple[dict[str, dict[str, Any]], int]:
    products: dict[str, dict[str, Any]] = {}
    successful_queries = 0
    pages = min(2, env_int("ADIDAS_SEARCH_PAGES", 1, 1))
    try:
        delay = max(0.0, float(os.getenv("ADIDAS_QUERY_DELAY", "0.35")))
    except ValueError as error:
        raise ValueError("ADIDAS_QUERY_DELAY deve essere un numero") from error

    for query in _season_queries():
        for page in range(pages):
            first = 1 + page * 35
            try:
                response = session.get(
                    BING_IMAGES_URL,
                    params={
                        "q": query,
                        "setlang": "en",
                        "form": "HDRSC3",
                        "first": str(first),
                    },
                    timeout=30,
                )
            except browser_requests.RequestsError as error:
                log_status("ADIDAS", "INDICE", f"errore rete per '{query}': {error}")
                continue
            if response.status_code != 200:
                log_status("ADIDAS", "INDICE", f"HTTP {response.status_code} per '{query}'")
                continue
            successful_queries += 1
            source = f"Bing Images: {query}"
            for candidate in parse_bing_image_results(response.text, source).values():
                merge_candidate(products, candidate)
            if delay:
                time.sleep(delay)
    return products, successful_queries


def discover_products() -> dict[str, dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    with browser_requests.Session(impersonate="chrome") as session:
        direct, direct_success = _fetch_direct_category(session)
        for candidate in direct.values():
            merge_candidate(products, candidate)
        indexed, index_success = _fetch_bing_index(session)
        for candidate in indexed.values():
            merge_candidate(products, candidate)

    if direct_success == 0:
        log_status("ADIDAS", "DIRETTO", "sorgente bloccata; uso l’indice pubblico")
    if index_success == 0:
        raise AdidasSourceError("nessuna query dell’indice pubblico ha risposto")
    if not products:
        raise AdidasSourceError(
            "le sorgenti hanno risposto ma non hanno prodotto candidati Juventus affidabili"
        )
    return products


def _state_product(candidate: dict[str, Any], first_seen: str | None = None) -> dict[str, Any]:
    now = utc_now()
    assets = {}
    for asset_id, asset in candidate.get("assets", {}).items():
        assets[asset_id] = {
            "url": asset["url"],
            "first_seen": asset.get("first_seen", now),
            "last_seen": now,
            "source": asset.get("source", ""),
        }
    return {
        "title": candidate["title"],
        "product_url": candidate["product_url"],
        "confidence": candidate["confidence"],
        "reasons": candidate["reasons"],
        "sources": candidate.get("sources", []),
        "first_seen": first_seen or now,
        "last_seen": now,
        "assets": assets,
    }


def _send_product_notification(
    telegram: TelegramClient,
    candidate: dict[str, Any],
    *,
    is_update: bool,
    new_asset_ids: Iterable[str],
) -> None:
    asset_ids = list(new_asset_ids)
    heading = "🖼 NUOVI ASSET ADIDAS" if is_update else "🚨 NUOVO PRODOTTO ADIDAS JUVENTUS"
    reasons = ", ".join(candidate.get("reasons", []))
    text = (
        f"<b>{heading}</b>\n\n"
        f"<b>{escape(candidate['title'])}</b>\n"
        f"Codice: <code>{escape(candidate['code'])}</code>\n"
        f"Confidenza: <b>{candidate['confidence']}%</b>\n"
        f"Motivi: {escape(reasons)}\n"
        f"Asset nuovi: <b>{len(asset_ids)}</b>"
    )
    product_url = candidate.get("product_url", "")
    if product_url:
        text += f'\n\n<a href="{escape(product_url, quote=True)}">Apri il prodotto</a>'

    assets = candidate.get("assets", {})
    first_asset = assets.get(asset_ids[0], {}) if asset_ids else {}
    image_url = first_asset.get("url", "")
    if image_url:
        text += f"\nAsset ID: <code>{escape(asset_ids[0])}</code>"
        try:
            telegram.send_photo_url(image_url, text, parse_mode="HTML")
            return
        except RuntimeError as error:
            log_status("ADIDAS", candidate["code"], f"foto non inviata, fallback testo: {error}")
            text += f'\n<a href="{escape(image_url, quote=True)}">Apri l’immagine</a>'
    telegram.send_message(text, parse_mode="HTML", disable_preview=False)


def _send_baseline_summary(
    telegram: TelegramClient,
    products: dict[str, dict[str, Any]],
) -> None:
    lines = [
        "✅ <b>MONITOR ADIDAS JUVENTUS ATTIVATO</b>",
        "",
        f"Baseline creata con <b>{len(products)} codici</b> già pubblici.",
        "Da ora riceverai i nuovi codici e i nuovi asset.",
        "",
    ]
    for code in sorted(products):
        title = products[code].get("title", "Prodotto Adidas")
        lines.append(f"• <code>{escape(code)}</code> — {escape(title[:90])}")

    chunks: list[str] = []
    current = ""
    for line in lines:
        addition = line + "\n"
        if current and len(current) + len(addition) > 3900:
            chunks.append(current.rstrip())
            current = ""
        current += addition
    if current:
        chunks.append(current.rstrip())
    for chunk in chunks:
        telegram.send_message(chunk, parse_mode="HTML", disable_preview=True)


def run(state: StateStore, telegram: TelegramClient) -> None:
    discovered = discover_products()
    adidas_state = state.section("adidas")
    saved_products = adidas_state.setdefault("products", {})
    initialized = bool(adidas_state.get("initialized"))

    if not initialized:
        for code, candidate in discovered.items():
            saved_products[code] = _state_product(candidate)
        adidas_state["initialized"] = True
        adidas_state["last_scan"] = utc_now()
        if env_bool("ADIDAS_NOTIFY_BASELINE", True):
            _send_baseline_summary(telegram, discovered)
        state.save()
        log_status("ADIDAS", "BASELINE", f"registrati {len(discovered)} codici")
        return

    notifications = 0
    now = utc_now()
    for code, candidate in sorted(discovered.items()):
        previous = saved_products.get(code)
        if not isinstance(previous, dict):
            asset_ids = list(candidate.get("assets", {}))
            _send_product_notification(
                telegram,
                candidate,
                is_update=False,
                new_asset_ids=asset_ids,
            )
            saved_products[code] = _state_product(candidate)
            state.save()
            notifications += 1
            log_status("ADIDAS", code, "nuovo codice notificato")
            continue

        old_assets = previous.setdefault("assets", {})
        new_asset_ids = [
            asset_id
            for asset_id in candidate.get("assets", {})
            if asset_id not in old_assets
        ]
        if new_asset_ids:
            _send_product_notification(
                telegram,
                candidate,
                is_update=True,
                new_asset_ids=new_asset_ids,
            )
            notifications += 1
            log_status("ADIDAS", code, f"notificati {len(new_asset_ids)} nuovi asset")

        refreshed = _state_product(candidate, first_seen=previous.get("first_seen"))
        for asset_id, old_asset in old_assets.items():
            if asset_id not in refreshed["assets"]:
                refreshed["assets"][asset_id] = old_asset
        # Evita crescita illimitata se il motore indicizza molte trasformazioni
        # della stessa immagine nel corso degli anni.
        refreshed["assets"] = dict(list(refreshed["assets"].items())[-40:])
        saved_products[code] = refreshed
        if new_asset_ids:
            state.save()

    adidas_state["last_scan"] = now
    state.save()
    log_status(
        "ADIDAS",
        "SCAN",
        f"{len(discovered)} codici controllati, {notifications} notifiche",
    )
