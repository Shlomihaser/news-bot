"""Reddit scraper using public JSON endpoints (no auth)."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseScraper
from ..models import ContentItem

logger = logging.getLogger(__name__)


REDDIT_BASE = "https://www.reddit.com"

# Reddit blocks requests with default Python UAs; pretend to be a browser.
REDDIT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
REDDIT_HEADERS = {
    "User-Agent": REDDIT_USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{REDDIT_BASE}/",
}

class RedditScraper(BaseScraper):
    """Async scraper for a list of public subreddits.

    `subreddits` is a list of dicts with keys: name, sort, limit, min_score,
    optional time_filter. Provided by the caller from config.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        subreddits: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(http_client)
        self.subreddits = list(subreddits or [])
        # Reddit 429s aggressively — keep concurrency low for this host.
        self._semaphore = asyncio.Semaphore(2)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        tasks = [self._fetch_subreddit(cfg, since) for cfg in self.subreddits]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: List[ContentItem] = []
        for cfg, result in zip(self.subreddits, results):
            if isinstance(result, Exception):
                logger.warning("Reddit fetch r/%s failed: %s", cfg.get("name"), result)
                continue
            items.extend(result)
        logger.info("Reddit scraper produced %d items", len(items))
        return items

    async def _fetch_subreddit(self, cfg: Dict[str, Any], since: datetime) -> List[ContentItem]:
        name = cfg["name"]
        sort = cfg.get("sort", "hot")
        limit = min(int(cfg.get("limit", 20)), 100)
        min_score = int(cfg.get("min_score", 0))
        time_filter = cfg.get("time_filter")

        url = f"{REDDIT_BASE}/r/{name}/{sort}.json"
        params: Dict[str, Any] = {"limit": limit, "raw_json": 1}
        if sort in ("top", "controversial") and time_filter:
            params["t"] = time_filter

        data = await self._reddit_get(url, params)
        if not data:
            return []

        children = (data.get("data") or {}).get("children", [])
        items: List[ContentItem] = []
        for child in children:
            if child.get("kind") != "t3":
                continue
            post = child.get("data") or {}
            item = self._parse_post(post, name, since, min_score)
            if item is not None:
                items.append(item)
        return items

    def _parse_post(
        self,
        post: Dict[str, Any],
        subreddit: str,
        since: datetime,
        min_score: int,
    ) -> Optional[ContentItem]:
        if post.get("stickied") or post.get("over_18"):
            return None

        score = int(post.get("score", 0) or 0)
        if score < min_score:
            return None

        created_utc = post.get("created_utc")
        published_at = None
        if created_utc:
            published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)
        if published_at and published_at < since:
            return None

        post_id = post.get("id")
        if not post_id:
            return None

        title = post.get("title", "No Title")
        is_self = bool(post.get("is_self"))
        permalink = post.get("permalink") or ""
        discussion_url = f"{REDDIT_BASE}{permalink}" if permalink else f"{REDDIT_BASE}/r/{subreddit}/comments/{post_id}"
        url = discussion_url if is_self else (post.get("url") or discussion_url)

        selftext = (post.get("selftext") or "").strip()
        summary = selftext[:300] + ("..." if len(selftext) > 300 else "")
        if not summary:
            summary = "Click to read more or join the discussion."

        return ContentItem(
            id=self._make_id("reddit", subreddit, post_id),
            source_type="reddit",
            source_name=f"r/{subreddit}",
            title=f"r/{subreddit}: {title}",
            url=url,
            summary=summary,
            author=post.get("author"),
            published_at=published_at,
            metadata={
                "reddit_score": score,
                "reddit_num_comments": int(post.get("num_comments", 0) or 0),
                "reddit_upvote_ratio": post.get("upvote_ratio"),
                "subreddit": subreddit,
                "flair": post.get("link_flair_text"),
                "discussion_url": discussion_url,
                "is_self": is_self,
            },
        )

    async def _reddit_get(self, url: str, params: Dict[str, Any]) -> Optional[Any]:
        async with self._semaphore:
            try:
                response = await self.client.get(
                    url, params=params, headers=REDDIT_HEADERS, follow_redirects=True, timeout=30.0,
                )
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5) or 5)
                    logger.warning("Reddit 429; waiting %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    response = await self.client.get(
                        url, params=params, headers=REDDIT_HEADERS, follow_redirects=True, timeout=30.0,
                    )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.warning("Reddit GET %s failed: %s", url, e)
                return None
