"""Telegram delivery.

Sends pre-formatted digest messages to a Telegram channel, handling flood
control and transient timeouts. Saves URLs to the sent_links DB only after
every part has been sent successfully.
"""

import asyncio
import os
from typing import List, Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut

from .db import save_sent_link
from .models import ContentItem


def _bot_from_env() -> Optional[Bot]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    return Bot(token=token)


async def send_digest(
    digest_messages: List[str],
    scored_items: List[ContentItem],
    dry_run: bool = False,
) -> None:
    """Send each pre-formatted message to the Telegram channel.

    Saves the URL of every scored item to the sent_links DB after the last
    message is sent. In dry-run mode prints to stdout and skips the DB write.
    """
    print("\n--- Starting to send digest to Telegram ---")
    if not digest_messages:
        print("No messages to send.")
        return

    if dry_run:
        print("\n--- DRY RUN: digest not sent to Telegram. Output: ---\n")
        for i, msg in enumerate(digest_messages, 1):
            print(f"--- Part {i}/{len(digest_messages)} ---")
            print(msg)
            print()
        return

    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    bot = _bot_from_env()
    if bot is None or not channel_id:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set; cannot send.")
        return

    for i, msg in enumerate(digest_messages):
        try:
            await bot.send_message(
                chat_id=channel_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            print(f"SUCCESS: Digest part {i + 1}/{len(digest_messages)} sent to Telegram.")
            if i < len(digest_messages) - 1:
                await asyncio.sleep(3)
        except RetryAfter as e:
            wait_time = e.retry_after + 1
            print(f"Telegram Flood Control: waiting {wait_time}s...")
            await asyncio.sleep(wait_time)
            try:
                await bot.send_message(
                    chat_id=channel_id,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                print(f"SUCCESS (retry): Digest part {i + 1} sent after waiting.")
            except Exception as retry_e:
                print(f"ERROR (retry failed): Could not send digest part {i + 1}: {retry_e}")
                return
        except TimedOut:
            print("TIMEOUT: Telegram API timed out. Retrying in 5 seconds.")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"ERROR: Could not send digest to Telegram: {e}")
            return

    for item in scored_items:
        save_sent_link(item.url)
    print(f"Saved {len(scored_items)} links to database.")
    print("--- Finished sending digest to Telegram ---")
