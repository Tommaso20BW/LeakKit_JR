from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
ROME = ZoneInfo("Europe/Rome")

STATE_PATH = MODULE_DIR / "state.json"
FOUND_PATH = MODULE_DIR / "found_assets.json"
TARGETS_PATH = MODULE_DIR / "targets.json"

DEFAULT_SCAN_START = "2026-07-27 00:00:00"
DEFAULT_FINAL_END = "2026-08-08 23:59:59"


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
    """Limite globale condiviso tra tutti i thread."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("REQUESTS_PER_SECOND deve essere maggiore di zero")
        self._interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next_request_at - now
            if delay > 0:
                time.sleep(delay)
            self._next_request_at = max(
                self._next_request_at,
                time.monotonic(),
            ) + self._interval


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
    for fmt in (DISPLAY_FORMAT, "%Y-%m-%dT%H:%M:%S", TIMESTAMP_FORMAT):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=ROME)
        except ValueError:
            continue
    raise ValueError(
        f"Data non valida: {value!r}. Usa YYYY-MM-DD HH:MM:SS "
        "oppure YYYYMMDDHHMMSS."
    )


def parse_workflow_started_at(value: str | None) -> datetime:
    """Converte il created_at del run GitHub da UTC all'ora di Roma."""
    if not value:
        return datetime.now(ROME).replace(microsecond=0)

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


def new_state(start: datetime, final_end: datetime) -> dict[str, Any]:
    return {
        "version": 1,
        "timezone": "Europe/Rome",
        "scan_start": compact(start),
        "final_end": compact(final_end),
        "next_timestamp": compact(start),
        "last_checked_timestamp": None,
        "last_attempted_timestamp": None,
        "completed": False,
        "runs": 0,
        "checked_timestamps": 0,
        "checked_urls": 0,
        "found_assets": 0,
        "last_run": None,
        "updated_at_utc": None,
    }


