from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

# Permette di riutilizzare telegram_client.py già presente nella root di LeakKit_JR.
MODULE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = MODULE_DIR.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from telegram_client import TelegramClient  # noqa: E402

TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"
ITALIAN_DISPLAY_FORMAT = "%d/%m/%Y %H:%M:%S"
ROME = ZoneInfo("Europe/Rome")
STATE_PATH = MODULE_DIR / "state.json"
FOUND_PATH = MODULE_DIR / "found_assets.json"
TARGETS_PATH = MODULE_DIR / "targets.json"


@dataclass(frozen=True)
class Target:
    name: str
    url_template: str

    def url_for(self, timestamp: datetime) -> str:
        return self.url_template.format(
            timestamp=timestamp.strftime(TIMESTAMP_FORMAT)
        )


@dataclass
class UrlResult:
    target: str
    timestamp: str
    url: str
    status: str  # found, missing, error
    http_status: int | None = None
    content_type: str | None = None
    content: bytes | None = None
    error: str | None = None


class GlobalRateLimiter:
    """Limite globale condiviso tra tutti i thread, con pausa globale."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("REQUESTS_PER_SECOND deve essere maggiore di zero")
        self._interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._pause_until = 0.0

    def pause(self, seconds: float) -> None:
        """Blocca l'avvio di nuove richieste per almeno ``seconds`` secondi."""
        if seconds <= 0:
            return
        with self._lock:
            self._pause_until = max(
                self._pause_until,
                time.monotonic() + seconds,
            )

    def wait(self) -> None:
        # Il lock non viene mantenuto durante tutta la pausa, così il thread
        # principale può estendere la pausa appena trova e invia un'immagine.
        while True:
            with self._lock:
                now = time.monotonic()
                ready_at = max(self._next_request_at, self._pause_until)
                delay = ready_at - now
                if delay <= 0:
                    self._next_request_at = max(
                        self._next_request_at,
                        now,
                    ) + self._interval
                    return
            time.sleep(min(delay, 0.25))


_thread_local = threading.local()


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/150.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )
        _thread_local.session = session
    return session


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_local_datetime(value: str) -> datetime:
    value = value.strip()
    formats = (
        ITALIAN_DISPLAY_FORMAT,
        DISPLAY_FORMAT,
        "%Y-%m-%dT%H:%M:%S",
        TIMESTAMP_FORMAT,
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=ROME)
        except ValueError:
            continue
    raise ValueError(
        f"Data non valida: {value!r}. Usa GG/MM/AAAA HH:MM:SS, "
        "YYYY-MM-DD HH:MM:SS oppure YYYYMMDDHHMMSS."
    )


def current_rome_time() -> datetime:
    return datetime.now(ROME).replace(microsecond=0)


def parse_workflow_started_at(value: str | None) -> datetime:
    """Converte il created_at del run GitHub da UTC all'ora di Roma."""
    if not value:
        return current_rome_time()

    cleaned = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ROME).replace(microsecond=0)


def compact(value: datetime) -> str:
    return value.strftime(TIMESTAMP_FORMAT)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def save_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_targets() -> list[Target]:
    raw = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    targets = [Target(**item) for item in raw]
    if not targets:
        raise RuntimeError("timestamp_scanner/targets.json non contiene target")
    return targets


def hour_start(value: datetime) -> datetime:
    """Restituisce l'inizio dell'ora locale che contiene ``value``."""
    return value.astimezone(ROME).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def hour_end(value: datetime) -> datetime:
    """Restituisce l'ultimo secondo dell'ora locale che contiene ``value``."""
    return hour_start(value) + timedelta(hours=1) - timedelta(seconds=1)


def latest_closed_timestamp(reference_time: datetime) -> datetime:
    """Ultimo secondo dell'ultima ora completamente conclusa."""
    return hour_start(reference_time) - timedelta(seconds=1)


