from datetime import datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from timestamp_scanner.scanner import (  # noqa: E402
    ROME,
    Target,
    compact,
    parse_local_datetime,
    parse_workflow_started_at,
)


class ScannerTests(unittest.TestCase):
    def test_compact_timestamp_includes_seconds(self) -> None:
        value = parse_local_datetime("2026-05-12 10:37:59")
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
        value = parse_local_datetime("2026-08-08 23:59:59")
        self.assertEqual(compact(value), "20260808235959")


if __name__ == "__main__":
    unittest.main()