def load_state(
    start: datetime,
    final_end: datetime,
    *,
    reset: bool,
) -> dict[str, Any]:
    expected_start = compact(start)
    expected_end = compact(final_end)

    if reset or not STATE_PATH.exists():
        state = new_state(start, final_end)
        save_json(STATE_PATH, state)
        return state

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if state.get("scan_start") != expected_start:
        raise RuntimeError(
            "SCAN_START non coincide con state.json. Avvia manualmente il "
            "workflow con reset_state=true per ripartire."
        )
    if state.get("final_end") != expected_end:
        raise RuntimeError(
            "SCAN_FINAL_END non coincide con state.json. Avvia manualmente il "
            "workflow con reset_state=true per ripartire."
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
            return (
                status,
                content_type,
                None,
                f"HTTP 200 ma contenuto non riconosciuto come immagine ({content_type})",
            )

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

        if status in {404, 410}:
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
        # Non ha senso interrogare gli altri target dello stesso secondo se il
        # CDN sta già rispondendo con un errore: quel secondo sarà ritentato.
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


def send_new_asset(
    telegram: TelegramClient,
    result: UrlResult,
) -> None:
    if result.content is None:
        raise RuntimeError("Contenuto immagine mancante")

    caption = (
        "🚨 NUOVO ASSET JUVENTUS TROVATO\n\n"
        f"Cartella: {result.target}\n"
        f"Codice: {result.timestamp}\n"
        f"URL: {result.url}"
    )
    filename = f"{result.target}-{result.timestamp}{extension_for(result.content_type)}"
    telegram.send_photo_bytes(
        result.content,
        filename,
        caption=caption,
        mime_type=result.content_type or "image/webp",
    )


def set_last_run(
    state: dict[str, Any],
    *,
    run_id: str,
    workflow_started_at: datetime,
    cutoff: datetime,
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
        "cutoff": compact(cutoff),
        "scanner_started_at_utc": started_at_utc,
        "scanner_finished_at_utc": finished_at_utc,
        "stop_reason": stop_reason,
        "checked_timestamps": checked_in_run,
        "found_assets": found_in_run,
        "fatal_error": fatal_error,
    }
    state["updated_at_utc"] = finished_at_utc or utc_now_iso()


def run_scan(args: argparse.Namespace) -> int:
    scan_start = parse_local_datetime(
        os.getenv("SCAN_START", DEFAULT_SCAN_START)
    )
    final_end = parse_local_datetime(
        os.getenv("SCAN_FINAL_END", DEFAULT_FINAL_END)
    )
    workflow_started_at = parse_workflow_started_at(
        os.getenv("RUN_STARTED_AT_UTC")
    )
    run_cutoff = min(workflow_started_at, final_end)

    if scan_start > final_end:
        raise ValueError("SCAN_START deve essere precedente a SCAN_FINAL_END")

    reset = args.reset_state or env_bool("RESET_STATE")
    state = load_state(scan_start, final_end, reset=reset)
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

    if cursor > final_end:
        state["completed"] = True
        set_last_run(
            state,
            run_id=run_id,
            workflow_started_at=workflow_started_at,
            cutoff=run_cutoff,
            started_at_utc=scanner_started_at_utc,
            finished_at_utc=utc_now_iso(),
            stop_reason="completed",
            checked_in_run=0,
            found_in_run=0,
        )
        save_json(STATE_PATH, state)
        print("Intervallo definitivo già completato.")
        return 0

    if cursor > run_cutoff:
        # Il precedente run è già arrivato oltre l'istante in cui questo run è
        # stato creato. Non si guarda nel futuro: il workflow successivo avrà
        # un nuovo cutoff.
        set_last_run(
            state,
            run_id=run_id,
            workflow_started_at=workflow_started_at,
            cutoff=run_cutoff,
            started_at_utc=scanner_started_at_utc,
            finished_at_utc=utc_now_iso(),
            stop_reason="caught_up",
            checked_in_run=0,
            found_in_run=0,
        )
        save_json(STATE_PATH, state)
        print(
            "Nessun secondo da controllare in questo run: "
            f"next={state['next_timestamp']} cutoff={compact(run_cutoff)}"
        )
        return 0

    if args.dry_run:
        total_timestamps = int((run_cutoff - cursor).total_seconds()) + 1
        print(f"Primo codice: {compact(cursor)}")
        print(f"Cutoff congelato all'avvio: {compact(run_cutoff)}")
        print(f"Ultimo limite definitivo: {compact(final_end)}")
        print(f"Timestamp disponibili nel run: {total_timestamps:,}")
        print(f"URL disponibili nel run: {total_timestamps * len(targets):,}")
        return 0

    requests_per_second = env_float("REQUESTS_PER_SECOND", 20.0)
    concurrency = env_int("CONCURRENCY", 30)
    retries = env_int("RETRIES", 3)
    timeout_seconds = env_int("HTTP_TIMEOUT_SECONDS", 25)
    chunk_size = env_int("CHUNK_TIMESTAMPS", 300)
    checkpoint_every = max(1, env_int("CHECKPOINT_EVERY", 1))
    max_runtime_seconds = env_int("MAX_RUNTIME_SECONDS", 18_000)

    print(
        "[TIMESTAMP SCANNER] "
        f"{compact(cursor)} → {compact(run_cutoff)} | "
        f"limite definitivo {compact(final_end)} | "
        f"{len(targets)} target | {requests_per_second:g} req/s"
    )

    limiter = GlobalRateLimiter(requests_per_second)
    checked_in_run = 0
    found_in_run = 0
    started_monotonic = time.monotonic()
    stop_reason = "cutoff_reached"
    fatal_error: str | None = None

    # Il client esistente legge i due secret già configurati nel repository.
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
                    state["last_attempted_timestamp"] = compact(timestamp)

                    errors = [result for result in results if result.status == "error"]
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

                            send_new_asset(telegram, result)
                            found["assets"][result.url] = {
                                "target": result.target,
                                "timestamp": result.timestamp,
                                "url": result.url,
                                "content_type": result.content_type,
                                "telegram_sent_at_utc": utc_now_iso(),
                            }
                            found_in_run += 1
                            state["found_assets"] = len(found["assets"])
                            save_json(FOUND_PATH, found)
                            print(f"[TROVATO E INVIATO] {result.url}")
                    except Exception as exc:
                        # Non avanziamo il cursore: al prossimo run questo stesso
                        # timestamp viene riprovato, senza perdere la foto.
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
                    state["next_timestamp"] = compact(
                        timestamp + timedelta(seconds=1)
                    )
                    state["completed"] = (
                        timestamp + timedelta(seconds=1) > final_end
                    )

                    # Per impostazione predefinita registra nel JSON ogni codice
                    # completato. Il file conserva solo l'ultimo codice, non un
                    # elenco da milioni di righe.
                    if checked_in_run % checkpoint_every == 0:
                        set_last_run(
                            state,
                            run_id=run_id,
                            workflow_started_at=workflow_started_at,
                            cutoff=run_cutoff,
                            started_at_utc=scanner_started_at_utc,
                            finished_at_utc=None,
                            stop_reason="running",
                            checked_in_run=checked_in_run,
                            found_in_run=found_in_run,
                        )
                        save_json(STATE_PATH, state)

                    if state["completed"]:
                        stop_reason = "completed"
                        stop_after_timestamp = True
                        break

                if stop_after_timestamp:
                    break

    finally:
        telegram.close()

    finished_at_utc = utc_now_iso()
    set_last_run(
        state,
        run_id=run_id,
        workflow_started_at=workflow_started_at,
        cutoff=run_cutoff,
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
        f"cutoff={compact(run_cutoff)}"
    )

    # Gli errori HTTP/Telegram sono trattati come temporanei: il JSON resta sul
    # primo secondo incompleto e il workflow successivo lo riprova.
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
        help="Riparte da SCAN_START preservando gli asset già inviati",
    )
    args = parser.parse_args()

    try:
        return run_scan(args)
    except Exception as exc:
        print(f"[ERRORE FATALE] {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            # Segnale leggibile dal workflow per evitare un ciclo infinito in
            # caso di errore di configurazione o codice.
            if STATE_PATH.exists():
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                state["last_run"] = {
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
