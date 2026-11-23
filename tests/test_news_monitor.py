from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

import news_monitor


class NewsMonitorMissingArticleTests(unittest.TestCase):
    LIVE_URL = "https://www.footyheadlines.com/123/live-article.html"
    GONE_URL = "https://www.footyheadlines.com/456/gone-article.html"

    @staticmethod
    def _version() -> dict[str, str]:
        return {
            "fingerprint": "same-fingerprint",
            "handled_fingerprint": "same-fingerprint",
            "published": "2026-08-06T09:00:00+02:00",
            "modified": "2026-08-06T09:00:00+02:00",
            "title": "Live article",
            "description": "Description",
            "image": "https://cdn.footyheadlines.com/live.jpg",
        }

    @staticmethod
    def _http_error(status_code: int, url: str) -> requests.HTTPError:
        response = requests.Response()
        response.status_code = status_code
        response.url = url
        return requests.HTTPError(
            f"{status_code} Client Error",
            response=response,
        )

    def _live_candidate(self) -> dict[str, object]:
        return {
            "url": self.LIVE_URL,
            "title": "Live article",
            "snippet": "Description",
            "sources": ["latest"],
        }

    def test_tracked_404_is_removed_from_state(self) -> None:
        version = self._version()
        news_state = {
            "initialized": True,
            "articles": {
                self.LIVE_URL: dict(version),
                self.GONE_URL: {
                    "fingerprint": "old",
                    "handled_fingerprint": "old",
                    "title": "Gone article",
                    "description": "Old description",
                },
            },
        }
        state = Mock()
        state.section.return_value = news_state
        telegram = Mock()

        def fetch_version(candidate):
            if candidate["url"] == self.GONE_URL:
                raise self._http_error(404, self.GONE_URL)
            return version

        with (
            patch.object(
                news_monitor,
                "fetch_news_candidates",
                return_value=[self._live_candidate()],
            ),
            patch.object(
                news_monitor,
                "fetch_article_version",
                side_effect=fetch_version,
            ),
            patch.object(news_monitor, "log_status") as log_status,
        ):
            news_monitor.run(state, telegram)

        self.assertNotIn(self.GONE_URL, news_state["articles"])
        state.save.assert_called_once_with()
        telegram.send_news_rich_message.assert_not_called()
        self.assertTrue(
            any(
                "rimosso dallo stato 'Gone article'" in call.args[2]
                for call in log_status.call_args_list
            )
        )

    def test_tracked_500_is_kept_for_future_retries(self) -> None:
        version = self._version()
        gone_entry = {
            "fingerprint": "old",
            "handled_fingerprint": "old",
            "title": "Gone article",
            "description": "Old description",
        }
        news_state = {
            "initialized": True,
            "articles": {
                self.LIVE_URL: dict(version),
                self.GONE_URL: dict(gone_entry),
            },
        }
        state = Mock()
        state.section.return_value = news_state
        telegram = Mock()

        def fetch_version(candidate):
            if candidate["url"] == self.GONE_URL:
                raise self._http_error(500, self.GONE_URL)
            return version

        with (
            patch.object(
                news_monitor,
                "fetch_news_candidates",
                return_value=[self._live_candidate()],
            ),
            patch.object(
                news_monitor,
                "fetch_article_version",
                side_effect=fetch_version,
            ),
            patch.object(news_monitor, "log_status") as log_status,
        ):
            news_monitor.run(state, telegram)

        self.assertEqual(gone_entry, news_state["articles"][self.GONE_URL])
        state.save.assert_not_called()
        self.assertTrue(
            any(
                "errore verifica 'Gone article'" in call.args[2]
                for call in log_status.call_args_list
            )
        )

    def test_page_404_is_not_removed_from_state(self) -> None:
        version = self._version()
        page_404_url = self.GONE_URL
        page_404_entry = {
            "fingerprint": "old",
            "handled_fingerprint": "old",
            "title": "Page article",
            "description": "Old description",
        }
        news_state = {
            "initialized": True,
            "articles": {
                self.LIVE_URL: dict(version),
                page_404_url: dict(page_404_entry),
            },
        }
        state = Mock()
        state.section.return_value = news_state
        telegram = Mock()
        page_404_candidate = {
            "url": page_404_url,
            "title": "Page article",
            "snippet": "Old description",
            "sources": ["latest"],
        }

        def fetch_version(candidate):
            if candidate["url"] == page_404_url:
                raise self._http_error(404, page_404_url)
            return version

        with (
            patch.object(
                news_monitor,
                "fetch_news_candidates",
                return_value=[page_404_candidate, self._live_candidate()],
            ),
            patch.object(
                news_monitor,
                "fetch_article_version",
                side_effect=fetch_version,
            ),
            patch.object(news_monitor, "log_status"),
        ):
            news_monitor.run(state, telegram)

        self.assertEqual(
            page_404_entry,
            news_state["articles"][page_404_url],
        )
        state.save.assert_not_called()


