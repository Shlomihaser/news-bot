"""Multi-signal scoring for ContentItem objects.

Combines six signals into a single score:
1. Keyword matches (word-boundary regex)
2. Negative-keyword penalties (sponsored, casino, etc.)
3. Engagement (HN score/comments, Reddit score/upvote ratio, Telegram views)
4. Source weight (high-signal blogs > generic feeds)
5. Cross-source bonus (item appears in >= 2 sources)
6. Recency bonus (< 6h or < 12h old)

Keyword lists, weights, and thresholds all come from config (see
config/aggregator.yaml).
"""

import re
from datetime import datetime, timezone
from typing import Dict, List, Pattern, Tuple

from .config import ScoringConfig
from .models import ContentItem


def _compile_patterns(weights: Dict[str, int]) -> List[Tuple[Pattern, int]]:
    return [
        (re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE), weight)
        for kw, weight in weights.items()
    ]


def _score_item(item: ContentItem, cfg: ScoringConfig,
                kw_patterns: List[Tuple[Pattern, int]],
                neg_patterns: List[Tuple[Pattern, int]]) -> float:
    """Compute multi-signal score for `item`, mutating its score / breakdown."""
    breakdown = {}

    text = f"{item.title} {item.summary}"

    # 1. Keyword score.
    kw_score = sum(weight for pat, weight in kw_patterns if pat.search(text))
    breakdown["keywords"] = kw_score

    # 2. Negative keyword penalty.
    neg_score = sum(weight for pat, weight in neg_patterns if pat.search(text))
    breakdown["negative"] = neg_score

    # 3. Engagement bonus.
    eng = 0.0
    meta = item.metadata
    if "hn_score" in meta and meta["hn_score"] is not None:
        eng += float(meta["hn_score"]) / 100.0
    if "hn_descendants" in meta and meta["hn_descendants"] is not None:
        eng += float(meta["hn_descendants"]) / 50.0
    if "reddit_score" in meta and meta["reddit_score"] is not None:
        eng += float(meta["reddit_score"]) / 100.0
    if meta.get("reddit_upvote_ratio") is not None and meta["reddit_upvote_ratio"] >= 0.9:
        eng += 1.0
    if meta.get("telegram_views") is not None:
        eng += float(meta["telegram_views"]) / 5000.0
    breakdown["engagement"] = round(eng, 2)

    # 4. Source weight (source_name first, then source_type, then default).
    src_weight = cfg.source_weights.get(item.source_name)
    if src_weight is None:
        src_weight = cfg.source_weights.get(item.source_type, cfg.default_source_weight)
    breakdown["source"] = src_weight

    # 5. Cross-source bonus.
    cross = 5 if len(item.merged_sources) >= 2 else 0
    breakdown["cross_source"] = cross

    # 6. Recency bonus.
    recency = 0
    if item.published_at is not None:
        published = item.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        hours_old = (datetime.now(tz=timezone.utc) - published).total_seconds() / 3600.0
        if hours_old < 6:
            recency = 2
        elif hours_old < 12:
            recency = 1
    breakdown["recency"] = recency

    total = kw_score + neg_score + eng + src_weight + cross + recency
    item.score = float(total)
    item.score_breakdown = breakdown
    return total


def filter_and_rank(items: List[ContentItem], cfg: ScoringConfig) -> List[ContentItem]:
    """Score, filter, sort descending, cap at max_items.

    Items with source_type == "official_anthropic" bypass the min-score
    filter and the max_items cap, and are returned at the front.
    """
    kw_patterns = _compile_patterns(cfg.keywords)
    neg_patterns = _compile_patterns(cfg.negative_keywords)

    official: List[ContentItem] = []
    kept: List[ContentItem] = []
    for item in items:
        s = _score_item(item, cfg, kw_patterns, neg_patterns)
        if item.source_type == "official_anthropic":
            official.append(item)
            continue
        if s >= cfg.min_score:
            kept.append(item)
        else:
            print(f"FILTERED OUT (score={s:.2f}): {item.title}")

    kept.sort(key=lambda i: i.score, reverse=True)
    result = official + kept[: cfg.max_items]

    print(
        f"\nScoring complete: {len(items)} collected → {len(official)} official + "
        f"{len(kept)} passed filter → {len(result)} in digest"
    )
    return result
