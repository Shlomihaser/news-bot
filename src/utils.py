"""Shared helpers (emoji map for digest formatting)."""

from urllib.parse import urlparse


def get_display_host(url: str) -> str:
    """Return display-friendly hostname, e.g. 'techcrunch.com'."""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def format_count(n) -> str:
    """1234 -> '1.2k', 999 -> '999'."""
    if n is None:
        return ""
    if n < 1000:
        return str(n)
    return f"{n/1000:.1f}k".replace(".0k", "k")


def get_source_emoji(source_name: str) -> str:
    """Return an emoji for a given source's human label.

    Falls back to a generic sparkle when no match is found.
    """
    emoji_map = {
        # RSS feeds
        "Hugging Face Blog": "🤗",
        "Hugging Face Paper": "📝",
        "ML Reddit": "🤖",
        "OpenAI Blog": "✨",
        "The Gradient": "📜",
        "Jay Alammar": "💡",
        "DeepMind Blog": "🔬",
        "AI From MIT News": "🎓",
        "General News From MIT News": "🏛️",
        "Microsoft AI Blog": "💻",
        "machinelearningmastery Blog": "👨‍🏫",
        "Nvidia AI Blog": "🚀",
        "Towards Data Science": "📊",
        "The Verge": "🟣",
        "Simon Willison": "🧪",
        "Anthropic Blog": "🅰️",
        "Anthropic News": "📣",
        "Claude Blog": "🅰️",
        "Import AI": "📰",
        "Lilian Weng": "🦉",
        "Sebastian Raschka": "📚",
        "Latent Space": "🛰️",

        # APIs
        "Hacker News": "🧑‍💻",
    }
    if source_name in emoji_map:
        return emoji_map[source_name]

    # Source-type prefixes (for dynamically generated labels).
    if source_name.startswith("GitHub Release:"):
        return "📦"
    if source_name.startswith("GitHub Trending"):
        return "⭐"
    if source_name.startswith("GitHub: @"):
        return "🐙"
    if source_name.startswith("r/"):
        return "🤖"
    if source_name.startswith("Telegram:") or source_name.startswith("@"):
        return "📡"

    return "✨"