def wait_until_hour_is_closed(cursor: datetime) -> datetime:
    """Attende la chiusura dell'ora del cursore e restituisce il cutoff."""
    available_at = hour_end(cursor) + timedelta(seconds=1)

    while True:
        now = current_rome_time()
        remaining_seconds = (available_at - now).total_seconds()
        if remaining_seconds <= 0:
            return latest_closed_timestamp(now)

        sleep_seconds = min(remaining_seconds, 300.0)
        print(
            "[ATTESA ORA] "
            f"finestra={compact(hour_start(cursor))}-"
            f"{compact(hour_end(cursor))} | "
            f"disponibile_alle={available_at.isoformat()} | "
            f"mancano={remaining_seconds:.0f}s"
        )
        time.sleep(sleep_seconds)


def new_state(reference_time: datetime) -> dict[str, Any]:
    start = hour_start(reference_time)
    return {
        "version": 2,
        "mode": "automatic_hourly",
        "timezone": "Europe/Rome",
        "scan_start": compact(start),
        "next_timestamp": compact(start),
        "last_checked_timestamp": None,
        "last_attempted_timestamp": None,
        "caught_up": True,
        "runs": 0,
        "checked_timestamps": 0,
        "checked_urls": 0,
        "found_assets": 0,
        "last_run": None,
        "updated_at_utc": None,
    }


def load_state(
    reference_time: datetime,
    *,
    reset: bool,
) -> dict[str, Any]:
    if reset or not STATE_PATH.exists():
        state = new_state(reference_time)
        save_json(STATE_PATH, state)
        return state

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    if not state.get("next_timestamp"):
        raise ValueError("state.json non contiene next_timestamp")

    # Migrazione trasparente dallo scanner con intervallo manuale. Il cursore
    # viene conservato: nessun secondo già raggiunto viene perso o saltato.
    if state.get("version") != 2 or state.get("mode") != "automatic_hourly":
        previous_version = state.get("version")
        state["version"] = 2
        state["mode"] = "automatic_hourly"
        state["timezone"] = "Europe/Rome"
        state.pop("final_end", None)
        state.pop("completed", None)
        cursor = datetime.strptime(
            state["next_timestamp"],
            TIMESTAMP_FORMAT,
        ).replace(tzinfo=ROME)
        state["caught_up"] = cursor > latest_closed_timestamp(reference_time)
        state["updated_at_utc"] = utc_now_iso()
        save_json(STATE_PATH, state)
        print(
            "[MIGRAZIONE STATO] "
            f"versione {previous_version} -> 2 | "
            f"riprendo da {state['next_timestamp']}"
        )

    return state


def load_found() -> dict[str, Any]:
    if not FOUND_PATH.exists():
        return {"version": 1, "assets": {}}
    payload = json.loads(FOUND_PATH.read_text(encoding="utf-8"))
    payload.setdefault("version", 1)
    payload.setdefault("assets", {})
    return payload


def looks_like_image(content: bytes, content_type: str | None) -> bool:
    if content_type and content_type.lower().startswith("image/"):
        return True
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return True
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if content.startswith(b"\xff\xd8\xff"):
        return True
    return False


def looks_like_block_page(content: bytes, content_type: str | None) -> bool:
    """Riconosce pagine anti-bot restituite anche con HTTP 200."""
    normalized_type = (content_type or "").lower()
    if "html" not in normalized_type and not content.lstrip().lower().startswith(
        (b"<!doctype html", b"<html")
    ):
        return False

    sample = content[:100_000].decode("utf-8", errors="ignore").lower()
    block_markers = (
        "cf-chl-",
        "cloudflare ray id",
        "checking your browser",
        "verify you are human",
        "captcha",
        "access denied",
        "request blocked",
        "bot detection",
        "px-captcha",
        "perimeterx",
        "incapsula",
        "akamai bot manager",
    )
    return any(marker in sample for marker in block_markers)


