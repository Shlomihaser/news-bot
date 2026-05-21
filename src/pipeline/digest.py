"""Digest formatting: turn scored ContentItems into HTML-formatted messages.

Pure formatting logic — no I/O, no Telegram. The output is a list of strings,
each ready to be sent as a single Telegram message (already split to respect
the 4096-character limit).
"""

import html
import os
from collections import defaultdict
from datetime import datetime
from typing import List, Set

from ..config import DigestConfig
from ..models import ContentItem
from ..utils import format_count, get_display_host, get_source_emoji

TELEGRAM_MAX_LENGTH = 4096

SHOW_SCORES = os.getenv("SHOW_SCORES", "0").lower() in ("1", "true")


def _is_agent_item(item: ContentItem, agent_topics: Set[str]) -> bool:
    if not item.source_type.startswith("github"):
        return False
    topics = set(item.metadata.get("github_topics", []) or [])
    return bool(topics & agent_topics)


def _group_by_source(items: List[ContentItem]):
    groups: dict = defaultdict(list)
    for it in items:
        groups[it.source_name].append(it)
    for name in groups:
        groups[name].sort(key=lambda i: i.score, reverse=True)
    return sorted(groups.items(), key=lambda kv: kv[1][0].score, reverse=True)


def _score_suffix(item: ContentItem) -> str:
    if not SHOW_SCORES:
        return ""
    b = item.score_breakdown or {}
    return (
        f"  <code>[k:{b.get('keywords', 0)} e:{b.get('engagement', 0)} "
        f"s:{b.get('source', 0)} c:{b.get('cross_source', 0)} "
        f"r:{b.get('recency', 0)} = {item.score:g}]</code>"
    )


def _render_hn_item(item: ContentItem) -> List[str]:
    host = get_display_host(item.url)
    is_discussion = host == "news.ycombinator.com" or not host
    label_emoji = "💬" if is_discussion else "📰"
    label = "HN Discussion" if is_discussion else host

    raw_title = item.title
    if raw_title.startswith("Hacker News: "):
        raw_title = raw_title[len("Hacker News: "):]
    escaped_title = html.escape(raw_title)

    descendants = item.metadata.get("hn_descendants") or 0
    comments_str = f" 💬 {format_count(descendants)}" if descendants > 0 else ""

    discussion_url = item.metadata.get("discussion_url")
    line = f"{label_emoji} <b>{html.escape(label)}: {escaped_title}</b>{comments_str}{_score_suffix(item)}"

    link_line = f"   🔗 <a href='{item.url}'>Read More</a>"
    if not is_discussion and discussion_url:
        link_line += f" · <a href='{discussion_url}'>HN</a>"

    return [line, link_line]


def _render_github_item(item: ContentItem) -> List[str]:
    emoji = get_source_emoji(item.source_name)
    repo_display = item.title.split(": ", 1)[-1] if ": " in item.title else item.title
    escaped_repo = html.escape(repo_display)
    summary = item.summary if item.summary and item.summary != "No description available." else ""
    escaped_summary = html.escape(summary)

    stars = item.metadata.get("github_stars") or item.metadata.get("stars")
    stars_str = f" ⭐{format_count(stars)}" if stars else ""

    head = f"{emoji} <b>{escaped_repo}</b>{stars_str}"
    if escaped_summary:
        head += f" — {escaped_summary}"
    head += _score_suffix(item)
    return [head, f"   🔗 <a href='{item.url}'>Read More</a>"]


def _render_generic_item(item: ContentItem, flash: bool = False) -> List[str]:
    emoji = get_source_emoji(item.source_name)
    escaped_title = html.escape(item.title)
    if flash:
        return [f"{emoji} <a href='{item.url}'>{escaped_title}</a>{_score_suffix(item)}"]
    return [
        f"{emoji} <b>{escaped_title}</b>{_score_suffix(item)}",
        f"   🔗 <a href='{item.url}'>Read More</a>",
    ]


def _render_item(item: ContentItem, flash: bool = False) -> List[str]:
    if item.source_type == "hackernews":
        return _render_hn_item(item)
    if item.source_type.startswith("github"):
        return _render_github_item(item)
    return _render_generic_item(item, flash=flash)


def format_digest_message(scored_items: List[ContentItem], cfg: DigestConfig) -> List[str]:
    """Format scored items into a digest, returned as one or more Telegram messages.

    Section headers and footer come from config. Long digests are split into
    multiple messages respecting the 4096-character Telegram limit.
    """
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    labels = cfg.section_labels
    footer_lines = list(cfg.footer_lines)
    agent_topics = set(cfg.agent_topics)

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
        is_big_release = item.score >= 5 and (kw >= 4 or src >= 4)
        if is_big_release:
            big_releases.append(item)
        else:
            flash_news.append(item)

    lines: List[str] = []
    lines.append(f"📰 <b>{labels['title']}</b>")
    lines.append(f"🗓 {now}")
    lines.append("")

    def _render_section(header: str, items: List[ContentItem], flash: bool = False) -> None:
        if not items:
            return
        lines.append(header)
        lines.append("")
        groups = _group_by_source(items)
        for idx, (source_name, group_items) in enumerate(groups):
            lines.append(f"<i>{html.escape(source_name)}</i>")
            for item in group_items:
                lines.extend(_render_item(item, flash=flash))
            if idx < len(groups) - 1 or not flash:
                lines.append("")

    _render_section(f"🅰️ <b>{labels['official']}</b>",     official_items)
    _render_section(f"🚀 <b>{labels['big_releases']}</b>",  big_releases)
    _render_section(f"🤖 <b>{labels['agents']}</b>",        agent_items)
    _render_section(f"🔥 <b>{labels['github']}</b>",        github_items)
    _render_section(f"⚡ <b>{labels['flash']}</b>",         flash_news, flash=True)

    if not (official_items or agent_items or big_releases or github_items or flash_news):
        empty = (
            f"📰 <b>{labels['title']}</b>\n🗓 {now}\n\n"
            f"{labels['empty']} 🤷\n\n"
            + "\n".join(footer_lines)
        )
        return [empty]

    lines.extend(footer_lines)

    full_message = "\n".join(lines)
    if len(full_message) <= TELEGRAM_MAX_LENGTH:
        return [full_message]

    # Split into multiple parts, preserving line boundaries.
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
