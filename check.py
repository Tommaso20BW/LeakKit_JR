"""Punto di ingresso unico per tutti i monitor LeakKit JR."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable

import font_monitor
import news_monitor
import store_product_monitor
from common import log_status
from state_store import StateStore
from telegram_client import TelegramClient


Monitor = Callable[[StateStore, TelegramClient], None]
MONITORS: dict[str, Monitor] = {
    "fonts": font_monitor.run,
    "products": store_product_monitor.run,
    "news": news_monitor.run,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Esegue i monitor LeakKit JR")
    parser.add_argument(
        "--only",
        action="append",
        choices=tuple(MONITORS),
        help="esegue solo il monitor indicato; ripetibile",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="controlla le sorgenti senza inviare messaggi o salvare lo stato",
    )
    parser.add_argument(
        "--migrate-state",
        action="store_true",
        help="importa i vecchi file nel JSON unico e termina",
    )
    return parser.parse_args(argv)


def run_monitors(
    state: StateStore,
    telegram: TelegramClient,
    selected: list[str],
) -> list[str]:
    failures: list[str] = []
    for name in selected:
        log_status("RUN", name.upper(), "avvio")
        try:
            MONITORS[name](state, telegram)
        except Exception as error:
            failures.append(name)
            log_status("ERROR", name.upper(), str(error))
            traceback.print_exc()
        else:
            log_status("RUN", name.upper(), "completato")
    return failures


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = StateStore(read_only=args.dry_run)
    if args.migrate_state:
        log_status("STATE", "JSON", "migrazione completata")
        return 0

    telegram = TelegramClient(dry_run=args.dry_run)
    selected = list(dict.fromkeys(args.only or MONITORS.keys()))
    failures = run_monitors(state, telegram, selected)
    if failures:
        print(
            "Monitor terminati con errori: " + ", ".join(failures),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