def request_image(
    url: str,
    limiter: GlobalRateLimiter,
    retries: int,
    timeout_seconds: int,
) -> tuple[int | None, str | None, bytes | None, str | None]:
    session = get_session()
    last_error: str | None = None

    for attempt in range(retries + 1):
        try:
            limiter.wait()
            response = session.get(
                url,
                timeout=(10, timeout_seconds),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min((2**attempt) + random.random(), 20.0))
                continue
            return None, None, None, last_error

        status = response.status_code
        content_type = response.headers.get("Content-Type")

        if status in {429, 500, 502, 503, 504} and attempt < retries:
            retry_after = response.headers.get("Retry-After", "").strip()
            if retry_after.isdigit():
                delay = min(float(retry_after), 60.0)
            else:
                delay = min((2**attempt) + random.random(), 30.0)
            response.close()
            time.sleep(delay)
            continue

        if status in {404, 410}:
            response.close()
            return status, content_type, None, None

        if status == 200:
            content = response.content
            response.close()
            if looks_like_image(content, content_type):
                return status, content_type, content, None
            if looks_like_block_page(content, content_type):
                return (
                    status,
                    content_type,
                    None,
                    "HTTP 200 con pagina anti-bot/CAPTCHA: scansione sospesa",
                )
            # Lo store può restituire normale HTML 200 per un asset inesistente.
            return status, content_type, None, None

        response.close()
        return (
            status,
            content_type,
            None,
            f"HTTP {status}: risposta inattesa o blocco temporaneo",
        )

    return None, None, None, last_error or "Errore HTTP sconosciuto"


def check_timestamp(
    timestamp: datetime,
    targets: list[Target],
    limiter: GlobalRateLimiter,
    retries: int,
    timeout_seconds: int,
) -> list[UrlResult]:
    results: list[UrlResult] = []
    timestamp_code = compact(timestamp)

    for target in targets:
        url = target.url_for(timestamp)
        status, content_type, content, error = request_image(
            url,
            limiter,
            retries,
            timeout_seconds,
        )

        if status in {404, 410} or (
            status == 200 and content is None and error is None
        ):
            results.append(
                UrlResult(
                    target=target.name,
                    timestamp=timestamp_code,
                    url=url,
                    status="missing",
                    http_status=status,
                    content_type=content_type,
                )
            )
            continue

        if status == 200 and content is not None:
            results.append(
                UrlResult(
                    target=target.name,
                    timestamp=timestamp_code,
                    url=url,
                    status="found",
                    http_status=status,
                    content_type=content_type,
                    content=content,
                )
            )
            continue

        results.append(
            UrlResult(
                target=target.name,
                timestamp=timestamp_code,
                url=url,
                status="error",
                http_status=status,
                content_type=content_type,
                error=error or "Errore sconosciuto",
            )
        )
        # Se il CDN sta fallendo, questo secondo verrà ritentato.
        break

    return results


def chunks_of_seconds(
    start: datetime,
    end: datetime,
    chunk_size: int,
) -> Iterable[list[datetime]]:
    cursor = start
    while cursor <= end:
        chunk: list[datetime] = []
        while cursor <= end and len(chunk) < chunk_size:
            chunk.append(cursor)
            cursor += timedelta(seconds=1)
        yield chunk


def extension_for(content_type: str | None) -> str:
    normalized = (content_type or "").lower()
    if "png" in normalized:
        return ".png"
    if "jpeg" in normalized or "jpg" in normalized:
        return ".jpg"
    return ".webp"


def asset_caption(result: UrlResult) -> str:
    """Crea una didascalia Telegram compatta con un link cliccabile."""
    target = escape(result.target)
    timestamp = escape(result.timestamp)
    url = escape(result.url, quote=True)
    return (
        "🚨 <b>Nuovo asset Juventus</b>\n\n"
        f"📁 {target}  •  <code>{timestamp}</code>\n"
        f'🔗 <a href="{url}">Apri immagine</a>'
    )


