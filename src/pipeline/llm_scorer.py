"""LLM-based relevance scoring via Google Gemini (free tier).

Activated only when GEMINI_API_KEY is set. Scores each item 1–10 against
a hardcoded interest profile and drops items below the configured threshold.
Uses gemini-2.0-flash which is free up to 1,500 requests/day — one daily
run uses exactly 1–2 requests. Falls back silently on any error.
"""

import json
import logging
import os
from typing import List

from ..models import ContentItem

logger = logging.getLogger(__name__)

_INTEREST_PROFILE = """\
You are a relevance scorer for an AI developer who wants to stay ahead of the industry.

Score 8–10 — must include:
- New AI model releases (GPT, Claude, Gemini, Llama, Mistral, DeepSeek, Grok, Phi, Qwen, etc.)
- Major announcements from OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, xAI, Cohere, etc.
- New AI tools, platforms, or developer products launching or releasing a major version
- MCP (Model Context Protocol) news, new servers, significant updates
- AI coding assistant news (Claude Code, Cursor, Copilot, Windsurf, Devin, etc.)
- Open-source AI repos going viral or shipping breaking changes
- AI agent frameworks releasing major versions (LangChain, LlamaIndex, CrewAI, pydantic-ai, smolagents, etc.)
- Research breakthroughs with clear near-term practical impact
- AI company funding rounds >$50M, acquisitions, or major strategic moves
- New AI capabilities that meaningfully change what developers can build

Score 5–7 — worth including:
- Interesting research papers with actionable takeaways
- Minor version updates to widely-used tools
- AI policy/regulation with real developer impact
- Benchmark comparisons between major models
- AI startup launches in inference, agents, or tooling

Score 1–4 — filter out:
- Beginner tutorials ("how to use ChatGPT", "AI for beginners")
- Generic listicles without real news value
- General tech news unrelated to AI
- Vague hype or speculation without concrete substance
- Entertainment AI (consumer image apps, celebrity content)
- Stock market analysis or investment advice
- Crypto/blockchain unless directly tied to AI infrastructure
"""


def _build_prompt(items: List[ContentItem]) -> str:
    entries = []
    for i, item in enumerate(items):
        title = item.title.replace("\n", " ")[:150]
        summary = (item.summary or "").replace("\n", " ")[:180]
        entries.append(
            f"[{i}] {title}\n    {summary}\n    Source: {item.source_name}"
        )
    return (
        "Score each news item 1–10 for relevance to an AI industry professional.\n\n"
        + "\n\n".join(entries)
        + "\n\nReturn ONLY a JSON array of integers, one per item, in order. Example: [9,3,7,8,2]"
    )


async def _score_batch(model, items: List[ContentItem]) -> List[int]:
    """Score one batch; returns [10]*len(items) on any failure so nothing is dropped."""
    try:
        response = await model.generate_content_async(_build_prompt(items))
        raw = response.text.strip()
        start, end = raw.find("["), raw.rfind("]") + 1
        if start == -1 or end == 0:
            logger.warning("LLM scorer: unexpected response format: %r", raw[:200])
            return [10] * len(items)
        scores = json.loads(raw[start:end])
        if len(scores) != len(items):
            logger.warning(
                "LLM scorer: got %d scores for %d items; keeping all.", len(scores), len(items)
            )
            return [10] * len(items)
        return [max(1, min(10, int(s))) for s in scores]
    except Exception as exc:
        logger.warning("LLM batch scoring failed (%s); keeping all %d items.", exc, len(items))
        return [10] * len(items)


async def llm_filter(items: List[ContentItem], cfg) -> List[ContentItem]:
    """Drop low-relevance items using Gemini Flash (free tier).

    Returns items unchanged when:
    - cfg.enabled is False
    - GEMINI_API_KEY env var is not set
    - google-generativeai package is not installed
    - any API error occurs
    """
    if not getattr(cfg, "enabled", True):
        return items

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set; skipping LLM scoring.")
        return items

    if not items:
        return items

    try:
        import google.generativeai as genai
    except ImportError:
        logger.warning("google-generativeai not installed; skipping LLM scoring.")
        return items

    model_name = getattr(cfg, "model", "gemini-2.0-flash")
    min_score = getattr(cfg, "min_score", 6)
    batch_size = getattr(cfg, "batch_size", 40)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=_INTEREST_PROFILE,
    )

    all_scores: List[int] = []
    for start in range(0, len(items), batch_size):
        batch = items[start: start + batch_size]
        scores = await _score_batch(model, batch)
        all_scores.extend(scores)

    if len(all_scores) != len(items):
        logger.warning("LLM scorer: score count mismatch; skipping filter.")
        return items

    kept, dropped = [], 0
    for item, score in zip(items, all_scores):
        if score >= min_score:
            item.score += score * 0.5  # small boost so LLM-approved items rank higher
            kept.append(item)
        else:
            dropped += 1
            logger.debug("LLM filtered (score=%d): %s", score, item.title)

    print(
        f"LLM scoring ({model_name}): {len(items)} items → {len(kept)} kept, "
        f"{dropped} filtered (threshold ≥{min_score})"
    )
    return kept
