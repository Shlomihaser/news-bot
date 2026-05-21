"""URL-normalization-based dedup across sources.

Two ContentItems with the same canonical URL but different sources (e.g.
HN linking to a blog also covered by RSS) are merged into a single item.
The richest summary wins; metadata is union-merged; merged_sources is
populated for the cross-source scoring bonus.
"""

from typing import List
from urllib.parse import urlparse

from ..models import ContentItem


def normalize_url(url: str) -> str:
    """Canonicalize for cross-source comparison.

    Strips scheme, leading "www.", and trailing slashes. Query string and
    fragment are dropped — typical sources differ only on UTM params.
    """
    try:
        parsed = urlparse(url or "")
    except (ValueError, TypeError):
        return url or ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"


def merge_cross_source_duplicates(items: List[ContentItem]) -> List[ContentItem]:
    """Group by normalized URL; collapse duplicates into one ContentItem."""
    groups: dict = {}
    for item in items:
        key = normalize_url(item.url)
        groups.setdefault(key, []).append(item)

    merged: List[ContentItem] = []
    for _, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Pick the entry with the richest summary as primary.
        primary = max(group, key=lambda i: len(i.summary or ""))
        primary.merged_sources = sorted({i.source_type for i in group})

        # Union-merge metadata: don't overwrite existing values on primary.
        for other in group:
            if other is primary:
                continue
            for k, v in other.metadata.items():
                primary.metadata.setdefault(k, v)

        merged.append(primary)
    return merged
