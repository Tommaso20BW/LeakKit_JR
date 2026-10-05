from __future__ import annotations

import unittest

import news_monitor


class NewsDedupTests(unittest.TestCase):
    def _version(self, *, modified="2026-10-05T12:06:00+02:00", title="Article", description="Description", image="https://example.com/image.jpg"):
        return {
            "fingerprint": news_monitor._content_fingerprint(
                {"title": title, "description": description, "image": image}
            ),
            "content_fingerprint": news_monitor._content_fingerprint(
                {"title": title, "description": description, "image": image}
            ),
            "published": "2026-10-05T12:00:00+02:00",
            "modified": modified,
            "title": title,
            "description": description,
            "image": image,
        }

    def test_modified_date_alone_is_not_an_update(self):
        previous = self._version(modified="2026-10-05T12:06:00+02:00")
        current = self._version(modified="2026-10-05T12:20:00+02:00")
        self.assertFalse(news_monitor._has_meaningful_update(previous, current))

    def test_real_content_change_is_an_update(self):
        previous = self._version()
        current = self._version(description="Description aggiornata")
        self.assertTrue(news_monitor._has_meaningful_update(previous, current))

    def test_same_content_on_different_url_is_duplicate(self):
        previous = self._version()
        articles = {"https://www.footyheadlines.com/123/old.html": previous}
        current = self._version(modified="2026-10-05T13:00:00+02:00")
        self.assertTrue(
            news_monitor._already_tracked_content(
                articles,
                "https://www.footyheadlines.com/456/new.html",
                current,
            )
        )

    def test_different_content_on_different_url_is_not_duplicate(self):
        previous = self._version()
        articles = {"https://www.footyheadlines.com/123/old.html": previous}
        current = self._version(title="Different article")
        self.assertFalse(
            news_monitor._already_tracked_content(
                articles,
                "https://www.footyheadlines.com/456/new.html",
                current,
            )
        )


if __name__ == "__main__":
    unittest.main()
