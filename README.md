# news-bot

A personal AI-news aggregator that posts a daily digest to a Telegram channel.
Pulls from 26 RSS feeds, Hacker News, 30 GitHub repos, 9 subreddits, and
Anthropic's official news page — then scores, LLM-filters, and groups everything
into a single focused message.

**Everything is configured from one file: [`config/aggregator.yaml`](config/aggregator.yaml).**
Adding a feed, removing a subreddit, tweaking a keyword weight — all YAML edits, no code changes needed.

## What it does

1. **Fetch** from every enabled source concurrently (RSS, HN, GitHub, Reddit, Anthropic news).
2. **Dedup** against a persistent SQLite DB of previously-sent URLs, and across sources by URL normalization.
3. **Keyword-score** by keyword matches, source trust, engagement (HN/Reddit), cross-source mentions, and recency.
4. **LLM-filter** using Gemini Flash (free tier) — each item is scored 1–10 for relevance and low-signal items are dropped.
5. **Group** into sections: Anthropic / Big Releases & Announcements / AI Agents, Tools & MCP / Hot on GitHub / Industry Updates.
6. **Send** one HTML-formatted Telegram message (auto-split if needed).

## Sources covered

| Category | Sources |
|---|---|
| AI labs | Anthropic, OpenAI, Google DeepMind, Meta AI, Mistral AI, Google AI, Microsoft AI, Nvidia |
| Research | Hugging Face Blog & Papers, Lilian Weng, Sebastian Raschka (Ahead of AI), Interconnects, The Batch, The Gradient, Jay Alammar, Latent Space, Import AI, Papers with Code, MIT AI News |
| Industry news | VentureBeat AI, TechCrunch AI, Ars Technica AI, The Verge AI, TLDR AI |
| Hacker News | Top stories (min 100pts) + Algolia keyword search (14 keywords) |
| Reddit | r/LocalLLaMA, r/MachineLearning, r/ClaudeAI, r/OpenAI, r/singularity, r/LocalLLM, r/artificial, r/ChatGPT, r/Anthropic |
| GitHub | 30 watched repos (releases) + topic search + events from 6 developers |

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
│   ├── pipeline/
│   │   ├── orchestrator.py # runs enabled scrapers concurrently
│   │   ├── dedup.py        # cross-source URL-normalization dedup
│   │   ├── scoring.py      # keyword/engagement/recency scoring
│   │   ├── llm_scorer.py   # Gemini-based relevance filter (free tier)
│   │   ├── digest.py       # formats scored items into HTML messages
│   │   └── notifier.py     # ships the digest to Telegram
│   ├── storage/
│   │   └── db.py           # sent_links SQLite
│   └── scrapers/
│       ├── base.py
│       ├── rss.py
│       ├── hackernews.py
│       ├── github.py
│       ├── reddit.py
│       ├── telegram.py
│       └── anthropic_official.py
├── data/                   # sent_links.db lives here (gitignored)
├── tests/
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

## Deploy to GitHub Actions

1. Push the repo to GitHub.
2. In repo settings → **Secrets and variables → Actions**, add:
   | Secret | Where to get it |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
   | `TELEGRAM_CHANNEL_ID` | your channel's `@handle` or numeric ID |
   | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) → Get API key (free) |
3. In the **Actions** tab, enable workflows and click **Run workflow** on *AI News Aggregator Bot* to test.
4. Adjust the cron in `.github/workflows/digest.yml` if needed (default: 03:00 UTC daily).

> **LLM filtering is optional.** If `GEMINI_API_KEY` is not set the bot skips the Gemini step and falls back to keyword scoring only. Gemini Flash free tier allows 1,500 requests/day — the bot uses 1–2 per run.

## Tuning the config

Open [`config/aggregator.yaml`](config/aggregator.yaml). Common edits:

- **Add an RSS feed** — append a line under `sources.rss.feeds`
- **Follow a subreddit** — add an entry under `sources.reddit.subreddits`
- **Watch a GitHub repo** — add an entry under `sources.github.releases`
- **Boost a keyword** — raise its number under `scoring.keywords`
- **Tighten LLM filtering** — raise `llm_scoring.min_score` (default: 6, max: 10)
- **Disable LLM filtering** — set `llm_scoring.enabled: false`

After any edit, run `python -m src.main --dry-run` to preview the digest locally.

## License

MIT — see [LICENSE](LICENSE).
