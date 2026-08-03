from __future__ import annotations

import html
import json
import tempfile
import unittest
from pathlib import Path

import adidas_monitor
from state_store import StateStore


class AdidasParserTests(unittest.TestCase):
    def test_extracts_code_hash_and_juventus_candidate(self) -> None:
        image_url = (
            "https://assets.adidas.com/images/w_1880,f_auto,q_auto/"
            "530d3e32e39e4c33917b581e23840651_9366/KC2282_HM53.jpg"
        )
        payload = {
            "t": "Juventus 26/27 Home Authentic Jersey - White | adidas",
            "murl": image_url,
            "purl": (
                "https://www.adidas.com.hk/en/juventus-26-27-home-authentic-jersey/"
                "KC2282.html"
            ),
        }
        document = '<a class="iusc" m="{}"></a>'.format(
            html.escape(json.dumps(payload), quote=True)
        )

        products = adidas_monitor.parse_bing_image_results(document, "test")

        self.assertEqual(["KC2282"], list(products))
        candidate = products["KC2282"]
        self.assertGreaterEqual(candidate["confidence"], 90)
        self.assertIn(
            "530d3e32e39e4c33917b581e23840651_9366/KC2282_HM53.jpg",
            candidate["assets"],
        )

    def test_rejects_unrelated_adidas_result(self) -> None:
        payload = {
            "t": "Campus 00s Shoes - Green | adidas",
            "murl": (
                "https://assets.adidas.com/images/w_1880,f_auto/"
                "b0b6d4a107ad4e84b3baaf8700866f07_9366/H03472_01_standard.jpg"
            ),
            "purl": "https://www.adidas.hr/tenisice-campus-00s/H03472.html",
        }
        document = '<a class="iusc" m="{}"></a>'.format(
            html.escape(json.dumps(payload), quote=True)
        )
        self.assertEqual(
            {}, adidas_monitor.parse_bing_image_results(document, "test")
        )

    def test_parses_yahoo_result_format(self) -> None:
        payload = {
            "alt": "Juventus 26/27 Home Jersey - White | adidas Hong Kong",
            "rurl": (
                "https://www.adidas.com.hk/en/juventus-26-27-home-jersey/"
                "KC2285.html"
            ),
            "iurl": (
                "https://assets.adidas.com/images/w_1880,f_auto,q_auto/"
                "7893afdbae8445ca8776a7bb7cbc98c8_9366/KC2285_41_detail.jpg"
            ),
        }
        document = '<li data="{}"></li>'.format(
            html.escape(json.dumps(payload), quote=True)
        )

        products = adidas_monitor.parse_yahoo_image_results(document, "test")

        self.assertEqual(["KC2285"], list(products))
        self.assertEqual(1, len(products["KC2285"]["assets"]))


class UnifiedStateMigrationTests(unittest.TestCase):
    def test_migrates_and_deletes_all_legacy_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / ".found-font-HOME-26-27").write_text(
                "notified\n", encoding="utf-8"
            )
            (directory / ".found-product-01").write_text(
                "notified\n", encoding="utf-8"
            )
            (directory / ".seen_news.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "initialized": True,
                        "articles": {"https://example.test/a.html": {"x": 1}},
                    }
                ),
                encoding="utf-8",
            )

            store = StateStore(directory / ".leakkit_state.json")

            self.assertTrue(store.data["fonts"]["HOME-26-27"]["notified"])
            self.assertTrue(store.data["store_products"]["01"]["notified"])
            self.assertIn(
                "https://example.test/a.html",
                store.data["news"]["articles"],
            )
            self.assertFalse((directory / ".found-font-HOME-26-27").exists())
            self.assertFalse((directory / ".found-product-01").exists())
            self.assertFalse((directory / ".seen_news.json").exists())
            self.assertTrue((directory / ".leakkit_state.json").exists())


if __name__ == "__main__":
    unittest.main()