def send_new_asset(
    telegram: TelegramClient,
    result: UrlResult,
) -> str:
    """Invia l'asset come foto o, se necessario, come file originale."""
    if result.content is None:
        raise RuntimeError("Contenuto immagine mancante")

    caption = asset_caption(result)
    filename = (
        f"{result.target}-{result.timestamp}"
        f"{extension_for(result.content_type)}"
    )
    mime_type = result.content_type or "image/webp"

    try:
        telegram.send_photo_bytes(
            result.content,
            filename,
            caption=caption,
            mime_type=mime_type,
            parse_mode="HTML",
        )
        return "photo"
    except RuntimeError as exc:
        error_text = str(exc).upper()
        recoverable_photo_errors = (
            "PHOTO_INVALID_DIMENSIONS",
            "IMAGE_PROCESS_FAILED",
        )
        if not any(
            marker in error_text
            for marker in recoverable_photo_errors
        ):
            raise

        print(
            "[TELEGRAM FALLBACK] Telegram non accetta l'asset come foto "
            f"({exc}). Lo invio come file originale."
        )
        telegram.send_document_bytes(
            result.content,
            filename,
            caption=caption,
            mime_type=mime_type,
            parse_mode="HTML",
        )
        return "document"


def pause_after_asset(
    limiter: GlobalRateLimiter,
    seconds: float,
    result: UrlResult,
) -> None:
    """Pausa la scansione senza chiudere il client Telegram o terminare il run."""
    if seconds <= 0:
        return

    limiter.pause(seconds)
    print(
        "[PAUSA DOPO INVIO] "
        f"{result.timestamp} [{result.target}] | "
        f"attendo {seconds:g} secondi senza chiudere il bot..."
    )
    time.sleep(seconds)
    print(
        "[RIPRESA SCANSIONE] "
        f"continuo dopo {result.timestamp}; client Telegram ancora attivo."
    )


def set_last_run(
    state: dict[str, Any],
    *,
    run_id: str,
    workflow_started_at: datetime,
    latest_closed_at: datetime,
    window_start: datetime | None,
    window_end: datetime | None,
    started_at_utc: str,
    finished_at_utc: str | None,
    stop_reason: str,
    checked_in_run: int,
    found_in_run: int,
    fatal_error: str | None = None,
) -> None:
    state["last_run"] = {
        "run_id": run_id,
        "workflow_started_at_rome": workflow_started_at.isoformat(),
        "latest_closed_timestamp": compact(latest_closed_at),
        "window_start": compact(window_start) if window_start else None,
        "window_end": compact(window_end) if window_end else None,
        "scanner_started_at_utc": started_at_utc,
        "scanner_finished_at_utc": finished_at_utc,
        "stop_reason": stop_reason,
        "checked_timestamps": checked_in_run,
        "found_assets": found_in_run,
        "fatal_error": fatal_error,
    }
    state["updated_at_utc"] = finished_at_utc or utc_now_iso()


