"""Anthropic official-channel scraper.

Fetches article cards from:
  - https://www.anthropic.com/news    (Anthropic corporate / research news)
  - https://claude.com/blog           (Claude product blog)

Neither page exposes a public RSS feed, so we parse the rendered HTML.
Items from this scraper bypass the keyword-relevance filter — every new
article goes straight to the top of the digest. The sent_links DB still
prevents re-posting the same URL.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..models import ContentItem

logger = logging.getLogger(__name__)


# Bypass marker — read by scoring + main formatter.
SOURCE_TYPE = "official_anthropic"

# Cap how many items per source on first ever run, so we don't flood the
# digest with the entire archive. Subsequent runs see them in sent_links.db.
MAX_ITEMS_PER_SOURCE = 10


@dataclass
class _Site:
    label: str           # human source_name
    base: str            # e.g. "https://www.anthropic.com"
    listing_url: str     # page to scrape
    article_path: str    # path prefix that identifies article links, e.g. "/news/"


SITES: List[_Site] = [
    _Site(
        label="Anthropic News",
        base="https://www.anthropic.com",
        listing_url="https://www.anthropic.com/news",
        article_path="/news/",
    ),
    _Site(
        label="Claude Blog",
        base="https://claude.com",
        listing_url="https://claude.com/blog",
        article_path="/blog/",
    ),
]


class AnthropicOfficialScraper(BaseScraper):
    """Scrape Anthropic's news + Claude blog index pages."""

    async def fetch(self, since: datetime) -> List[ContentItem]:
        results = await asyncio.gather(
            *(self._fetch_site(s) for s in SITES),
            return_exceptions=True,
        )
        items: List[ContentItem] = []
        for site, result in zip(SITES, results):
            if isinstance(result, Exception):
                logger.warning("Anthropic-official fetch failed for %s: %s", site.label, result)
                continue
            items.extend(result)
        logger.info("AnthropicOfficial scraper produced %d items", len(items))
        return items

    async def _fetch_site(self, site: _Site) -> List[ContentItem]:
        try:
            response = await self.client.get(
                site.listing_url,
                follow_redirects=True,
                timeout=30.0,
                headers={
                    # Some CDNs deny default user agents.
                    "User-Agent": "Mozilla/5.0 (compatible; ai-news-aggregator-he/1.0)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
            html = response.text
        except httpx.HTTPError as e:
            logger.warning("HTTP error fetching %s: %s", site.listing_url, e)
            return []

        soup = BeautifulSoup(html, "html.parser")

        # Find every anchor whose href matches the article path. Dedup by URL,
        # preserve order so the page's own ordering (newest first) wins.
        seen_urls: set = set()
        articles: List[Tuple[str, str, Optional[datetime]]] = []  # (url, title, published_at)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            url = self._normalize_url(site.base, href, site.article_path)
            if url is None or url in seen_urls:
                continue

            title = self._extract_title(a)
            if not title:
                continue

            published_at = self._extract_date_near(a)

            seen_urls.add(url)
            articles.append((url, title, published_at))
            if len(articles) >= MAX_ITEMS_PER_SOURCE:
                break

        items: List[ContentItem] = []
        for url, title, published_at in articles:
            slug = urlparse(url).path.rstrip("/").split("/")[-1] or url
            items.append(ContentItem(
                id=self._make_id(SOURCE_TYPE, site.label.lower().replace(" ", "_"), slug),
                source_type=SOURCE_TYPE,
                source_name=site.label,
                title=title,
                url=url,
                summary="",
                author=None,
                published_at=published_at,
                metadata={"official": True, "site": site.label},
            ))
        logger.info("AnthropicOfficial[%s]: %d items", site.label, len(items))
        return items

    # --- helpers ------------------------------------------------------

    @staticmethod
    def _normalize_url(base: str, href: str, article_path: str) -> Optional[str]:
        """Return absolute URL if href looks like an article on this site, else None."""
        if not href:
            return None
        # Skip mail, fragments, blank.
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return None
        absolute = urljoin(base + "/", href)
        parsed = urlparse(absolute)
        if not parsed.scheme.startswith("http"):
            return None
        # Must be on the same host as the base.
        base_host = urlparse(base).hostname
        if parsed.hostname not in (base_host, f"www.{base_host}", (base_host or "").lstrip("www.")):
            return None
        # Must be under the article path and have a non-empty slug after it.
        path = parsed.path
        if not path.startswith(article_path):
            return None
        slug_part = path[len(article_path):].strip("/")
        if not slug_part or "/" in slug_part:
            # Reject the index page itself ('/news/') and nested category pages.
            return None
        # Drop query/fragment for stable dedup.
        return f"{parsed.scheme}://{parsed.hostname}{path.rstrip('/')}"

    @staticmethod
    def _extract_title(a) -> str:
        """Pull a clean title from the anchor — prefer aria-label / heading text."""
        # 1. Aria label is often set explicitly to the article title.
        aria = (a.get("aria-label") or "").strip()
        if aria and len(aria) > 4:
            return aria
        # 2. Headings inside the anchor.
        heading = a.find(["h1", "h2", "h3", "h4", "h5"])
        if heading:
            text = heading.get_text(" ", strip=True)
            if text:
                return text
        # 3. Fallback: anchor's own text, collapsed.
        text = a.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        # Drop trivial chrome words like "Read more", "Announcement", standalone dates.
        if text.lower() in {"read more", "learn more", "read the post", "announcement"}:
            return ""
        return text[:200]

    @staticmethod
    def _extract_date_near(a) -> Optional[datetime]:
        """Best-effort: look for a <time datetime=...> inside the card or its parent."""
        candidates = []
        time_tag = a.find("time")
        if time_tag:
            candidates.append(time_tag)
        if a.parent is not None:
            candidates.extend(a.parent.find_all("time", limit=2))
        for t in candidates:
            iso = t.get("datetime")
            if not iso:
                continue
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None
