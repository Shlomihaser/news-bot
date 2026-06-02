"""Typed config loader for news-bot.

Loads `config/aggregator.yaml` into nested dataclasses. The YAML file is the
single source of truth for sources, scoring, and digest labels — every
scraper and downstream consumer receives its config from here.
"""

from __future__ import annotations

import os
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Dict, List, Optional, get_args, get_origin, get_type_hints

import yaml

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "aggregator.yaml",
)


# --- Source dataclasses ----------------------------------------------------

@dataclass
class RSSFeed:
    url: str = ""
    name: str = ""


@dataclass
class RSSConfig:
    enabled: bool = True
    feeds: List[RSSFeed] = field(default_factory=list)


@dataclass
class HNTopStories:
    fetch_n: int = 60
    min_score: int = 100


@dataclass
class HNAlgolia:
    keywords: List[str] = field(default_factory=list)
    min_points: int = 50


@dataclass
class HNConfig:
    enabled: bool = True
    top_stories: HNTopStories = field(default_factory=HNTopStories)
    algolia: HNAlgolia = field(default_factory=HNAlgolia)


@dataclass
class Subreddit:
    name: str = ""
    sort: str = "hot"
    limit: int = 20
    min_score: int = 0
    time_filter: Optional[str] = None


@dataclass
class RedditConfig:
    enabled: bool = True
    subreddits: List[Subreddit] = field(default_factory=list)


@dataclass
class GHReleaseEntry:
    owner: str = ""
    repo: str = ""
    topics: List[str] = field(default_factory=list)


@dataclass
class GHSearch:
    topics: List[str] = field(default_factory=list)
    min_stars: int = 500


@dataclass
class GitHubConfig:
    enabled: bool = True
    releases: List[GHReleaseEntry] = field(default_factory=list)
    search: GHSearch = field(default_factory=GHSearch)
    user_events: List[str] = field(default_factory=list)
    event_repo_topics: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class TelegramConfig:
    enabled: bool = False
    channels: List[str] = field(default_factory=list)
    per_channel_limit: int = 30


@dataclass
class AnthropicOfficialConfig:
    enabled: bool = True


@dataclass
class SourcesConfig:
    rss: RSSConfig = field(default_factory=RSSConfig)
    hackernews: HNConfig = field(default_factory=HNConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    anthropic_official: AnthropicOfficialConfig = field(default_factory=AnthropicOfficialConfig)


# --- Scoring + digest ------------------------------------------------------

@dataclass
class ScoringConfig:
    min_score: int = 3
    max_items: int = 30
    keywords: Dict[str, int] = field(default_factory=dict)
    negative_keywords: Dict[str, int] = field(default_factory=dict)
    source_weights: Dict[str, int] = field(default_factory=dict)
    default_source_weight: int = 1


@dataclass
class DigestConfig:
    section_labels: Dict[str, str] = field(default_factory=dict)
    footer_lines: List[str] = field(default_factory=list)
    agent_topics: List[str] = field(default_factory=list)
    section_item_cap: int = 5


@dataclass
class LLMScoringConfig:
    enabled: bool = True
    model: str = "gemini-2.0-flash"
    min_score: int = 6
    batch_size: int = 40


@dataclass
class Config:
    time_window_hours: int = 24
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    llm_scoring: LLMScoringConfig = field(default_factory=LLMScoringConfig)


# --- Generic dict → dataclass coercion -------------------------------------

def _coerce(value, ftype):
    if value is None:
        return None
    origin = get_origin(ftype)
    args = get_args(ftype)

    # Optional[X] (Union[X, None]) — recurse into the non-None arg.
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _coerce(value, non_none[0])

    if origin is list:
        inner = args[0]
        if not isinstance(value, list):
            raise TypeError(f"Expected list, got {type(value).__name__}")
        return [_coerce(v, inner) for v in value]

    if origin is dict:
        # Dicts of primitives pass through as-is. We don't recursively coerce
        # values; the YAML loader produces native types already.
        if not isinstance(value, dict):
            raise TypeError(f"Expected dict, got {type(value).__name__}")
        return value

    if is_dataclass(ftype):
        return _from_dict(ftype, value)

    # Primitives: trust the YAML loader.
    return value


def _from_dict(cls, data):
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise TypeError(f"Expected mapping for {cls.__name__}, got {type(data).__name__}")

    hints = get_type_hints(cls)
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _coerce(data[f.name], hints[f.name])
    return cls(**kwargs)


# --- Public loader ---------------------------------------------------------

_cached: Optional[Config] = None


def load_config(path: Optional[str] = None, force: bool = False) -> Config:
    """Load (and cache) the YAML config.

    `path` defaults to <repo>/config/aggregator.yaml, overridable via the
    NEWS_BOT_CONFIG environment variable. Pass `force=True` to bypass the
    cache (useful for tests).
    """
    global _cached
    if _cached is not None and not force:
        return _cached

    resolved = path or os.getenv("NEWS_BOT_CONFIG") or DEFAULT_CONFIG_PATH
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Config file not found: {resolved}")

    with open(resolved, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = _from_dict(Config, raw)
    _validate(cfg)
    _cached = cfg
    return cfg


def _validate(cfg: Config) -> None:
    """Fail fast on obvious config mistakes."""
    if cfg.time_window_hours <= 0:
        raise ValueError("time_window_hours must be positive")

    s = cfg.sources
    if s.rss.enabled and not s.rss.feeds:
        raise ValueError("sources.rss.enabled is true but feeds list is empty")
    for f in s.rss.feeds:
        if not f.url or not f.name:
            raise ValueError(f"RSS feed missing url/name: {f}")
    if s.reddit.enabled:
        for sub in s.reddit.subreddits:
            if not sub.name:
                raise ValueError("Subreddit entry missing name")
    if s.github.enabled:
        for rel in s.github.releases:
            if not rel.owner or not rel.repo:
                raise ValueError(f"GitHub release entry missing owner/repo: {rel}")
    if s.telegram.enabled and not s.telegram.channels:
        raise ValueError("sources.telegram.enabled is true but channels list is empty")

    sc = cfg.scoring
    if sc.min_score < 0 or sc.max_items <= 0:
        raise ValueError("scoring.min_score must be >=0 and max_items > 0")

    required_labels = {"title", "official", "big_releases", "agents", "github", "flash", "continued", "empty"}
    missing = required_labels - set(cfg.digest.section_labels.keys())
    if missing:
        raise ValueError(f"digest.section_labels missing keys: {sorted(missing)}")
