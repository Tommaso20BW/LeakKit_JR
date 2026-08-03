from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import check


class OrchestrationTests(unittest.TestCase):
    def test_continues_after_a_monitor_failure(self) -> None:
        calls: list[str] = []

        def fail(_state, _telegram) -> None:
            calls.append("fail")
            raise RuntimeError("boom")

        def succeed(_state, _telegram) -> None:
            calls.append("succeed")

        with (
            patch.dict(
                check.MONITORS,
                {"first": fail, "second": succeed},
                clear=True,
            ),
            patch.object(check.traceback, "print_exc"),
        ):
            failures = check.run_monitors(Mock(), Mock(), ["first", "second"])

        self.assertEqual(["fail", "succeed"], calls)
        self.assertEqual(["first"], failures)


if __name__ == "__main__":
    unittest.main()
