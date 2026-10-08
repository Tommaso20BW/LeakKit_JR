import argparse
from datetime import datetime
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import timestamp_scanner.scanner as scanner_module  # noqa: E402
from timestamp_scanner.scanner import (  # noqa: E402
    ROME,
    GlobalRateLimiter,
    Target,
    UrlResult,
    asset_caption,
    compact,
    hour_end,
    hour_start,
    latest_closed_timestamp,
    parse_local_datetime,
    parse_workflow_started_at,
    send_new_assets,
    wait_until_hour_is_closed,
)


class ScannerTests(unittest.TestCase):
    def test_compact_timestamp_includes_seconds(self) -> None:
        value = parse_local_datetime("2026-05-12 10:37:59")
        self.assertEqual(compact(value), "20260512103759")

    def test_italian_date_format(self) -> None:
        value = parse_local_datetime("12/05/2026 10:37:59")
        self.assertEqual(compact(value), "20260512103759")

    def test_target_url(self) -> None:
        target = Target(
            "categories",
            "https://example.test/{timestamp}.webp",
        )
        value = datetime(2026, 7, 21, 10, 55, 42, tzinfo=ROME)
        self.assertTrue(
            target.url_for(value).endswith("/20260721105542.webp")
        )

    def test_github_utc_is_converted_to_rome_summer_time(self) -> None:
        value = parse_workflow_started_at("2026-08-06T13:42:00Z")
        self.assertEqual(
            value,
            datetime(2026, 8, 6, 15, 42, 0, tzinfo=ROME),
        )

    def test_final_timestamp_has_seconds(self) -> None:
        value = parse_local_datetime("08/08/2026 23:59:59")
        self.assertEqual(compact(value), "20260808235959")

    def test_pause_can_be_scheduled(self) -> None:
        limiter = GlobalRateLimiter(20)
        limiter.pause(0)
        limiter.wait()

    def test_hour_boundaries_use_rome_time(self) -> None:
        value = parse_local_datetime("09/08/2026 01:37:42")
        self.assertEqual(compact(hour_start(value)), "20260809010000")
        self.assertEqual(compact(hour_end(value)), "20260809015959")

    def test_only_fully_closed_hours_are_available(self) -> None:
        value = parse_local_datetime("09/08/2026 01:00:00")
        self.assertEqual(
            compact(latest_closed_timestamp(value)),
            "20260809005959",
        )

    def test_wait_reaches_the_end_of_the_current_hour(self) -> None:
        cursor = parse_local_datetime("09/08/2026 00:00:00")
        clock = [
            parse_local_datetime("09/08/2026 00:59:58"),
            parse_local_datetime("09/08/2026 01:00:00"),
        ]
        with (
            patch.object(
                scanner_module,
                "current_rome_time",
                side_effect=clock,
            ),
            patch.object(scanner_module.time, "sleep") as sleep,
        ):
            cutoff = wait_until_hour_is_closed(cursor)

        self.assertEqual(compact(cutoff), "20260809005959")
        sleep.assert_called_once_with(2.0)

    def test_v1_state_migration_preserves_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "scan_start": "20260727000000",
                        "final_end": "20260808235959",
                        "next_timestamp": "20260807113753",
                        "completed": False,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(scanner_module, "STATE_PATH", state_path):
                state = scanner_module.load_state(
                    parse_local_datetime("09/08/2026 02:00:00"),
                    reset=False,
                )

            self.assertEqual(state["version"], 2)
            self.assertEqual(state["mode"], "automatic_hourly")
            self.assertEqual(state["next_timestamp"], "20260807113753")
            self.assertFalse(state["caught_up"])
            self.assertNotIn("final_end", state)
            self.assertNotIn("completed", state)

    def test_reset_starts_from_current_hour(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "scan_start": "20260805000000",
                        "final_end": "20260807235959",
                        "next_timestamp": "20260806123456",
                        "completed": False,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(scanner_module, "STATE_PATH", state_path):
                state = scanner_module.load_state(
                    parse_local_datetime("09/08/2026 12:34:56"),
                    reset=True,
                )

            self.assertEqual(state["scan_start"], "20260809120000")
            self.assertEqual(state["next_timestamp"], "20260809120000")
            self.assertTrue(state["caught_up"])

    def test_interrupted_hour_resumes_from_first_incomplete_second(self) -> None:
        class DummyTelegram:
            def __init__(self, dry_run: bool = False) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            state_path = temporary_path / "state.json"
            found_path = temporary_path / "found_assets.json"
            state = scanner_module.new_state(
                parse_local_datetime("09/08/2026 00:00:00")
            )
            state["next_timestamp"] = "20260809005957"
            state["caught_up"] = False
            state_path.write_text(json.dumps(state), encoding="utf-8")

            should_fail = {"value": True}

            def fake_check(timestamp, *args, **kwargs):
                timestamp_code = compact(timestamp)
                if should_fail["value"] and timestamp_code == "20260809005958":
                    return [
                        UrlResult(
                            target="categories",
                            timestamp=timestamp_code,
                            url=f"https://example.test/{timestamp_code}.webp",
                            status="error",
                            error="blocco temporaneo",
                        )
                    ]
                return [
                    UrlResult(
                        target="categories",
                        timestamp=timestamp_code,
                        url=f"https://example.test/{timestamp_code}.webp",
                        status="missing",
                        http_status=404,
                    )
                ]

            environment = {
                "RUN_STARTED_AT_UTC": "2026-08-08T23:00:00Z",
                "CHUNK_TIMESTAMPS": "3",
                "CHECKPOINT_EVERY": "1",
                "RESET_STATE": "false",
            }
            patches = (
                patch.object(scanner_module, "STATE_PATH", state_path),
                patch.object(scanner_module, "FOUND_PATH", found_path),
                patch.object(
                    scanner_module,
                    "load_targets",
                    return_value=[
                        Target(
                            "categories",
                            "https://example.test/{timestamp}.webp",
                        )
                    ],
                ),
                patch.object(scanner_module, "TelegramClient", DummyTelegram),
                patch.object(scanner_module, "check_timestamp", fake_check),
                patch.dict(scanner_module.os.environ, environment, clear=False),
            )

            for active_patch in patches:
                active_patch.start()
                self.addCleanup(active_patch.stop)

            args = argparse.Namespace(dry_run=False, reset_state=False)
            self.assertEqual(scanner_module.run_scan(args), 0)
            interrupted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(interrupted["next_timestamp"], "20260809005958")
            self.assertEqual(
                interrupted["last_run"]["stop_reason"],
                "transient_http_error",
            )

            should_fail["value"] = False
            self.assertEqual(scanner_module.run_scan(args), 0)
            resumed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed["next_timestamp"], "20260809010000")
            self.assertTrue(resumed["caught_up"])
            self.assertEqual(resumed["last_run"]["window_end"], "20260809005959")


