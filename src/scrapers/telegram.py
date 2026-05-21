"""Telegram public-channel scraper via the t.me/s/<channel> web preview.

No API key, no auth. Channel list is intentionally empty by default —
the owner fills it in. With an empty list the scraper just returns [].
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..models import ContentItem

logger = logging.getLogger(__name__)


TELEGRAM_WEB_BASE = "https://t.me/s"
TELEGRAM_USER_AGENT = "Mozilla/5.0 (compatible; news-bot/1.0)"


class TelegramScraper(BaseScraper):
    """Scrape recent messages from public Telegram channels.

    `channels` is a list of handles (without leading @) supplied from config.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        channels: Optional[List[str]] = None,
        limit: int = 30,
    ):
        super().__init__(http_client)
        self.channels = list(channels or [])
        self.limit = limit
        self._semaphore = asyncio.Semaphore(2)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.channels:
            return []
        results = await asyncio.gather(
            *[self._fetch_channel(ch, since) for ch in self.channels],
            return_exceptions=True,
        )
        items: List[ContentItem] = []
        for ch, r in zip(self.channels, results):
            if isinstance(r, Exception):
                logger.warning("Telegram fetch @%s failed: %s", ch, r)
                continue
            items.extend(r)
        logger.info("Telegram scraper produced %d items", len(items))
        return items

    async def _fetch_channel(self, channel: str, since: datetime) -> List[ContentItem]:
        url = f"{TELEGRAM_WEB_BASE}/{channel}"
        headers = {"User-Agent": TELEGRAM_USER_AGENT}
        async with self._semaphore:
            try:
                response = await self.client.get(
                    url, headers=headers, follow_redirects=True, timeout=60.0,
                )
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5) or 5)
                    logger.warning("Telegram 429 on @%s; waiting %ds", channel, retry_after)
                    await asyncio.sleep(retry_after)
                    response = await self.client.get(
                        url, headers=headers, follow_redirects=True, timeout=60.0,
                    )
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("Telegram GET @%s failed: %s", channel, e)
                return []

        return self._parse(response.text, channel, since)

    def _parse(self, html: str, channel: str, since: datetime) -> List[ContentItem]:
        soup = BeautifulSoup(html, "html.parser")
        wraps = soup.select("div.tgme_widget_message_wrap")
        # Newest messages are at the bottom of the page; take the last N.
        wraps = wraps[-self.limit:] if len(wraps) > self.limit else wraps

        items: List[ContentItem] = []
        for wrap in wraps:
            item = self._parse_message(wrap, channel, since)
            if item is not None:
                items.append(item)
        return items

    def _parse_message(self, wrap, channel: str, since: datetime) -> Optional[ContentItem]:
        msg = wrap.select_one("div.tgme_widget_message")
        if msg is None:
            return None

        # Permalink + msg id come from the date anchor href.
        date_a = wrap.select_one(".tgme_widget_message_date")
        if date_a is None or not date_a.get("href"):
            return None
        permalink = date_a["href"]
        msg_id = permalink.rstrip("/").split("/")[-1]
        if not msg_id:
            return None

        time_el = wrap.select_one("time[datetime]")
        if time_el is None:
            return None
        try:
            published_at = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            return None
        if published_at < since:
            return None

        text_el = wrap.select_one(".tgme_widget_message_text")
        if text_el is None:
            return None
        for br in text_el.find_all("br"):
            br.replace_with("\n")
        text = text_el.get_text(separator="").strip()
        if not text:
            return None

        title = self._make_title(text)
        summary = text if len(text) <= 300 else text[:297] + "..."

        # Prefer the first external link as the canonical URL.
        canonical_url = permalink
        for a in text_el.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "t.me" not in href:
                canonical_url = href
                break

        # View counts are an engagement signal.
        views_raw = wrap.select_one(".tgme_widget_message_views")
        views = self._parse_views(views_raw.get_text(strip=True)) if views_raw else None

        return ContentItem(
            id=self._make_id("telegram", channel, msg_id),
            source_type="telegram",
            source_name=f"@{channel}",
            title=f"@{channel}: {title}",
            url=canonical_url,
            summary=summary,
            author=channel,
            published_at=published_at,
            metadata={
                "telegram_views": views,
                "channel": channel,
                "msg_url": permalink,
            },
        )

    @staticmethod
    def _make_title(text: str) -> str:
        first_para = text.split("\n\n")[0].replace("\n", " ").strip()
        return first_para if len(first_para) <= 80 else first_para[:80].rstrip() + "..."

    @staticmethod
    def _parse_views(raw: str) -> Optional[int]:
        # Telegram shows counts like "12.3K", "1.2M", "987".
        if not raw:
            return None
        s = raw.strip().upper()
        m = re.match(r"^([\d.]+)\s*([KMB]?)$", s)
        if not m:
            try:
                return int(s.replace(",", ""))
            except ValueError:
                return None
        num = float(m.group(1))
        mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(m.group(2), 1)
        return int(num * mult)
