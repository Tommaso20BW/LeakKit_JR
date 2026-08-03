"""Archivio JSON unico e migrazione dei vecchi file di stato."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import ROOT_DIR, log_status


STATE_FILE = ROOT_DIR / ".leakkit_state.json"
STATE_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "fonts": {},
        "store_products": {},
        "news": {"initialized": False, "articles": {}},
        "adidas": {"initialized": False, "products": {}},
    }


class StateStore:
    """Mantiene tutti i monitor nello stesso file, con scritture atomiche."""

    def __init__(self, path: Path = STATE_FILE, read_only: bool = False):
        self.path = path
        self.read_only = read_only
        self.data = self._load()
        self._ensure_shape()
        if self._migrate_legacy_files():
            self.save()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()

        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"{self.path.name} non valido; interrompo per evitare duplicati"
            ) from error

        if not isinstance(data, dict):
            raise RuntimeError(f"{self.path.name} deve contenere un oggetto JSON")
        return data

    def _ensure_shape(self) -> None:
        defaults = empty_state()
        self.data["version"] = STATE_VERSION
        for key in ("fonts", "store_products"):
            if not isinstance(self.data.get(key), dict):
                self.data[key] = defaults[key]

        for key in ("news", "adidas"):
            if not isinstance(self.data.get(key), dict):
                self.data[key] = copy.deepcopy(defaults[key])

        news = self.data["news"]
        news.setdefault("initialized", False)
        if not isinstance(news.get("articles"), dict):
            news["articles"] = {}

        adidas = self.data["adidas"]
        adidas.setdefault("initialized", False)
        if not isinstance(adidas.get("products"), dict):
            adidas["products"] = {}

    def _migrate_legacy_files(self) -> bool:
        """Importa flag/notizie precedenti e poi elimina i file ormai inutili."""

        changed = False
        migrated_at = utc_now()

        state_dir = self.path.parent

        for legacy in sorted(state_dir.glob(".found-font-*")):
            kit = legacy.name.removeprefix(".found-font-")
            self.data["fonts"].setdefault(
                kit,
                {"notified": True, "migrated_at": migrated_at},
            )
            if not self.read_only:
                legacy.unlink()
            changed = True

        for legacy in sorted(state_dir.glob(".found-product-*")):
            code = legacy.name.removeprefix(".found-product-")
            self.data["store_products"].setdefault(
                code,
                {"notified": True, "migrated_at": migrated_at},
            )
            if not self.read_only:
                legacy.unlink()
            changed = True

        legacy_news = state_dir / ".seen_news.json"
        if legacy_news.exists():
            self._merge_legacy_news(legacy_news)
            if not self.read_only:
                legacy_news.unlink()
            changed = True

        if changed:
            log_status("STATE", "MIGRAZIONE", "vecchi file importati nel JSON unico")
        return changed

    def _merge_legacy_news(self, path: Path) -> None:
        try:
            with path.open("r", encoding="utf-8") as file:
                legacy = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"{path.name} non valido; migrazione interrotta"
            ) from error

        articles: dict[str, Any]
        initialized = False
        if isinstance(legacy, list):
            articles = {url: None for url in legacy if isinstance(url, str)}
        elif isinstance(legacy, dict) and isinstance(legacy.get("articles"), dict):
            articles = legacy["articles"]
            initialized = bool(
                legacy.get("initialized", legacy.get("version") == 2)
            )
        else:
            raise RuntimeError(f"formato di {path.name} non riconosciuto")

        target = self.data["news"]
        for url, article in articles.items():
            target["articles"].setdefault(url, article)
        target["initialized"] = bool(target.get("initialized") or initialized)

    def section(self, name: str) -> dict[str, Any]:
        section = self.data.get(name)
        if not isinstance(section, dict):
            section = {}
            self.data[name] = section
        return section

    def save(self) -> None:
        if self.read_only:
            return
        self.data["version"] = STATE_VERSION
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, self.path)