class FakeTelegram:
    def __init__(self, rich_error: Exception | None = None) -> None:
        self.rich_error = rich_error
        self.rich_calls: list[dict] = []

    def send_rich_gallery_bytes(self, **kwargs):
        self.rich_calls.append(kwargs)
        if self.rich_error is not None:
            raise self.rich_error
        return {"message_id": len(self.rich_calls)}


class TelegramGalleryTests(unittest.TestCase):
    def make_result(
        self,
        timestamp: str = "20260806162654",
        target: str = "categories",
    ) -> UrlResult:
        return UrlResult(
            target=target,
            timestamp=timestamp,
            url=f"https://example.test/{timestamp}.webp",
            status="found",
            content_type="image/webp",
            content=b"RIFFxxxxWEBPpayload",
        )

    def test_gallery_sends_one_rich_message_for_multiple_images(self) -> None:
        telegram = FakeTelegram()
        results = [
            self.make_result("20260806162654"),
            self.make_result("20260806162655"),
            self.make_result("20260806162656"),
        ]

        sent = send_new_assets(telegram, results)

        self.assertEqual(len(telegram.rich_calls), 1)
        self.assertEqual(len(telegram.rich_calls[0]["images"]), 3)
        self.assertEqual(sent, [result.url for result in results])
        self.assertEqual(
            telegram.rich_calls[0]["heading"],
            "🚨 Nuovi asset Juventus",
        )
        self.assertIn("3", telegram.rich_calls[0]["body"])

    def test_gallery_splits_after_50_images(self) -> None:
        telegram = FakeTelegram()
        results = [
            self.make_result(f"2026080616{minute:04d}")
            for minute in range(51)
        ]

        sent = send_new_assets(telegram, results)

        self.assertEqual(len(telegram.rich_calls), 2)
        self.assertEqual(len(telegram.rich_calls[0]["images"]), 50)
        self.assertEqual(len(telegram.rich_calls[1]["images"]), 1)
        self.assertEqual(len(sent), 51)

    def test_gallery_does_not_send_missing_content(self) -> None:
        telegram = FakeTelegram()
        result = self.make_result()
        result.content = None

        sent = send_new_assets(telegram, [result])

        self.assertEqual(sent, [])
        self.assertEqual(telegram.rich_calls, [])

    def test_gallery_error_is_propagated(self) -> None:
        telegram = FakeTelegram(RuntimeError("Telegram temporary error"))
        with self.assertRaisesRegex(RuntimeError, "temporary error"):
            send_new_assets(telegram, [self.make_result()])




if __name__ == "__main__":
    unittest.main()