def run_scan(args: argparse.Namespace) -> int:
    workflow_started_at = parse_workflow_started_at(
        os.getenv("RUN_STARTED_AT_UTC")
    )
    latest_closed_at = latest_closed_timestamp(workflow_started_at)

    reset = args.reset_state or env_bool("RESET_STATE")
    state = load_state(workflow_started_at, reset=reset)
    found = load_found()
    targets = load_targets()
    state["found_assets"] = len(found["assets"])

    run_id = os.getenv("GITHUB_RUN_ID", "local")
    scanner_started_at_utc = utc_now_iso()
    state["runs"] = int(state.get("runs", 0)) + 1

    cursor = datetime.strptime(
        state["next_timestamp"],
        TIMESTAMP_FORMAT,
    ).replace(tzinfo=ROME)

    if (
        cursor > latest_closed_at
        and not args.dry_run
        and env_bool("WAIT_FOR_CLOSED_HOUR")
    ):
        latest_closed_at = wait_until_hour_is_closed(cursor)

    if cursor > latest_closed_at:
        state["caught_up"] = True
        set_last_run(
            state,
            run_id=run_id,
            workflow_started_at=workflow_started_at,
            latest_closed_at=latest_closed_at,
            window_start=None,
            window_end=None,
            started_at_utc=scanner_started_at_utc,
            finished_at_utc=utc_now_iso(),
            stop_reason="waiting_for_hour",
            checked_in_run=0,
            found_in_run=0,
        )
        save_json(STATE_PATH, state)
        next_open_hour = hour_start(workflow_started_at) + timedelta(hours=1)
        print(
            "[IN ATTESA] Nessuna ora completa da controllare | "
            f"prossimo={state['next_timestamp']} | "
            f"nuova finestra disponibile alle {next_open_hour.isoformat()}"
        )
        return 0

    run_cutoff = min(hour_end(cursor), latest_closed_at)
    state["caught_up"] = False

    if args.dry_run:
        total_timestamps = int((run_cutoff - cursor).total_seconds()) + 1
        print(f"Primo codice: {compact(cursor)}")
        print(f"Fine della finestra oraria: {compact(run_cutoff)}")
        print(f"Ultimo secondo già chiuso: {compact(latest_closed_at)}")
        print(f"Timestamp della finestra: {total_timestamps:,}")
        print(f"URL della finestra: {total_timestamps * len(targets):,}")
        return 0

    requests_per_second = env_float("REQUESTS_PER_SECOND", 20.0)
    concurrency = env_int("CONCURRENCY", 30)
    retries = env_int("RETRIES", 3)
    timeout_seconds = env_int("HTTP_TIMEOUT_SECONDS", 25)
    chunk_size = env_int("CHUNK_TIMESTAMPS", 300)
    checkpoint_every = max(1, env_int("CHECKPOINT_EVERY", 1))
    max_runtime_seconds = env_int("MAX_RUNTIME_SECONDS", 3_600)
    pause_after_asset_seconds = max(
        0.0,
        env_float("PAUSE_AFTER_ASSET_SECONDS", 20.0),
    )

    print(
        "[TIMESTAMP SCANNER] "
        f"{compact(cursor)} -> {compact(run_cutoff)} | "
        f"ultimo secondo chiuso {compact(latest_closed_at)} | "
        f"{len(targets)} target | {requests_per_second:g} req/s | "
        f"pausa dopo asset {pause_after_asset_seconds:g}s"
    )

    limiter = GlobalRateLimiter(requests_per_second)
    checked_in_run = 0
    found_in_run = 0
    started_monotonic = time.monotonic()
    stop_reason = "hour_completed"
    fatal_error: str | None = None

    # Viene creato una volta e chiuso soltanto alla vera fine del run.
    telegram = TelegramClient(dry_run=False)

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            for chunk in chunks_of_seconds(cursor, run_cutoff, chunk_size):
                elapsed = time.monotonic() - started_monotonic
                if elapsed >= max_runtime_seconds:
                    stop_reason = "time_budget"
                    break

                mapped = executor.map(
                    lambda ts: check_timestamp(
                        ts,
                        targets,
                        limiter,
                        retries,
                        timeout_seconds,
                    ),
                    chunk,
                )

                stop_after_timestamp = False
                for timestamp, results in zip(chunk, mapped, strict=True):
                    elapsed = time.monotonic() - started_monotonic
                    if elapsed >= max_runtime_seconds:
                        stop_reason = "time_budget"
                        stop_after_timestamp = True
                        break

                    state["last_attempted_timestamp"] = compact(timestamp)
                    errors = [
                        result
                        for result in results
                        if result.status == "error"
                    ]
                    if errors:
                        first_error = errors[0]
                        stop_reason = "transient_http_error"
                        fatal_error = (
                            f"{first_error.timestamp} [{first_error.target}] "
                            f"{first_error.error}"
                        )
                        print(f"[ERRORE] {fatal_error}", file=sys.stderr)
                        stop_after_timestamp = True
                        break

                    try:
                        for result in results:
                            if result.status != "found":
                                continue

                            if result.url in found["assets"]:
                                print(f"[GIÀ INVIATO] {result.url}")
                                continue

                            telegram_mode = send_new_asset(
                                telegram,
                                result,
                            )
                            found["assets"][result.url] = {
                                "target": result.target,
                                "timestamp": result.timestamp,
                                "url": result.url,
                                "content_type": result.content_type,
                                "telegram_mode": telegram_mode,
                                "telegram_sent_at_utc": utc_now_iso(),
                            }
                            found_in_run += 1
                            state["found_assets"] = len(found["assets"])
                            save_json(FOUND_PATH, found)
                            print(
                                "[TROVATO E INVIATO] "
                                f"modalità={telegram_mode} | {result.url}"
                            )

                            # Non esce dal ciclo e non chiude Telegram:
                            # attende e poi continua nello stesso processo.
                            pause_after_asset(
                                limiter,
                                pause_after_asset_seconds,
                                result,
                            )
                    except Exception as exc:
                        # Non avanziamo il cursore: lo stesso timestamp sarà
                        # riprovato nel run successivo.
                        stop_reason = "telegram_error"
                        fatal_error = f"{compact(timestamp)} Telegram: {exc}"
                        print(f"[ERRORE] {fatal_error}", file=sys.stderr)
                        stop_after_timestamp = True
                        break

                    checked_in_run += 1
                    state["checked_timestamps"] = int(
                        state.get("checked_timestamps", 0)
                    ) + 1
                    state["checked_urls"] = int(
                        state.get("checked_urls", 0)
                    ) + len(targets)
                    state["last_checked_timestamp"] = compact(timestamp)
                    next_timestamp = timestamp + timedelta(seconds=1)
                    state["next_timestamp"] = compact(next_timestamp)
                    state["caught_up"] = next_timestamp > latest_closed_at

                    if checked_in_run % checkpoint_every == 0:
                        set_last_run(
                            state,
                            run_id=run_id,
                            workflow_started_at=workflow_started_at,
                            latest_closed_at=latest_closed_at,
                            window_start=cursor,
                            window_end=run_cutoff,
                            started_at_utc=scanner_started_at_utc,
                            finished_at_utc=None,
                            stop_reason="running",
                            checked_in_run=checked_in_run,
                            found_in_run=found_in_run,
                        )
                        save_json(STATE_PATH, state)

                if stop_after_timestamp:
                    break
    finally:
        # La chiusura avviene qui, non dopo il singolo invio.
        telegram.close()

    finished_at_utc = utc_now_iso()
    set_last_run(
        state,
        run_id=run_id,
        workflow_started_at=workflow_started_at,
        latest_closed_at=latest_closed_at,
        window_start=cursor,
        window_end=run_cutoff,
        started_at_utc=scanner_started_at_utc,
        finished_at_utc=finished_at_utc,
        stop_reason=stop_reason,
        checked_in_run=checked_in_run,
        found_in_run=found_in_run,
        fatal_error=fatal_error,
    )
    save_json(STATE_PATH, state)
    save_json(FOUND_PATH, found)

    print(
        "[TIMESTAMP SCANNER] Fine run | "
        f"motivo={stop_reason} | controllati={checked_in_run:,} | "
        f"trovati={found_in_run} | prossimo={state['next_timestamp']} | "
        f"finestra_fino_a={compact(run_cutoff)} | "
        f"in_pari={str(state['caught_up']).lower()}"
    )

    # Errori HTTP/Telegram temporanei lasciano il cursore sul primo secondo
    # incompleto e il workflow successivo lo riprova.
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scanner al secondo degli asset pubblici dello store Juventus"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra l'intervallo del run senza effettuare richieste",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Riparte dall'ora corrente preservando gli asset già inviati",
    )
    args = parser.parse_args()

    try:
        return run_scan(args)
    except Exception as exc:
        print(
            f"[ERRORE FATALE] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        try:
            if STATE_PATH.exists():
                state = json.loads(
                    STATE_PATH.read_text(encoding="utf-8")
                )
                previous_last_run = state.get("last_run") or {}
                state["last_run"] = {
                    **previous_last_run,
                    "run_id": os.getenv("GITHUB_RUN_ID", "local"),
                    "stop_reason": "fatal_error",
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                    "scanner_finished_at_utc": utc_now_iso(),
                }
                state["updated_at_utc"] = utc_now_iso()
                save_json(STATE_PATH, state)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
