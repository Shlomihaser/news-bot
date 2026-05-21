"""Concurrently run all enabled scrapers and return a combined ContentItem list.

Reads the YAML config and instantiates only the scrapers whose `enabled` flag
is true. A failure in one scraper is logged but does not crash the run.
"""

import asyncio
import logging
from datetime import datetime
from typing import List

import httpx

from .config import Config
from .models import ContentItem
from .scrapers.anthropic_official import AnthropicOfficialScraper
from .scrapers.base import BaseScraper
from .scrapers.github import GitHubScraper
from .scrapers.hackernews import HackerNewsScraper
from .scrapers.reddit import RedditScraper
from .scrapers.rss import RSSScraper
from .scrapers.telegram import TelegramScraper

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "news-bot/1.0",
    "Accept-Language": "en-US,en;q=0.9",
}


def _build_scrapers(cfg: Config, client: httpx.AsyncClient) -> dict:
    """Instantiate only the scrapers whose `enabled` flag is set in config."""
    s = cfg.sources
    scrapers: dict = {}

    if s.anthropic_official.enabled:
        scrapers["AnthropicOfficial"] = AnthropicOfficialScraper(client)

    if s.rss.enabled:
        scrapers["RSS"] = RSSScraper(
            client,
            feeds=[(f.url, f.name) for f in s.rss.feeds],
        )

    if s.hackernews.enabled:
        scrapers["HackerNews"] = HackerNewsScraper(
            client,
            fetch_top_n=s.hackernews.top_stories.fetch_n,
            top_min_score=s.hackernews.top_stories.min_score,
            algolia_keywords=s.hackernews.algolia.keywords,
            algolia_min_points=s.hackernews.algolia.min_points,
        )

    if s.github.enabled:
        scrapers["GitHub"] = GitHubScraper(
            client,
            release_watchlist=[(r.owner, r.repo, r.topics) for r in s.github.releases],
            search_topics=s.github.search.topics,
            search_min_stars=s.github.search.min_stars,
            event_users=s.github.user_events,
            event_repo_topics=s.github.event_repo_topics,
        )

    if s.reddit.enabled:
        scrapers["Reddit"] = RedditScraper(
            client,
            subreddits=[
                {
                    "name": sub.name,
                    "sort": sub.sort,
                    "limit": sub.limit,
                    "min_score": sub.min_score,
                    **({"time_filter": sub.time_filter} if sub.time_filter else {}),
                }
                for sub in s.reddit.subreddits
            ],
        )

    if s.telegram.enabled:
        scrapers["Telegram"] = TelegramScraper(
            client,
            channels=s.telegram.channels,
            limit=s.telegram.per_channel_limit,
        )

    return scrapers


async def fetch_all_sources(cfg: Config, since: datetime) -> List[ContentItem]:
    """Fetch from every enabled scraper concurrently."""
    async with httpx.AsyncClient(timeout=30.0, headers=DEFAULT_HEADERS) as client:
        scrapers = _build_scrapers(cfg, client)
        if not scrapers:
            logger.warning("No scrapers enabled in config — returning empty result.")
            return []

        results = await asyncio.gather(
            *(scraper.fetch(since) for scraper in scrapers.values()),
            return_exceptions=True,
        )

        all_items: List[ContentItem] = []
        for name, result in zip(scrapers.keys(), results):
            if isinstance(result, Exception):
                logger.error("%s scraper failed: %s", name, result)
                continue
            logger.info("%s: %d items fetched", name, len(result))
            all_items.extend(result)

        return all_items