class NewsMonitorRichDeliveryTests(unittest.TestCase):
    def test_fetch_article_version_extracts_schema_image(self) -> None:
        candidate = {
            "url": "https://www.footyheadlines.com/2026/09/test.html",
            "title": "Fallback title",
            "snippet": "Fallback description",
            "sources": ["latest"],
        }
        response = Mock()
        response.text = """
        <html><head>
        <script type="application/ld+json">
        {
          "@type": "NewsArticle",
          "headline": "New Juventus Kit",
          "description": "Article description",
          "datePublished": "2026-09-19T10:00:00+02:00",
          "dateModified": "2026-09-19T10:05:00+02:00",
          "image": {"@type": "ImageObject", "url": "/media/juventus-kit.jpg"}
        }
        </script>
        </head></html>
        """
        response.raise_for_status = Mock()

        with patch.object(news_monitor.requests, "get", return_value=response):
            version = news_monitor.fetch_article_version(candidate)

        self.assertEqual(
            "https://www.footyheadlines.com/media/juventus-kit.jpg",
            version["image"],
        )
        self.assertEqual("New Juventus Kit", version["title"])

    def test_send_news_article_uses_rich_message(self) -> None:
        telegram = Mock()
        candidate = {
            "url": "https://www.footyheadlines.com/2026/09/test.html",
        }
        version = {
            "title": "New Juventus Kit",
            "description": "Article description",
            "image": "https://cdn.footyheadlines.com/juventus-kit.jpg",
        }

        news_monitor.send_news_article(
            telegram,
            candidate,
            version,
            is_update=True,
        )

        telegram.send_news_rich_message.assert_called_once_with(
            title="New Juventus Kit",
            description="Article description",
            url=candidate["url"],
            image_url=version["image"],
            is_update=True,
        )
        telegram.send_message.assert_not_called()


class NewsMonitorRejectedArticleTests(unittest.TestCase):
    @staticmethod
    def _candidate(number: int) -> dict[str, object]:
        return {
            "url": f"https://www.footyheadlines.com/{number}/article-{number}.html",
            "title": f"Article {number}",
            "snippet": "Description",
            "sources": ["latest"],
        }

    @staticmethod
    def _version(candidate: dict[str, object]) -> dict[str, str]:
        title = str(candidate["title"])
        return {
            "fingerprint": title,
            "handled_fingerprint": title,
            "published": "2026-08-06T09:00:00+02:00",
            "modified": "2026-08-06T09:00:00+02:00",
            "title": title,
            "description": title,
            "image": "",
        }

    def test_rejected_article_does_not_block_next_ones(self) -> None:
        first, second = self._candidate(1), self._candidate(2)
        news_state: dict[str, object] = {"initialized": True, "articles": {}}
        state = Mock()
        state.section.return_value = news_state
        sent = []

        def send(_telegram, candidate, _version, _is_update) -> None:
            if candidate["url"] == second["url"]:   # il primo elaborato: la lista si legge al contrario
                raise RuntimeError("Telegram 400")
            sent.append(candidate["url"])

        with patch.object(news_monitor, "fetch_news_candidates", return_value=[first, second]),                 patch.object(news_monitor, "fetch_article_version", side_effect=self._version),                 patch.object(news_monitor, "send_news_article", side_effect=send):
            news_monitor.run(state, Mock())

        self.assertEqual(sent, [first["url"]])
        self.assertIn(first["url"], news_state["articles"])
        self.assertNotIn(second["url"], news_state["articles"])


if __name__ == "__main__":
    unittest.main()


class NewsMonitorSameArticleTwoUrlsTests(unittest.TestCase):
    URL_A = "https://www.footyheadlines.com/4502316860/adidas-juventus-2026-knit-leaked.html"
    URL_B = "https://www.footyheadlines.com/4502316860/exclusive-adidas-juventus-2026-knit-leaked.html"

    @staticmethod
    def _version(fp: str, title: str, description: str) -> dict[str, str]:
        return {
            "fingerprint": fp,
            "content_fingerprint": fp,
            "handled_fingerprint": fp,
            "published": "2026-11-20T05:49:00+01:00",
            "modified": "2026-11-20T05:49:47+01:00",
            "title": title,
            "description": description,
            "image": "https://cdn.footyheadlines.com/x.jpg",
        }

    def test_update_seen_on_second_url_is_not_sent_again(self) -> None:
        old = self._version("old", "Jersey Leaked", "Leaked images")
        new_version = self._version("new", "Jersey Released", "Officially unveiled")
        old["fingerprint"] = old["handled_fingerprint"] = news_monitor._content_fingerprint(old)
        new_version["fingerprint"] = new_version["handled_fingerprint"] = news_monitor._content_fingerprint(new_version)
        old["content_fingerprint"] = old["fingerprint"]
        new_version["content_fingerprint"] = new_version["fingerprint"]
        news_state = {"initialized": True, "articles": {self.URL_A: dict(old), self.URL_B: dict(old)}}
        state = Mock()
        state.section.return_value = news_state
        telegram = Mock()

        def candidates(url):
            return [{"url": url, "title": "Jersey", "snippet": "", "sources": ["latest"]}]

        for url in (self.URL_A, self.URL_B):          # due controlli, uno per URL
            with (
                patch.object(news_monitor, "fetch_news_candidates", return_value=candidates(url)),
                patch.object(news_monitor, "fetch_article_version", return_value=dict(new_version)),
                patch.object(news_monitor, "log_status"),
            ):
                news_monitor.run(state, telegram)

        self.assertEqual(telegram.send_news_rich_message.call_count, 1)
        self.assertEqual(news_state["articles"][self.URL_B]["fingerprint"], new_version["fingerprint"])
