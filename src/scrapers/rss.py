"""RSS / Atom feed scraper. Feeds list is provided by the caller (config-driven)."""

import asyncio
import calendar
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

import feedparser
import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..models import ContentItem

logger = logging.getLogger(__name__)


class RSSScraper(BaseScraper):
    """Async scraper for a list of RSS/Atom feeds.

    `feeds` is a list of (url, source_name) tuples supplied from config.
    """

    def __init__(self, http_client: httpx.AsyncClient, feeds: Optional[List[Tuple[str, str]]] = None):
        super().__init__(http_client)
        self.feeds = list(feeds or [])
        self._semaphore = asyncio.Semaphore(4)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        tasks = [self._fetch_feed(url, name, since) for url, name in self.feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: List[ContentItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("RSS fetch raised: %s", result)
                continue
            items.extend(result)
        return items

    async def _fetch_feed(self, feed_url: str, source_name: str, since: datetime) -> List[ContentItem]:
        items: List[ContentItem] = []
        try:
            async with self._semaphore:
                response = await self.client.get(feed_url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()
            parsed = feedparser.parse(response.text)
        except Exception as e:
            logger.warning("RSS fetch failed for %s (%s): %s", source_name, feed_url, e)
            return items

        if parsed.bozo and not parsed.entries:
            logger.warning("RSS feed appears invalid: %s (%s)", source_name, feed_url)
            return items

        for entry in parsed.entries:
            published_at = self._parse_date(entry)
            # Be lenient: keep items with no parseable date so we don't drop
            # feeds that omit dates entirely.
            if published_at and published_at < since:
                continue

            link = getattr(entry, "link", None)
            if not link:
                continue

            title = getattr(entry, "title", "No Title")
            raw_summary = getattr(entry, "summary", getattr(entry, "description", title))
            summary = BeautifulSoup(raw_summary, "html.parser").get_text(separator=" ", strip=True)
            if len(summary) > 300:
                summary = summary[:297] + "..."

            entry_id = entry.get("id", link)
            native_id = str(abs(hash(entry_id)))

            items.append(ContentItem(
                id=self._make_id("rss", source_name.replace(" ", "_").lower(), native_id),
                source_type="rss",
                source_name=source_name,
                title=f"{source_name}: {title}",
                url=link,
                summary=summary,
                author=getattr(entry, "author", None) or source_name,
                published_at=published_at,
                metadata={"feed_url": feed_url, "feed_name": source_name},
            ))

        logger.info("RSS %s: %d items", source_name, len(items))
        return items

    @staticmethod
    def _parse_date(entry) -> Optional[datetime]:
        for field_name in ("published", "updated", "created"):
            parsed_field = entry.get(f"{field_name}_parsed")
            if parsed_field:
                try:
                    return datetime.fromtimestamp(calendar.timegm(parsed_field), tz=timezone.utc)
                except Exception:
                    pass
            raw = entry.get(field_name)
            if raw:
                try:
                    dt = parsedate_to_datetime(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except Exception:
                    continue
        return None
