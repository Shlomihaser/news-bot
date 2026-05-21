"""GitHub scraper: releases on a watchlist, stars-velocity search, and
release/tag events for a watchlist of users.

Replaces the old `https://github.com/trending/<lang>` HTML scraper.
Uses the REST API with the auto-injected GITHUB_TOKEN where available.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from .base import BaseScraper
from ..models import ContentItem

logger = logging.getLogger(__name__)


GITHUB_API_BASE = "https://api.github.com"


def clean_release_body(body: str) -> str:
    """Clean GitHub release body markdown into readable plain prose."""
    if not body:
        return ""
    text = re.sub(r"<[^>]+>", "", body)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)

    drop_prefixes = (
        "co-authored-by:",
        "signed-off-by:",
        "reviewed-by:",
        "fixes:",
        "closes #",
        "see:",
        "full changelog",
    )
    cleaned_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if any(low.startswith(p) for p in drop_prefixes):
            continue
        if re.match(r"^[a-f0-9]{7,40}$", s):
            continue
        cleaned_lines.append(s)
    text = " ".join(cleaned_lines)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > 220:
        cut = text[:220]
        space = cut.rfind(" ")
        if space > 100:
            cut = cut[:space]
        text = cut + "…"
    return text

class GitHubScraper(BaseScraper):
    """Async scraper for GitHub releases, search, and user events.

    All watchlists and thresholds come from config.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        release_watchlist: Optional[List[Tuple[str, str, List[str]]]] = None,
        search_topics: Optional[List[str]] = None,
        search_min_stars: int = 500,
        event_users: Optional[List[str]] = None,
        event_repo_topics: Optional[Dict[str, List[str]]] = None,
    ):
        super().__init__(http_client)
        self.release_watchlist = list(release_watchlist or [])
        self.search_topics = list(search_topics or [])
        self.search_min_stars = search_min_stars
        self.event_users = list(event_users or [])
        self.event_repo_topics = dict(event_repo_topics or {})
        self.token = os.getenv("GITHUB_TOKEN")
        # GitHub limits us to 60 unauthenticated / 5000 authenticated req/h.
        # A semaphore of 4 is friendly even for the unauth case across the
        # ~30 endpoints this scraper hits.
        self._semaphore = asyncio.Semaphore(4)

    def _headers(self) -> dict:
        h = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-news-aggregator-he",
        }
        if self.token:
            h["Authorization"] = f"token {self.token}"
        return h

    async def fetch(self, since: datetime) -> List[ContentItem]:
        tasks = []
        for entry in self.release_watchlist:
            owner, repo, topics = entry[0], entry[1], (entry[2] if len(entry) > 2 else [])
            tasks.append(self._fetch_releases(owner, repo, since, topics))
        for topic in self.search_topics:
            tasks.append(self._search_topic(topic, since))
        for username in self.event_users:
            tasks.append(self._fetch_user_events(username, since))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: List[ContentItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("GitHub sub-fetch raised: %s", result)
                continue
            items.extend(result)
        logger.info("GitHub scraper produced %d items", len(items))
        return items

    # --- Releases -----------------------------------------------------

    async def _fetch_releases(self, owner: str, repo: str, since: datetime, topics: Optional[List[str]] = None) -> List[ContentItem]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases"
        async with self._semaphore:
            try:
                response = await self.client.get(
                    url,
                    headers=self._headers(),
                    params={"per_page": 5},
                    follow_redirects=True,
                    timeout=30.0,
                )
                response.raise_for_status()
                releases = response.json()
            except httpx.HTTPError as e:
                logger.warning("GitHub releases fetch failed for %s/%s: %s", owner, repo, e)
                return []

        items: List[ContentItem] = []
        for release in releases:
            published_raw = release.get("published_at") or release.get("created_at")
            if not published_raw:
                continue
            try:
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if published_at < since:
                continue

            tag = release.get("tag_name", "")
            html_url = release.get("html_url") or f"https://github.com/{owner}/{repo}/releases"
            summary = clean_release_body(release.get("body") or "")
            source_label = f"GitHub Release: {repo}"
            items.append(ContentItem(
                id=self._make_id("github_release", f"{owner}_{repo}", str(release.get("id", tag))),
                source_type="github_release",
                source_name=source_label,
                title=f"{owner}/{repo} {tag}".strip(),
                url=html_url,
                summary=summary,
                author=(release.get("author") or {}).get("login"),
                published_at=published_at,
                metadata={
                    "repo": f"{owner}/{repo}",
                    "tag": tag,
                    "prerelease": release.get("prerelease", False),
                    "github_topics": list(topics or []),
                },
            ))
        return items

    # --- Stars-velocity search ---------------------------------------

    async def _search_topic(self, topic: str, since: datetime) -> List[ContentItem]:
        # Yesterday in UTC, formatted YYYY-MM-DD.
        yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        q = f"topic:{topic} stars:>{self.search_min_stars} pushed:>{yesterday}"
        url = f"{GITHUB_API_BASE}/search/repositories"
        params = {"q": q, "sort": "updated", "order": "desc", "per_page": 10}

        async with self._semaphore:
            try:
                response = await self.client.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    follow_redirects=True,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.warning("GitHub search failed for topic %s: %s", topic, e)
                return []

        items: List[ContentItem] = []
        for repo in data.get("items", []):
            full_name = repo.get("full_name", "")
            html_url = repo.get("html_url", f"https://github.com/{full_name}")
            description = (repo.get("description") or "").strip()
            stars = repo.get("stargazers_count", 0)
            updated_raw = repo.get("pushed_at") or repo.get("updated_at")
            published_at = None
            if updated_raw:
                try:
                    published_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                except ValueError:
                    pass
            repo_topics = repo.get("topics") or []
            items.append(ContentItem(
                id=self._make_id("github_search", topic, str(repo.get("id", full_name))),
                source_type="github_search",
                source_name=f"GitHub Trending ({topic})",
                title=full_name,
                url=html_url,
                summary=description[:300],
                author=(repo.get("owner") or {}).get("login"),
                published_at=published_at,
                metadata={
                    "repo": full_name,
                    "stars": stars,
                    "github_stars": stars,
                    "topic": topic,
                    "github_topics": list(repo_topics),
                },
            ))
        return items

    # --- User events --------------------------------------------------

    async def _fetch_user_events(self, username: str, since: datetime) -> List[ContentItem]:
        url = f"{GITHUB_API_BASE}/users/{username}/events/public"
        async with self._semaphore:
            try:
                response = await self.client.get(
                    url,
                    headers=self._headers(),
                    params={"per_page": 30},
                    follow_redirects=True,
                    timeout=30.0,
                )
                response.raise_for_status()
                events = response.json()
            except httpx.HTTPError as e:
                logger.warning("GitHub events fetch failed for %s: %s", username, e)
                return []

        items: List[ContentItem] = []
        for event in events:
            etype = event.get("type")
            if etype == "ReleaseEvent":
                pass
            elif etype == "CreateEvent" and (event.get("payload") or {}).get("ref_type") == "tag":
                pass
            else:
                continue

            try:
                created_at = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if created_at < since:
                continue

            item = self._parse_event(event, username, created_at)
            if item:
                items.append(item)
        return items

    def _parse_event(self, event: dict, username: str, created_at: datetime) -> Optional[ContentItem]:
        etype = event["type"]
        event_id = str(event.get("id", ""))
        repo_name = (event.get("repo") or {}).get("name", "")
        repo_url = f"https://github.com/{repo_name}" if repo_name else "https://github.com"

        if etype == "ReleaseEvent":
            release = (event.get("payload") or {}).get("release") or {}
            tag = release.get("tag_name", "")
            title = f"{username} released {tag} in {repo_name}".strip()
            summary = clean_release_body(release.get("body") or "")
            url = release.get("html_url") or repo_url
            source_label = f"GitHub: @{username}"
        elif etype == "CreateEvent":
            payload = event.get("payload") or {}
            tag = payload.get("ref", "")
            title = f"{username} tagged {tag} in {repo_name}".strip()
            summary = (payload.get("description") or "").strip()[:300]
            url = repo_url
            source_label = f"GitHub: @{username}"
        else:
            return None

        return ContentItem(
            id=self._make_id("github_event", username, event_id),
            source_type="github_event",
            source_name=source_label,
            title=title,
            url=url,
            summary=summary,
            author=username,
            published_at=created_at,
            metadata={
                "event_type": etype,
                "repo": repo_name,
                "github_topics": list(self.event_repo_topics.get(repo_name, [])),
            },
        )
