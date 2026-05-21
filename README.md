# news-bot

A personal AI-news aggregator that posts a daily English digest to a Telegram
channel. Fetches from RSS feeds, Hacker News, GitHub releases/trending,
Reddit, Telegram channels, and Anthropic's official news pages — then scores,
filters, and groups items into a single message.

**Everything is configured from one file: [`config/aggregator.yaml`](config/aggregator.yaml).**
Adding a feed, removing a subreddit, tweaking a keyword weight — all YAML edits,
no Python changes required.

## Credits

Based on [liadb33/AI-news-aggregator-he](https://github.com/liadb33/AI-news-aggregator-he),
itself a fork of [hrnrxb/AI-News-Aggregator-Bot](https://github.com/hrnrxb/AI-News-Aggregator-Bot).
Both MIT-licensed. This repo adds YAML-driven configuration, an English digest,
and a module reorganization.

## What it does

1. **Fetch** from every enabled source concurrently (RSS, HN, GitHub, Reddit, Telegram, Anthropic news).
2. **Dedup** against a persistent SQLite DB of previously-sent URLs, and again across sources by URL normalization.
3. **Score** by keyword matches, source weight, engagement (HN/Reddit/Telegram), and recency.
4. **Group** into sections: Official from Anthropic / Big Releases / AI Agents & New Tools / Hot on GitHub / Flash News.
5. **Send** one HTML-formatted digest message (auto-split if it exceeds Telegram's 4096-char limit).

## Layout

```
news-bot/
├── .github/workflows/
│   ├── digest.yml          # daily cron — runs the bot
│   └── keepalive.yml       # weekly heartbeat (prevents GH from disabling the cron)
├── config/
│   └── aggregator.yaml     # the only file you edit to tune the bot
├── src/
│   ├── main.py             # entry point: wires everything together
│   ├── config.py           # YAML loader + typed dataclasses
│   ├── models.py           # ContentItem (uniform shape across sources)
│   ├── utils.py            # display helpers
│   ├── pipeline/           # the data flow: fetch → dedup → score → format → deliver
│   │   ├── orchestrator.py # runs enabled scrapers concurrently
│   │   ├── dedup.py        # cross-source URL-normalization dedup
│   │   ├── scoring.py      # keyword/engagement/recency scoring
│   │   ├── digest.py       # formats scored items into HTML messages
│   │   └── notifier.py     # ships the digest to Telegram
│   ├── storage/            # persistence
│   │   └── db.py           # sent_links SQLite
│   └── scrapers/           # source-specific fetchers
│       ├── base.py
│       ├── rss.py
│       ├── hackernews.py
│       ├── github.py
│       ├── reddit.py
│       ├── telegram.py
│       └── anthropic_official.py
├── data/                   # sent_links.db lives here (gitignored)
├── tests/                  # reserved
├── .env.example
├── requirements.txt
└── LICENSE
```

## Local setup

1. Clone the repo.
2. Create and activate a virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Copy the env template and fill in your values:
   ```powershell
   copy .env.example .env
   # edit .env
   ```
5. Dry-run (prints the digest, doesn't send to Telegram):
   ```powershell
   python -m src.main --dry-run
   ```
6. Live run:
   ```powershell
   python -m src.main
   ```

## Deploy to GitHub Actions

1. Push the repo to GitHub.
2. In repo settings → **Secrets and variables → Actions**, add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`
3. In the **Actions** tab, enable workflows and click **Run workflow** on
   *AI News Aggregator Bot* to test.
4. Adjust the cron in `.github/workflows/digest.yml` to your preferred time.

## Editing the config

Open [`config/aggregator.yaml`](config/aggregator.yaml). Every section is
commented. Common edits:

- **Add an RSS feed**: append a line under `sources.rss.feeds`.
- **Disable Reddit for a week**: set `sources.reddit.enabled: false`.
- **Follow another subreddit**: add an entry under `sources.reddit.subreddits`.
- **Bump a keyword weight**: change its number under `scoring.keywords`.
- **Change a section label**: edit `digest.section_labels.<key>`.

After any edit, run `python -m src.main --dry-run` to verify it looks right
before deploying.

## License

MIT — see [LICENSE](LICENSE).
