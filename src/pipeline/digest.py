"""Digest formatting: turn scored ContentItems into HTML-formatted messages.

Compact single-line layout. Each item is a clickable title with a short
metadata tag (source + engagement). Sections are capped at
DigestConfig.section_item_cap items.
"""

import html
import os
import re
from datetime import datetime
from typing import List, Set

from ..config import DigestConfig
from ..models import ContentItem
from ..utils import format_count, get_display_host

TELEGRAM_MAX_LENGTH = 4096

SHOW_SCORES = os.getenv("SHOW_SCORES", "0").lower() in ("1", "true")

# Strips "May 13, 2026 Announcements " and "May 13, 2026 " prefixes that
# leak from Anthropic news-page aria-labels.
_ANTHROPIC_DATE_PREFIX = re.compile(
    r"^[A-Z][a-z]+ \d{1,2},?\s+\d{4}\s+(?:Announcements\s+)?"
)


# --- Helpers ---------------------------------------------------------------

def _is_agent_item(item: ContentItem, agent_topics: Set[str]) -> bool:
    if not item.source_type.startswith("github"):
        return False
    topics = set(item.metadata.get("github_topics", []) or [])
    return bool(topics & agent_topics)


def _score_suffix(item: ContentItem) -> str:
    if not SHOW_SCORES:
        return ""
    b = item.score_breakdown or {}
    return (
        f"  <code>[k:{b.get('keywords', 0)} e:{b.get('engagement', 0)} "
        f"s:{b.get('source', 0)} c:{b.get('cross_source', 0)} "
        f"r:{b.get('recency', 0)} = {item.score:g}]</code>"
    )


def _clean_title(item: ContentItem) -> str:
    """Strip source-name prefixes the scrapers prepend, plus Anthropic date leaks."""
    t = item.title
    prefix = f"{item.source_name}: "
    if t.startswith(prefix):
        t = t[len(prefix):]
    # Hacker News scraper uses a hardcoded "Hacker News: " prefix.
    if t.startswith("Hacker News: "):
        t = t[len("Hacker News: "):]
    if item.source_type == "official_anthropic":
        t = _ANTHROPIC_DATE_PREFIX.sub("", t)
    return t.strip()


def _truncate(text: str, limit: int = 90) -> str:
    if not text or len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    space = cut.rfind(" ")
    if space > 40:
        cut = cut[:space]
    return cut + "…"


# --- Per-source-type line rendering ---------------------------------------

def _line(url: str, title_html: str, tag: str, item: ContentItem) -> str:
    body = f"• <a href='{url}'>{title_html}</a>"
    if tag:
        body += f" — {tag}"
    return body + _score_suffix(item)


def _render_anthropic(item: ContentItem) -> str:
    title = html.escape(_clean_title(item))
    return _line(item.url, title, "", item)


def _render_hn(item: ContentItem) -> str:
    title = html.escape(_clean_title(item))
    host = get_display_host(item.url)
    is_discussion = host == "news.ycombinator.com" or not host
    src = "HN" if is_discussion else host
    descendants = item.metadata.get("hn_descendants") or 0
    tag = f"{src} 💬{format_count(descendants)}" if descendants else src
    return _line(item.url, title, tag, item)


def _render_gh_release(item: ContentItem) -> str:
    title = html.escape(_clean_title(item))
    return _line(item.url, title, "release", item)


def _render_gh_repo(item: ContentItem) -> str:
    # github_search and github_event: title is repo path or an event sentence.
    title = html.escape(_clean_title(item))
    stars = item.metadata.get("github_stars") or item.metadata.get("stars")
    star_str = f"⭐{format_count(stars)}" if stars else ""
    desc = _truncate(item.summary or "", 90)
    desc_html = html.escape(desc)
    if star_str and desc_html:
        tag = f"{star_str} — {desc_html}"
    elif star_str:
        tag = star_str
    elif desc_html:
        tag = desc_html
    else:
        tag = "GitHub"
    return _line(item.url, title, tag, item)


