"""news-bot entry point.

Reads YAML config, fetches from every enabled source, dedups, scores,
formats, and ships the digest to Telegram.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List

# Force UTF-8 on stdout/stderr so emoji + arrow characters don't break on
# Windows consoles that default to cp1252.
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass

# Load env vars from .env when running locally.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Support both `python src/main.py` and `python -m src.main`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.config import load_config
    from src.db import initialize_db, load_sent_links
    from src.dedup import merge_cross_source_duplicates
    from src.digest import format_digest_message
    from src.models import ContentItem
    from src.notifier import send_digest
    from src.orchestrator import fetch_all_sources
    from src.scoring import filter_and_rank
else:
    from .config import load_config
    from .db import initialize_db, load_sent_links
    from .dedup import merge_cross_source_duplicates
    from .digest import format_digest_message
    from .models import ContentItem
    from .notifier import send_digest
    from .orchestrator import fetch_all_sources
    from .scoring import filter_and_rank


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _log_per_source_counts(label: str, items: List[ContentItem]) -> None:
    counts: dict = {}
    for it in items:
        counts[it.source_type] = counts.get(it.source_type, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(empty)"
    print(f"Per-source counts {label} (total {len(items)}): {summary}")


async def main_bot_run() -> None:
    start_time = time.time()
    print(f"\n--- Bot run started at: {time.ctime()} ---")

    cfg = load_config()
    print(f"Loaded config: time_window={cfg.time_window_hours}h, "
          f"min_score={cfg.scoring.min_score}, max_items={cfg.scoring.max_items}")

    initialize_db()
    sent_links = load_sent_links()
    print(f"Previously sent links loaded from DB: {len(sent_links)}")

    since = datetime.now(tz=timezone.utc) - timedelta(hours=cfg.time_window_hours)
    print(f"Time window: items published after {since.isoformat()}")

    print("\n--- News Collection ---")
    items = await fetch_all_sources(cfg, since)
    _log_per_source_counts("after fetch", items)

    # Backup dedup against sent_links DB.
    fresh = [item for item in items if item.url not in sent_links]
    if len(fresh) != len(items):
        print(f"Dropped {len(items) - len(fresh)} items already in sent_links DB.")

    # Cross-source URL-normalization dedup.
    fresh = merge_cross_source_duplicates(fresh)
    _log_per_source_counts("after dedup", fresh)

    print("\n--- Scoring and Filtering ---")
    scored = filter_and_rank(fresh, cfg.scoring)
    _log_per_source_counts("in digest", scored)

    digest_messages = format_digest_message(scored, cfg.digest)
    dry_run = os.getenv("DRY_RUN", "0") == "1"
    await send_digest(digest_messages, scored, dry_run=dry_run)

    duration = time.time() - start_time
    print(f"--- Bot run finished at: {time.ctime()} ({duration:.2f}s) ---")


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        os.environ["DRY_RUN"] = "1"
    asyncio.run(main_bot_run())
