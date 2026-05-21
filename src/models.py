"""Unified content model for all scrapers and downstream consumers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ContentItem:
    """A single piece of content fetched from any source.

    All scrapers produce List[ContentItem]. Scoring, dedup, and the
    Telegram formatter all consume ContentItem.
    """

    id: str                          # "{source_type}:{subtype}:{native_id}"
    source_type: str                 # "github_release" | "github_search" | "github_event" | "hackernews" | "rss" | "reddit" | "telegram"
    source_name: str                 # human label, e.g. "GitHub Release: vllm", "r/LocalLLaMA"
    title: str
    url: str
    summary: str = ""
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    merged_sources: list = field(default_factory=list)
