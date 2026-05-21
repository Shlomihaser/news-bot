"""Hacker News scraper: top stories (Firebase API) + Algolia keyword search."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..models import ContentItem

logger = logging.getLogger(__name__)

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


class HackerNewsScraper(BaseScraper):
    """Top stories + Algolia keyword search, deduplicated by story id.

    All thresholds and keyword lists come from config.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        fetch_top_n: int = 60,
        top_min_score: int = 100,
        algolia_keywords: Optional[List[str]] = None,
        algolia_min_points: int = 50,
    ):
        super().__init__(http_client)
        self.fetch_top_n = fetch_top_n
        self.top_min_score = top_min_score
        self.algolia_keywords = list(algolia_keywords or [])
        self.algolia_min_points = algolia_min_points
        self._semaphore = asyncio.Semaphore(4)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        top_task = self._fetch_top_stories(since)
        algolia_task = self._fetch_algolia(since)
        top, algolia = await asyncio.gather(top_task, algolia_task, return_exceptions=True)

        if isinstance(top, Exception):
            logger.warning("HN top stories failed: %s", top)
            top = []
        if isinstance(algolia, Exception):
            logger.warning("HN Algolia failed: %s", algolia)
            algolia = []

        # Dedup by HN story id; prefer the entry with the higher score
        # (top-stories typically has fresher score than Algolia indexed copy).
        by_id: Dict[str, ContentItem] = {}
        for item in list(top) + list(algolia):
            existing = by_id.get(item.id)
            if existing is None or item.metadata.get("hn_score", 0) > existing.metadata.get("hn_score", 0):
                by_id[item.id] = item
        items = list(by_id.values())
        logger.info(
            "HN: %d top + %d algolia => %d after dedup",
            len(top),
            len(algolia),
            len(items),
        )
        return items

    # --- Top stories (Firebase) --------------------------------------

    async def _fetch_top_stories(self, since: datetime) -> List[ContentItem]:
        try:
            response = await self.client.get(f"{HN_API_BASE}/topstories.json", timeout=30.0)
            response.raise_for_status()
            ids = response.json()[: self.fetch_top_n]
        except httpx.HTTPError as e:
            logger.warning("HN topstories index fetch failed: %s", e)
            return []

        story_results = await asyncio.gather(
            *[self._fetch_story(story_id) for story_id in ids],
            return_exceptions=True,
        )

        items: List[ContentItem] = []
        for story in story_results:
            if isinstance(story, Exception) or not story:
                continue
            # Filter at fetch time so we don't keep low-score stories in memory.
            if (story.get("score") or 0) < self.top_min_score:
                continue
            item = self._parse_story(story, since)
            if item is not None:
                items.append(item)
        return items

    async def _fetch_story(self, story_id: int) -> Optional[dict]:
        async with self._semaphore:
            try:
                response = await self.client.get(f"{HN_API_BASE}/item/{story_id}.json", timeout=30.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError:
                return None

    def _parse_story(self, story: dict, since: datetime) -> Optional[ContentItem]:
        story_id = story.get("id")
        if story_id is None:
            return None

        published_at = None
        if "time" in story:
            published_at = datetime.fromtimestamp(story["time"], tz=timezone.utc)
        if published_at and published_at < since:
            return None

        title = story.get("title", "No Title")
        link = story.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        raw_text = story.get("text", "")
        summary = BeautifulSoup(raw_text, "html.parser").get_text(separator=" ", strip=True) if raw_text else ""
        if not summary:
            summary = "Click to read more or join the discussion."
        if len(summary) > 300:
            summary = summary[:297] + "..."

        return ContentItem(
            id=self._make_id("hackernews", "story", str(story_id)),
            source_type="hackernews",
            source_name="Hacker News",
            title=f"Hacker News: {title}",
            url=link,
            summary=summary,
            author=story.get("by"),
            published_at=published_at,
            metadata={
                "hn_score": story.get("score", 0) or 0,
                "hn_descendants": story.get("descendants", 0) or 0,
                "discussion_url": f"https://news.ycombinator.com/item?id={story_id}",
            },
        )

    # --- Algolia keyword search --------------------------------------

    async def _fetch_algolia(self, since: datetime) -> List[ContentItem]:
        if not self.algolia_keywords:
            return []
        results = await asyncio.gather(
            *[self._search_one(kw, since) for kw in self.algolia_keywords],
            return_exceptions=True,
        )
        items: List[ContentItem] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Algolia query raised: %s", r)
                continue
            items.extend(r)
        return items

    async def _search_one(self, keyword: str, since: datetime) -> List[ContentItem]:
        url = f"{HN_ALGOLIA_BASE}/search_by_date"
        params = {
            "query": keyword,
            "tags": "story",
            "numericFilters": f"points>{self.algolia_min_points}",
        }
        async with self._semaphore:
            try:
                response = await self.client.get(url, params=params, timeout=30.0)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.warning("Algolia fetch failed for %r: %s", keyword, e)
                return []

        items: List[ContentItem] = []
        for hit in data.get("hits", []):
            story_id = hit.get("objectID")
            if not story_id:
                continue
            published_at = None
            ts = hit.get("created_at_i")
            if ts:
                published_at = datetime.fromtimestamp(ts, tz=timezone.utc)
            if published_at and published_at < since:
                continue
            title = hit.get("title") or hit.get("story_title") or "No Title"
            link = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            summary = (hit.get("story_text") or "").strip()
            summary = BeautifulSoup(summary, "html.parser").get_text(separator=" ", strip=True) if summary else ""
            if not summary:
                summary = "Click to read more or join the discussion."
            if len(summary) > 300:
                summary = summary[:297] + "..."

            items.append(ContentItem(
                id=self._make_id("hackernews", "story", str(story_id)),
                source_type="hackernews",
                source_name="Hacker News",
                title=f"Hacker News: {title}",
                url=link,
                summary=summary,
                author=hit.get("author"),
                published_at=published_at,
                metadata={
                    "hn_score": hit.get("points", 0) or 0,
                    "hn_descendants": hit.get("num_comments", 0) or 0,
                    "discussion_url": f"https://news.ycombinator.com/item?id={story_id}",
                    "algolia_keyword": keyword,
                },
            ))
        return items
