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
    compact,
    parse_local_datetime,
    parse_workflow_started_at,
    send_new_asset,
    UrlResult,
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

    def test_new_start_automatically_creates_new_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "scan_start": "20260727000000",
                        "final_end": "20260808235959",
                        "next_timestamp": "20260727000000",
                        "completed": False,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(scanner_module, "STATE_PATH", state_path):
                state = scanner_module.load_state(
                    parse_local_datetime("05/08/2026 00:00:00"),
                    parse_local_datetime("07/08/2026 23:59:59"),
                    reset=False,
                )

            self.assertEqual(state["scan_start"], "20260805000000")
            self.assertEqual(state["final_end"], "20260807235959")
            self.assertEqual(state["next_timestamp"], "20260805000000")

    def test_only_new_end_preserves_progress(self) -> None:
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
                    parse_local_datetime("05/08/2026 00:00:00"),
                    parse_local_datetime("08/08/2026 23:59:59"),
                    reset=False,
                )

            self.assertEqual(state["final_end"], "20260808235959")
            self.assertEqual(state["next_timestamp"], "20260806123456")
            self.assertFalse(state["completed"])


class FakeTelegram:
    def __init__(self, photo_error: Exception | None = None) -> None:
        self.photo_error = photo_error
        self.photo_calls = 0
        self.document_calls = 0

    def send_photo_bytes(self, *args, **kwargs):
        self.photo_calls += 1
        if self.photo_error is not None:
            raise self.photo_error
        return {"message_id": 1}

    def send_document_bytes(self, *args, **kwargs):
        self.document_calls += 1
        return {"message_id": 2}


class TelegramFallbackTests(unittest.TestCase):
    def make_result(self) -> UrlResult:
        return UrlResult(
            target="categories",
            timestamp="20260806162654",
            url="https://example.test/20260806162654.webp",
            status="found",
            content_type="image/webp",
            content=b"RIFFxxxxWEBPpayload",
        )

    def test_normal_image_is_sent_as_photo(self) -> None:
        telegram = FakeTelegram()
        mode = send_new_asset(telegram, self.make_result())
        self.assertEqual(mode, "photo")
        self.assertEqual(telegram.photo_calls, 1)
        self.assertEqual(telegram.document_calls, 0)

    def test_invalid_dimensions_fall_back_to_document(self) -> None:
        telegram = FakeTelegram(
            RuntimeError(
                "Telegram sendPhoto: Bad Request: "
                "PHOTO_INVALID_DIMENSIONS"
            )
        )
        mode = send_new_asset(telegram, self.make_result())
        self.assertEqual(mode, "document")
        self.assertEqual(telegram.photo_calls, 1)
        self.assertEqual(telegram.document_calls, 1)

    def test_unrelated_telegram_error_is_not_hidden(self) -> None:
        telegram = FakeTelegram(
            RuntimeError("Telegram sendPhoto: Unauthorized")
        )
        with self.assertRaisesRegex(RuntimeError, "Unauthorized"):
            send_new_asset(telegram, self.make_result())
        self.assertEqual(telegram.document_calls, 0)


if __name__ == "__main__":
    unittest.main()