def _render_reddit(item: ContentItem) -> str:
    title = html.escape(_clean_title(item))
    sub = item.metadata.get("subreddit", "")
    score = item.metadata.get("reddit_score") or 0
    tag = f"r/{sub} ⬆{format_count(score)}" if score else f"r/{sub}"
    return _line(item.url, title, tag, item)


def _render_telegram(item: ContentItem) -> str:
    title = html.escape(_clean_title(item))
    return _line(item.url, title, html.escape(item.source_name), item)


def _render_rss(item: ContentItem) -> str:
    title = html.escape(_clean_title(item))
    return _line(item.url, title, html.escape(item.source_name), item)


def _render_line(item: ContentItem) -> str:
    st = item.source_type
    if st == "official_anthropic":
        return _render_anthropic(item)
    if st == "hackernews":
        return _render_hn(item)
    if st == "github_release":
        return _render_gh_release(item)
    if st.startswith("github"):
        return _render_gh_repo(item)
    if st == "reddit":
        return _render_reddit(item)
    if st == "telegram":
        return _render_telegram(item)
    return _render_rss(item)


# --- Main entry ------------------------------------------------------------

def format_digest_message(scored_items: List[ContentItem], cfg: DigestConfig) -> List[str]:
    """Format scored items into one (rarely two) HTML messages.

    Compact layout: one line per item. Each section is capped at
    cfg.section_item_cap items, sorted by score desc within section.
    """
    now = datetime.now().strftime("%d/%m/%Y")
    labels = cfg.section_labels
    footer_lines = list(cfg.footer_lines)
    agent_topics = set(cfg.agent_topics)
    cap = max(1, cfg.section_item_cap)

    official_items: List[ContentItem] = []
    agent_items: List[ContentItem] = []
    big_releases: List[ContentItem] = []
    github_items: List[ContentItem] = []
    flash_news: List[ContentItem] = []

    for item in scored_items:
        if item.source_type == "official_anthropic":
            official_items.append(item)
            continue
        if _is_agent_item(item, agent_topics):
            agent_items.append(item)
            continue
        if item.source_type.startswith("github"):
            github_items.append(item)
            continue
        kw = item.score_breakdown.get("keywords", 0)
        src = item.score_breakdown.get("source", 0)
        if item.score >= 5 and (kw >= 4 or src >= 4):
            big_releases.append(item)
        else:
            flash_news.append(item)

    def _top(items: List[ContentItem]) -> List[ContentItem]:
        return sorted(items, key=lambda i: i.score, reverse=True)[:cap]

    sections = [
        ("🅰️", labels["official"],     _top(official_items)),
        ("🚀", labels["big_releases"],  _top(big_releases)),
        ("🤖", labels["agents"],        _top(agent_items)),
        ("🔥", labels["github"],        _top(github_items)),
        ("⚡", labels["flash"],         _top(flash_news)),
    ]

    if not any(items for _, _, items in sections):
        empty = (
            f"📰 <b>{labels['title']}</b> — {now}\n\n"
            f"{labels['empty']} 🤷\n\n"
            + "\n".join(footer_lines)
        )
        return [empty]

    lines: List[str] = []
    lines.append(f"📰 <b>{labels['title']}</b> — {now}")
    lines.append("")

    for emoji, header, items in sections:
        if not items:
            continue
        lines.append(f"{emoji} <b>{header}</b>")
        for item in items:
            lines.append(_render_line(item))
        lines.append("")

    # Trim trailing blank, then append footer.
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    lines.extend(footer_lines)

    full_message = "\n".join(lines)
    if len(full_message) <= TELEGRAM_MAX_LENGTH:
        return [full_message]

    # Split (rare with capped sections, but defensive).
    messages: List[str] = []
    current_msg = ""
    part_num = 1
    for line in lines:
        test_line = line + "\n"
        if len(current_msg) + len(test_line) > TELEGRAM_MAX_LENGTH - 50:
            messages.append(current_msg.strip())
            current_msg = f"📰 <b>{labels['continued']} ({part_num + 1})</b>\n\n"
            part_num += 1
        current_msg += test_line
    if current_msg.strip():
        messages.append(current_msg.strip())

    print(f"Digest split into {len(messages)} messages due to length")
    return messages
